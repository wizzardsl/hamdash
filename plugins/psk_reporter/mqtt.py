"""A subscribe-only MQTT 3.1.1 client, for PSK Reporter's live feed.

Spots arrive as they happen instead of being asked for every few minutes.
That is better in every direction: no retrieval limit to run into, no
`rptlimit` truncating a busy square, nothing to time out, and the card
stops being a thing that polls somebody else's server at all.

**Hand-rolled rather than `paho-mqtt`, deliberately.** Subscribing is
four packets - CONNECT, SUBSCRIBE, PING, and reading PUBLISH - and a
frozen card runs inside the hamdash binary (invariant 6), so a card's
dependency is in every build including those of people who never install
it. `plugins/spots/telnet.py` hand-rolls its protocol for the same
reason. Publishing, QoS 1 and 2, retained messages, wills and TLS are
all deliberately absent: this reads a public firehose and nothing else.

The topic tree is the useful part:

    pskr/filter/v2/{band}/{mode}/{tx}/{rx}/{tx_grid}/{rx_grid}/{tx_dxcc}/{rx_dxcc}

so "who is hearing me" and "what am I hearing" are two subscriptions
rather than a query to filter afterwards. `+` matches one level, `#` the
rest.

Imports nothing from the card, so `tests/test_psk_mqtt.py` can drive it
over a socket pair with no network.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time

HOST = "mqtt.pskreporter.info"
PORT = 1883                 # 1884 is TLS, 1885/1886 are WebSockets
KEEPALIVE = 60              # seconds; the broker drops us at 1.5x this
RECONNECT_MIN = 5.0
RECONNECT_MAX = 300.0
# Nothing at all for this long means the socket is gone rather than the
# band being quiet - the same call `spots` makes about a telnet feed.
QUIET_DEATH = 10 * 60.0

CONNECT, CONNACK, PUBLISH, SUBSCRIBE, SUBACK = 1, 2, 3, 8, 9
PINGREQ, PINGRESP, DISCONNECT = 12, 13, 14

REFUSED = {
    1: "the broker wants a different protocol version",
    2: "the broker rejected the client id",
    3: "the broker is unavailable",
    4: "bad username or password",
    5: "not authorised",
}


class ProtocolError(Exception):
    """The far end said something that is not MQTT."""


# ---- the wire format -------------------------------------------------

def _varint(n: int) -> bytes:
    """MQTT's remaining-length: 7 bits a byte, top bit means 'more'."""
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        if n:
            b |= 128
        out.append(b)
        if not n:
            return bytes(out)


def _string(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack("!H", len(raw)) + raw


def connect_packet(client_id: str, keepalive: int = KEEPALIVE) -> bytes:
    # Clean session (0x02): we want what is happening now, not a queue of
    # what happened while we were away.
    body = (_string("MQTT") + bytes([4, 0x02])
            + struct.pack("!H", keepalive) + _string(client_id))
    return bytes([CONNECT << 4]) + _varint(len(body)) + body


def subscribe_packet(topics, packet_id: int = 1) -> bytes:
    body = struct.pack("!H", packet_id)
    for t in topics:
        body += _string(t) + b"\x00"        # QoS 0: at most once, no acks
    # The 0x02 is mandatory in the fixed header of SUBSCRIBE.
    return bytes([(SUBSCRIBE << 4) | 0x02]) + _varint(len(body)) + body


PING = bytes([PINGREQ << 4, 0])


class Reader:
    """Packets out of a byte stream.

    A stream, not a datagram: one `recv` can hold half a packet or three
    of them, so this buffers rather than assuming a read is a message.
    Reading the wrong number of bytes desynchronises everything after it
    - which looks like garbled topics rather than like an error, and is
    how the first version of this was found to be wrong.
    """

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> None:
        self.buf.extend(data)

    def packets(self):
        """Every whole packet in the buffer, leaving any partial one."""
        while True:
            if len(self.buf) < 2:
                return
            n, used = 0, 1
            mult = 1
            while True:
                if used >= len(self.buf):
                    return                      # length not all here yet
                b = self.buf[used]
                n += (b & 127) * mult
                used += 1
                if not b & 128:
                    break
                mult *= 128
                if mult > 128 ** 3:
                    raise ProtocolError("remaining length is not a number")
            if len(self.buf) < used + n:
                return                          # body not all here yet
            first = self.buf[0]
            body = bytes(self.buf[used:used + n])
            del self.buf[:used + n]
            yield first >> 4, first & 0x0F, body


def publish_parts(body: bytes, flags: int):
    """Topic and payload out of a PUBLISH body.

    QoS above 0 puts a packet id between the topic and the payload. We
    subscribe at QoS 0 so there should never be one, but reading it wrong
    if there is would put two bytes of binary at the front of the JSON.
    """
    if len(body) < 2:
        raise ProtocolError("PUBLISH with no topic")
    tlen = struct.unpack("!H", body[:2])[0]
    if len(body) < 2 + tlen:
        raise ProtocolError("PUBLISH topic longer than the packet")
    topic = body[2:2 + tlen].decode("utf-8", "replace")
    rest = body[2 + tlen:]
    if (flags >> 1) & 0x03:                     # QoS 1 or 2
        rest = rest[2:]
    return topic, rest.decode("utf-8", "replace")


# ---- the client ------------------------------------------------------

class Feed:
    """One connection to the broker, reconnecting on its own.

    `on_spot(dict)` is called for every message that parses. Everything
    else - a packet that is not a PUBLISH, a payload that is not JSON, a
    topic we did not expect - is dropped rather than raised: **nothing
    that arrives on a socket may end the reader**, which is the lesson
    the SDK's bus reader already taught this tree.
    """

    def __init__(self, topics, on_spot, host: str = HOST, port: int = PORT,
                 client_id: str | None = None) -> None:
        self.topics = list(topics)
        self.on_spot = on_spot
        self.host = host
        self.port = port
        self.client_id = client_id or f"hamdash-{int(time.time() * 1000) % 10**9}"
        self.status = "idle"
        self.error = ""
        self.spots = 0
        self.connected_at = 0.0
        self.last_msg = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ----------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="psk-mqtt")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def info(self) -> dict:
        return {"status": self.status, "error": self.error,
                "spots": self.spots, "topics": self.topics,
                "connected_s": (round(time.time() - self.connected_at)
                                if self.connected_at else 0),
                "quiet_s": (round(time.time() - self.last_msg)
                            if self.last_msg else 0)}

    # -- the loop -----------------------------------------------------
    def _run(self) -> None:
        backoff = RECONNECT_MIN
        while not self._stop.is_set():
            try:
                self._session()
                backoff = RECONNECT_MIN         # a good session resets it
            except Exception as exc:            # noqa: BLE001
                self.status = "error"
                self.error = f"{type(exc).__name__}: {exc}"
            if self._stop.is_set():
                break
            # Back off, but stay interruptible so Stop is immediate.
            self._stop.wait(backoff)
            backoff = min(RECONNECT_MAX, backoff * 2)
        self.status = "stopped"

    def _session(self) -> None:
        self.status = "connecting"
        sock = socket.create_connection((self.host, self.port), timeout=30)
        try:
            sock.settimeout(5.0)
            sock.sendall(connect_packet(self.client_id))
            reader = Reader()
            if not self._await_connack(sock, reader):
                return
            sock.sendall(subscribe_packet(self.topics))
            self.status = "running"
            self.error = ""
            self.connected_at = time.time()
            self.last_msg = time.time()
            self._pump(sock, reader)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _await_connack(self, sock, reader) -> bool:
        deadline = time.time() + 30
        while time.time() < deadline and not self._stop.is_set():
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            if not data:
                raise ProtocolError("the broker closed the connection")
            reader.feed(data)
            for kind, _flags, body in reader.packets():
                if kind != CONNACK:
                    continue
                code = body[1] if len(body) > 1 else 255
                if code == 0:
                    return True
                self.status = "error"
                self.error = REFUSED.get(code, f"refused (code {code})")
                # A refusal is about us, not about the network: retrying
                # hard would be the callsign-lookup mistake again.
                self._stop.wait(RECONNECT_MAX)
                return False
        return False

    def _pump(self, sock, reader) -> None:
        next_ping = time.time() + KEEPALIVE / 2
        while not self._stop.is_set():
            try:
                data = sock.recv(8192)
                if not data:
                    raise ProtocolError("the broker closed the connection")
                reader.feed(data)
                for kind, flags, body in reader.packets():
                    if kind == PUBLISH:
                        self.last_msg = time.time()
                        self._deliver(flags, body)
            except socket.timeout:
                pass
            now = time.time()
            if now - self.last_msg > QUIET_DEATH:
                raise ProtocolError(
                    f"nothing for {QUIET_DEATH / 60:.0f} minutes; "
                    f"reconnecting")
            if now >= next_ping:
                sock.sendall(PING)
                next_ping = now + KEEPALIVE / 2

    def _deliver(self, flags: int, body: bytes) -> None:
        try:
            topic, payload = publish_parts(body, flags)
            spot = json.loads(payload)
        except (ProtocolError, ValueError):
            return                              # not ours; keep reading
        if not isinstance(spot, dict):
            return
        spot["topic"] = topic
        self.spots += 1
        try:
            self.on_spot(spot)
        except Exception:                       # noqa: BLE001
            pass        # a bad handler must not kill the feed either


# ---- topics ----------------------------------------------------------

def topics_for(call: str = "", grid: str = "", want: str = "both") -> list:
    """What to subscribe to for one station.

    `pskr/filter/v2/{band}/{mode}/{tx}/{rx}/{tx_grid}/{rx_grid}/{tx_dxcc}/{rx_dxcc}`

    "Who is hearing me" is the transmitter slot; "what am I hearing" is
    the receiver slot. Asking for both is two subscriptions, not one with
    a wildcard across them - a single `+` in both places is the whole
    world, which is thousands of spots a second and none of them yours.
    """
    call = (call or "").strip().upper()
    grid = (grid or "").strip().upper()[:4]
    out = []
    if call:
        if want in ("both", "tx"):
            out.append(f"pskr/filter/v2/+/+/{call}/+/+/+/+/+")
        if want in ("both", "rx"):
            out.append(f"pskr/filter/v2/+/+/+/{call}/+/+/+/+")
    elif grid:
        # No callsign: everything heard *in* the square, which is the
        # closest honest answer to "near me".
        out.append(f"pskr/filter/v2/+/+/+/+/+/{grid}/+/+")
    return out
