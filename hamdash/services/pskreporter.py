"""Report the stations this station hears to PSK Reporter.

What WSJT-X's "Enable PSK Reporter Spotting" does: each FT8 or FT4
station the Decoder card copies goes, with this station's callsign and
locator, to pskreporter.info, where the station that called CQ can see
where it was heard. The protocol is PSK Reporter's own description at
https://pskreporter.info/pskdev.html - IPFIX (RFC 5101) over UDP to
report.pskreporter.info port 4739 - and every rule below is theirs:

* **No more than one datagram every five minutes**, unless one fills up,
  on a timer counted from when this started - never from the clock, so
  that thousands of stations do not all send on the minute.
* **The record format descriptors (templates) go in the first three
  datagrams and then once an hour**; the server caches them per source
  address and port, which is why one socket is kept for the session.
* **A callsign at most once every five minutes, and once an hour unless
  it has changed** - here, unless it moved band. "The goal is to minimize
  the number of database records."
* `informationSource` is 1, automatically extracted; the header's
  sequence number counts reports, not datagrams; the observation domain
  is a random number constant for the session.

What is sent, and what is not, is decided here rather than in the card:

* **It is a host service, like `wsjtx.py`.** A card's process exists
  because a tile does; this has to outlive any one tile, and it is the
  host's outward network traffic. It hears `decoder.decoded` from the bus.
* **Off by default.** It sends the operator's callsign, locator and what
  they heard to a third party - the "nothing new widens the surface
  unasked" rule - so it starts only when ticked in Settings.
* **FT8 and FT4 only.** Their CRC makes a decoded callsign one that was
  really sent; CW, RTTY and PSK31 print whatever the noise spells, and a
  garbled callsign is a station on somebody else's map that does not
  exist. The decoder publishes only FT8 and FT4, and this checks again.
* **Never without a dial frequency**, because the frequency reported is
  the radio's dial plus the decode's audio offset and a guessed dial is a
  spot on the wrong band. Never from the mock or the simulated radio.
  Never the operator's own callsign. Never a hashed call (`<...>`, or one
  shown only because a hash matched). Never an OSD decode beyond
  `OSD_TRUST` - the decoder's less certain stage.
* **Development data never reaches the live map**: the tests encode
  datagrams and check them byte for byte against the examples on that
  page, and send only to a local socket.

The sender template is PSK Reporter's documented one with the sender's
locator: callsign, frequency, mode, informationSource, locator, time. It
carries no SNR, because their only documented templates with an SNR also
carry iMD, which FT8 does not measure, and the page asks that a program
not fitting them write to the author rather than invent its own. Asking
for an SNR template is the way to add it.
"""
from __future__ import annotations

import random
import socket
import struct
import threading
import time

from . import bands

HOST = "report.pskreporter.info"
PORT = 4739

INTERVAL = 300.0            # seconds between datagrams, at most one
TEMPLATE_EVERY = 3600.0     # templates at least this often...
TEMPLATE_FIRST = 3          # ...and in this many datagrams at the start
REPEAT_MIN = 300.0          # a callsign no more often than this, ever
REPEAT_SAME = 3600.0        # nor this, unless it has moved band
MAX_DATAGRAM = 1400         # bytes: under a typical MTU, never fragmented
MAX_QUEUE = 2000            # held while the network is down, at most
OSD_TRUST = 0.045           # the decoder's OSD_SURE_DIST

ENTERPRISE = 30351          # 0x768F, registered to PSK Reporter
RX_TEMPLATE = 0x9992
TX_TEMPLATE = 0x9993
VARIABLE = 0xFFFF
MODES = ("FT8", "FT4")

# (element id, length, enterprise?) - the cookie-cutter descriptors from
# the page, which the tests hold this to byte for byte.
RX_FIELDS = (
    (2, VARIABLE, True),        # receiverCallsign
    (4, VARIABLE, True),        # receiverLocator
    (8, VARIABLE, True),        # decodingSoftware
)
TX_FIELDS = (
    (1, VARIABLE, True),        # senderCallsign
    (5, 4, True),               # frequency, Hz
    (10, VARIABLE, True),       # mode
    (11, 1, True),              # informationSource
    (3, VARIABLE, True),        # senderLocator
    (150, 4, False),            # flowStartSeconds (IANA, not enterprise)
)


# ---------------------------------------------------------------- IPFIX


def _pad4(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 4)


def string(s: str) -> bytes:
    """A variable-length field: one length byte, then UTF-8."""
    raw = (s or "").encode("utf-8")[:254]
    return bytes((len(raw),)) + raw


def _field_specs(fields) -> bytes:
    out = b""
    for ident, length, enterprise in fields:
        if enterprise:
            out += struct.pack(">HHI", 0x8000 | ident, length, ENTERPRISE)
        else:
            out += struct.pack(">HH", ident, length)
    return out


def rx_template(fields=RX_FIELDS, template=RX_TEMPLATE) -> bytes:
    """An options template set (set id 3) with one scope field, as the
    page's receiver descriptor is."""
    body = struct.pack(">HHH", template, len(fields), 1) + _field_specs(fields)
    return _pad4(struct.pack(">HH", 3, 4 + len(body) + (-(4 + len(body)) % 4))
                 + body)


def tx_template(fields=TX_FIELDS, template=TX_TEMPLATE) -> bytes:
    """A template set (set id 2), as the page's sender descriptors are."""
    body = struct.pack(">HH", template, len(fields)) + _field_specs(fields)
    return _pad4(struct.pack(">HH", 2, 4 + len(body) + (-(4 + len(body)) % 4))
                 + body)


def data_set(template: int, records: list[bytes]) -> bytes:
    body = b"".join(records)
    size = 4 + len(body)
    return _pad4(struct.pack(">HH", template, size + (-size % 4)) + body)


def rx_record(call: str, locator: str, software: str) -> bytes:
    return string(call) + string(locator) + string(software)


def tx_record(spot: dict, fields=TX_FIELDS) -> bytes:
    """One heard station, in the order the template says."""
    out = b""
    for ident, length, _ in fields:
        if ident == 1:
            out += string(spot["call"])
        elif ident == 5:
            out += int(spot["freq"]).to_bytes(length, "big")
        elif ident == 10:
            out += string(spot["mode"])
        elif ident == 11:
            out += bytes((spot.get("source", 1),))
        elif ident == 3:
            out += string(spot.get("locator", ""))
        elif ident == 150:
            out += struct.pack(">I", int(spot["at"]))
        elif ident == 6:
            out += struct.pack(">b", max(-128, min(127, int(spot["snr"]))))
        else:
            raise ValueError(f"no encoding for element {ident}")
    return out


def datagram(*, export_time: int, sequence: int, domain: int,
             receiver: bytes, spots: list[bytes], templates: bool,
             rx_fields=RX_FIELDS, tx_fields=TX_FIELDS) -> bytes:
    """A whole message: header, templates if wanted, the receiver record,
    the sender records."""
    body = b""
    if templates:
        body += rx_template(rx_fields) + tx_template(tx_fields)
    body += data_set(RX_TEMPLATE, [receiver])
    if spots:
        body += data_set(TX_TEMPLATE, spots)
    head = struct.pack(">HHIII", 10, 16 + len(body), export_time,
                       sequence & 0xFFFFFFFF, domain)
    return head + body


# ---------------------------------------------------------------- policy


def base_call(call: str) -> str:
    """The part of a call that is the station: G4ABC of EA8/G4ABC/P."""
    parts = [p for p in (call or "").upper().split("/") if p]
    with_digit = [p for p in parts if any(c.isdigit() for c in p)]
    pool = with_digit or parts
    return max(pool, key=len) if pool else ""


class Reporter:
    """Collects spots and sends them, on PSK Reporter's terms.

    `station` returns the config's station block (callsign, grid), asked
    each time so a change in Settings applies at once. `send` is how a
    datagram leaves - a UDP socket in real use, a list in the tests - and
    `clock` is injectable for the same reason.
    """

    def __init__(self, station, version: str, send=None, clock=None,
                 rng=None, host: str = HOST, port: int = PORT) -> None:
        self.station = station
        self.software = f"hamdash {version}"
        self.clock = clock or time.time
        self._rng = rng or random.Random()
        self.host, self.port = host, port
        self._send = send
        self._sock = None
        self._lock = threading.Lock()
        self.enabled = False
        self.domain = self._rng.getrandbits(32)
        self.started = self.clock()
        self.next_send = self.started + INTERVAL
        self.sequence = 0           # reports sent before the next datagram
        self.datagrams = 0
        self.templates_at = None
        self.queue: list[dict] = []
        self.last_seen: dict[str, tuple[float, str]] = {}
        self.last_sent_at = None
        self.last_sent_count = 0
        self.total_sent = 0
        self.error = ""
        self.skipped: dict[str, int] = {}
        self._stop = threading.Event()
        self._thread = None

    # -- switching -------------------------------------------------------
    def set_enabled(self, on: bool) -> None:
        with self._lock:
            if on and not self.enabled:
                # Counted from now, not from the clock: "timed sends of
                # packets must not be synchronized to the system clock".
                self.started = self.clock()
                self.next_send = self.started + INTERVAL
            if not on:
                self.queue.clear()
            self.enabled = bool(on)

    def start(self) -> "Reporter":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="pskreporter")
            self._thread.start()
        return self

    def stop(self) -> None:
        """Send what is waiting - it was heard - and stop."""
        self._stop.set()
        try:
            if self.enabled and self.queue:
                self.flush(force=True)
        except Exception:                               # noqa: BLE001
            pass
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def _run(self) -> None:
        while not self._stop.wait(2.0):
            try:
                self.tick()
            except Exception as exc:                    # noqa: BLE001
                # Nothing may end this thread: a thread that dies takes its
                # job with it while the process stays up looking healthy.
                self.error = f"{type(exc).__name__}: {exc}"

    # -- why not ---------------------------------------------------------
    def blocked(self) -> str:
        """Why nothing can be sent right now, or "" if it can."""
        if not self.enabled:
            return "off"
        st = self.station() or {}
        if not str(st.get("callsign") or "").strip():
            return "no callsign"
        grid = str(st.get("grid") or "").strip()
        if len(grid) < 4:
            return "no locator"
        return ""

    def _skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1

    # -- taking spots ----------------------------------------------------
    def offer(self, event: dict) -> int:
        """A `decoder.decoded` bus event. Returns how many were queued."""
        if self.blocked():
            return 0
        if event.get("source") not in (None, "decoder"):
            self._skip("not from the decoder")
            return 0
        p = event.get("payload", event) or {}
        mode = str(p.get("mode") or "").upper()
        if mode not in MODES:
            self._skip("not FT8 or FT4")
            return 0
        if p.get("simulated"):
            self._skip("simulated")
            return 0
        own = base_call(str((self.station() or {}).get("callsign") or ""))
        at = float(p.get("slot_ts") or self.clock())
        n = 0
        with self._lock:
            for d in p.get("decodes") or []:
                why = self._refuse(d, own)
                if why:
                    self._skip(why)
                    continue
                call = d["call"].upper()
                band = bands.for_hz(int(d["rf_hz"])) or ""
                last = self.last_seen.get(call)
                now = self.clock()
                if last is not None:
                    gap = now - last[0]
                    if gap < REPEAT_MIN or (gap < REPEAT_SAME
                                            and last[1] == band):
                        self._skip("reported recently")
                        continue
                self.last_seen[call] = (now, band)
                grid = str(d.get("grid") or "")
                self.queue.append({"call": call, "freq": int(d["rf_hz"]),
                                   "mode": mode, "source": 1,
                                   "locator": grid if len(grid) == 4 else "",
                                   "at": int(at)})
                n += 1
            del self.queue[:-MAX_QUEUE]
            # Forget what is too old to matter, or a week of a busy band
            # is a dictionary of every station on it.
            if len(self.last_seen) > 5000:
                cut = self.clock() - REPEAT_SAME
                self.last_seen = {k: v for k, v in self.last_seen.items()
                                  if v[0] >= cut}
        return n

    @staticmethod
    def _refuse(d: dict, own: str) -> str:
        call = str(d.get("call") or "").strip().upper()
        if not call or call.startswith("<") or "..." in call:
            return "no callsign, or a hashed one"
        if not any(c.isdigit() for c in call):
            return "no callsign, or a hashed one"
        if own and base_call(call) == own:
            return "own callsign"
        if not d.get("rf_hz"):
            return "no dial frequency"
        # Decoded with part of the message assumed (a1 CQ, a2 my call):
        # very likely right, and not yet measured on enough real slots to
        # be put on somebody else's map. jt9 reports them; we do not, yet.
        if d.get("ap"):
            return "decoded a priori"
        if d.get("osd") and float(d["osd"]) > OSD_TRUST:
            return "uncertain decode"
        return ""

    # -- sending ---------------------------------------------------------
    def tick(self) -> None:
        """Send if the timer says so, or if a datagram's worth is waiting."""
        if self.blocked():
            return
        now = self.clock()
        if self.queue and (now >= self.next_send or self._full()):
            self.flush()
        if now >= self.next_send:
            # Keep to the schedule from the start, whether or not there
            # was anything to send this time.
            while self.next_send <= now:
                self.next_send += INTERVAL

    def _full(self) -> bool:
        return len(self._build(self.queue, dry=True)[1]) < len(self.queue)

    def _receiver(self) -> bytes:
        st = self.station() or {}
        return rx_record(str(st.get("callsign")).strip().upper(),
                         str(st.get("grid")).strip(), self.software)

    def _build(self, queue: list[dict], dry: bool = False
               ) -> tuple[bytes, list[dict]]:
        """The next datagram, and which spots it carries."""
        now = int(self.clock())
        templates = (self.datagrams < TEMPLATE_FIRST
                     or self.templates_at is None
                     or now - self.templates_at >= TEMPLATE_EVERY)
        receiver = self._receiver()
        fixed = len(datagram(export_time=now, sequence=0, domain=0,
                             receiver=receiver, spots=[b"x"],
                             templates=templates)) - 1
        taken, recs, size = [], [], fixed
        for spot in queue:
            r = tx_record(spot)
            if size + len(r) > MAX_DATAGRAM and recs:
                break
            recs.append(r)
            taken.append(spot)
            size += len(r)
        if dry:
            return b"", taken
        return datagram(export_time=now, sequence=self.sequence,
                        domain=self.domain, receiver=receiver, spots=recs,
                        templates=templates), taken

    def flush(self, force: bool = False) -> int:
        """Send one datagram of what is queued; return how many spots."""
        if self.blocked() and not force:
            return 0
        with self._lock:
            if not self.queue:
                return 0
            msg, taken = self._build(self.queue)
            had_templates = (self.datagrams < TEMPLATE_FIRST
                             or self.templates_at is None
                             or int(self.clock()) - self.templates_at
                             >= TEMPLATE_EVERY)
        try:
            self._transmit(msg)
        except OSError as exc:
            # Kept for the next turn of the timer: the network being down
            # for five minutes is not a reason to forget what was heard.
            self.error = f"could not send: {exc}"
            return 0
        with self._lock:
            del self.queue[:len(taken)]
            self.sequence += len(taken)
            self.datagrams += 1
            if had_templates:
                self.templates_at = int(self.clock())
            self.last_sent_at = self.clock()
            self.last_sent_count = len(taken)
            self.total_sent += len(taken)
            self.error = ""
        return len(taken)

    def _transmit(self, msg: bytes) -> None:
        if self._send is not None:
            self._send(msg)
            return
        if self._sock is None:
            # One socket for the session, so every datagram leaves from the
            # same port: the server keys its cached templates on it.
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        addr = socket.getaddrinfo(self.host, self.port, socket.AF_INET,
                                  socket.SOCK_DGRAM)[0][4]
        self._sock.sendto(msg, addr)

    # -- what the Settings sheet shows -----------------------------------
    def status(self) -> dict:
        why = self.blocked()
        return {"enabled": self.enabled, "blocked": why,
                "queued": len(self.queue), "total_sent": self.total_sent,
                "last_sent_at": self.last_sent_at,
                "last_sent_count": self.last_sent_count,
                "next_send_in": max(0, round(self.next_send - self.clock()))
                if self.enabled and not why else None,
                "error": self.error, "skipped": dict(self.skipped),
                "software": self.software, "host": f"{self.host}:{self.port}"}
