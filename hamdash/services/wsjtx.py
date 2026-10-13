"""WSJT-X's UDP broadcast, and the QSOs it logs.

WSJT-X sends a datagram whenever something happens: a heartbeat every 15
seconds, a status line on every change, and - when the operator presses
Log QSO - both a structured record and the finished ADIF. This listens
for those and puts the logged contacts in the log as they happen.

**It is a host service, not a card**, and that is the whole shape of the
decision. A card's process exists because a tile exists; close the tile
and the socket goes with it, and the failure is forty FT8 contacts that
were never logged and nothing on screen saying so. Logging has to run
whether or not anybody is looking at it, which is the same reason
`qsl.Runner` lives here.

**Off by default, and bound to loopback.** Opening a port nobody asked
for widens the surface of an app with no authentication; this one is
ticked on deliberately.

The wire format is Qt's `QDataStream`, which is why this file is all
explicit big-endian unpacking:

  * integers are big-endian regardless of the machine;
  * a string is a `quint32` byte count then UTF-8, and `0xFFFFFFFF`
    means null rather than empty;
  * a bool is one byte;
  * a `QDateTime` is a Julian day (`qint64`), milliseconds since
    midnight (`quint32`), a time-spec byte, and an offset only when the
    spec says so.

`_Reader` raises on a short read rather than returning what it can,
because **reading the wrong number of bytes does not raise by itself** -
it silently misaligns everything after it, and what comes out is a
callsign made of somebody's grid square. That lesson is already written
down in `plugins/psk_reporter/mqtt.py`; this is the same mistake with a
different protocol in front of it.

This module imports nothing from hamdash, so `tests/test_wsjtx.py` can
drive it with bytes.
"""
from __future__ import annotations

import ipaddress
import socket
import struct
import threading
import time
from datetime import datetime, timedelta, timezone

#: Every WSJT-X datagram starts with this. Anything else on the port is
#: not ours and is dropped without comment - the port may well be a
#: multicast group somebody else is also using.
MAGIC = 0xADBCCBDA

HEARTBEAT = 0
STATUS = 1
DECODE = 2
CLEAR = 3
REPLY = 4
QSO_LOGGED = 5
CLOSE = 6
LOGGED_ADIF = 12

#: WSJT-X's own default. Its Settings → Reporting → UDP Server must point
#: here, or nothing arrives and everything looks fine from this side.
DEFAULT_PORT = 2237
DEFAULT_BIND = "127.0.0.1"

#: Heartbeats come every 15 s, so this is "we have missed three".
STALE_S = 50

#: Days from the Julian day number to the Unix epoch.
JULIAN_EPOCH = 2440588


class Truncated(ValueError):
    """A datagram ended in the middle of a field."""


class _Reader:
    """Qt's QDataStream, as much of it as this protocol uses."""

    def __init__(self, data: bytes):
        self.data = data
        self.at = 0

    def _take(self, n: int) -> bytes:
        if n < 0 or self.at + n > len(self.data):
            raise Truncated(f"wanted {n} bytes at {self.at} of {len(self.data)}")
        out = self.data[self.at:self.at + n]
        self.at += n
        return out

    def u8(self) -> int:
        return self._take(1)[0]

    def u32(self) -> int:
        return struct.unpack(">I", self._take(4))[0]

    def i32(self) -> int:
        return struct.unpack(">i", self._take(4))[0]

    def u64(self) -> int:
        return struct.unpack(">Q", self._take(8))[0]

    def i64(self) -> int:
        return struct.unpack(">q", self._take(8))[0]

    def boolean(self) -> bool:
        return self.u8() != 0

    def string(self) -> str:
        """A Qt utf8 string. `0xFFFFFFFF` is null, which is not empty.

        The distinction does not survive into Python here - both come
        back as "" - because nothing downstream wants to tell an absent
        grid square from a blank one.
        """
        n = self.u32()
        if n == 0xFFFFFFFF:
            return ""
        return self._take(n).decode("utf-8", "replace")

    def when(self) -> datetime | None:
        """A QDateTime, as UTC.

        Spec 1 is UTC and is what WSJT-X sends. Spec 2 carries an offset
        and only then is there a trailing qint32 to read - getting that
        wrong shifts every later field, which is exactly the misparse
        `_Reader` exists to make loud.
        """
        jd = self.i64()
        ms = self.u32()
        spec = self.u8()
        offset = self.i32() if spec == 2 else 0
        if jd <= 0:
            return None
        base = datetime(1970, 1, 1, tzinfo=timezone.utc)
        return base + timedelta(days=jd - JULIAN_EPOCH,
                                milliseconds=ms) - timedelta(seconds=offset)

    def more(self) -> bool:
        return self.at < len(self.data)


def decode(data: bytes) -> dict | None:
    """One datagram, as a dict, or None if it is not WSJT-X's.

    Only the messages this feature needs are unpacked past the header;
    the rest come back with their type and nothing else, which is enough
    to count them and to know the link is alive.
    """
    try:
        r = _Reader(data)
        if r.u32() != MAGIC:
            return None
        msg = {"schema": r.u32(), "type": r.u32(), "id": r.string()}
    except Truncated:
        return None

    kind = msg["type"]
    try:
        if kind == HEARTBEAT:
            msg["max_schema"] = r.u32()
            msg["version"] = r.string()
            msg["revision"] = r.string()
        elif kind == STATUS:
            msg["dial_hz"] = r.u64()
            msg["mode"] = r.string()
            msg["dx_call"] = r.string()
            msg["report"] = r.string()
            msg["tx_mode"] = r.string()
            msg["tx_enabled"] = r.boolean()
            msg["transmitting"] = r.boolean()
            msg["decoding"] = r.boolean()
            msg["rx_df"] = r.u32()
            msg["tx_df"] = r.u32()
            msg["de_call"] = r.string()
            msg["de_grid"] = r.string()
            msg["dx_grid"] = r.string()
        elif kind == QSO_LOGGED:
            msg["off"] = r.when()
            msg["dx_call"] = r.string()
            msg["dx_grid"] = r.string()
            msg["tx_hz"] = r.u64()
            msg["mode"] = r.string()
            msg["sent"] = r.string()
            msg["received"] = r.string()
            msg["power"] = r.string()
            msg["comments"] = r.string()
            msg["name"] = r.string()
            msg["on"] = r.when()
            msg["operator"] = r.string()
            msg["my_call"] = r.string()
            msg["my_grid"] = r.string()
        elif kind == LOGGED_ADIF:
            msg["adif"] = r.string()
    except Truncated:
        # A field we wanted was not there. Keep what was read - the type
        # and the id are enough to count the packet and to say the link
        # is alive - and mark it, rather than throwing the whole
        # datagram away. Schemas differ between versions and the fields
        # that get added go on the end.
        msg["short"] = True

    return msg


class Listener:
    """The socket, its thread, and what it has seen.

    `on_adif` is handed the ADIF text of a logged contact. Everything
    else it learns is for the status line, which exists to answer the
    only question an operator has about this: *is it actually working*.
    A port can be open and correct while WSJT-X points somewhere else,
    and the two look identical from here until a heartbeat arrives.
    """

    def __init__(self, bind: str = DEFAULT_BIND, port: int = DEFAULT_PORT,
                 on_adif=None, on_event=None, log=None):
        self.bind = (bind or DEFAULT_BIND).strip() or DEFAULT_BIND
        self.port = int(port or DEFAULT_PORT)
        self.on_adif = on_adif
        self.on_event = on_event
        self.log = log
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.error = ""
        self.seen_at = 0.0
        self.version = ""
        self.packets = 0
        self.qsos = 0          # type 5, the structured "QSO logged"
        self.adifs = 0         # type 12, the one carrying a record
        self.logged = 0        # what the log actually accepted
        self.last_call = ""

    # ---------------------------------------------------------------- life
    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True
            self._stop.clear()
            try:
                self._sock = self._open()
            except OSError as exc:
                self.error = self._why(exc)
                self._sock = None
                return False
            self.error = ""
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="wsjtx")
            self._thread.start()
            return True

    def _open(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._bind(s)
        except OSError:
            # A refused bind is the *expected* failure here - the port is
            # usually held by JTAlert - and leaking the descriptor each
            # time would make retrying the thing that runs the process
            # out of handles. `-W error::ResourceWarning` on the test run
            # is what caught it.
            s.close()
            raise
        s.settimeout(0.5)
        return s

    def _bind(self, s: socket.socket) -> None:
        multicast = self._is_multicast(self.bind)
        if multicast:
            # Several programs share a multicast group on purpose - that
            # is the whole reason WSJT-X offers one, so JTAlert and this
            # can both listen. SO_REUSEADDR is required for it.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", self.port))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                         struct.pack("4sl", socket.inet_aton(self.bind),
                                     socket.INADDR_ANY))
        else:
            # Deliberately *not* SO_REUSEADDR here. On Windows it lets a
            # second socket bind a port somebody already has, and the
            # datagrams then go to one of them unpredictably - so half
            # the QSOs would reach JTAlert and half would reach us, which
            # is far worse than refusing to start and saying why.
            s.bind((self.bind, self.port))

    @staticmethod
    def _is_multicast(addr: str) -> bool:
        try:
            return ipaddress.ip_address(addr).is_multicast
        except ValueError:
            return False

    def _why(self, exc: OSError) -> str:
        """What to do about it, not just what failed."""
        if getattr(exc, "errno", None) in (98, 10048) or "in use" in str(exc).lower():
            return (f"something else is already listening on UDP {self.port} — "
                    "JTAlert, GridTracker or another WSJT-X companion. Close "
                    "it, or point them all at a multicast address such as "
                    "239.255.0.1 and set the same one here.")
        if getattr(exc, "errno", None) in (99, 10049):
            return (f"this machine has no address {self.bind} — use 127.0.0.1, "
                    "or the address of the interface WSJT-X sends to.")
        return f"could not listen on {self.bind}:{self.port} — {exc}"

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            s, self._sock = self._sock, None
            if s:
                try:
                    s.close()
                except OSError:
                    pass
            t, self._thread = self._thread, None
        if t:
            t.join(timeout=2)

    @property
    def listening(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self.error)

    # ---------------------------------------------------------------- loop
    def _run(self) -> None:
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                return
            try:
                data, _from = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                # The socket was closed under us, which is how stop()
                # works. Anything else here is also terminal.
                return
            try:
                self._handle(data)
            except Exception as exc:                    # noqa: BLE001
                # **Nothing that arrives on the socket may end this
                # thread.** A thread that dies takes its job with it
                # while the process goes on looking perfectly healthy,
                # and the symptom is QSOs quietly not being logged.
                self._say(f"wsjtx: dropped a datagram: {exc!r}")

    def _handle(self, data: bytes) -> None:
        msg = decode(data)
        if msg is None:
            return                       # not WSJT-X; somebody else's port
        self.packets += 1
        self.seen_at = time.time()
        kind = msg["type"]

        if kind == HEARTBEAT and msg.get("version"):
            self.version = msg["version"]
        elif kind == QSO_LOGGED:
            self.qsos += 1
            if msg.get("dx_call"):
                self.last_call = msg["dx_call"]
        elif kind == LOGGED_ADIF:
            self.adifs += 1
            self._store(msg.get("adif") or "")
        elif kind == CLOSE:
            self.version = ""
            self.seen_at = 0.0

        if self.on_event:
            try:
                self.on_event(msg)
            except Exception as exc:                    # noqa: BLE001
                self._say(f"wsjtx: event handler: {exc!r}")

    def _store(self, adif: str) -> None:
        if not adif.strip() or not self.on_adif:
            return
        try:
            result = self.on_adif(adif) or {}
        except Exception as exc:                        # noqa: BLE001
            self._say(f"wsjtx: could not log the contact: {exc!r}")
            return
        self.logged += int(result.get("added", 0)) + int(result.get("updated", 0))

    def _say(self, line: str) -> None:
        if self.log:
            try:
                self.log(line)
            except Exception:                           # noqa: BLE001
                pass
        else:
            print(line, flush=True)

    # -------------------------------------------------------------- status
    def status(self) -> dict:
        """Enough to tell the three states apart.

        Not listening; listening and WSJT-X has never said anything;
        listening and it is there. The middle one is the common case
        when something is wrong, and it is the one a bare "enabled"
        flag cannot express.
        """
        age = time.time() - self.seen_at if self.seen_at else None
        fresh = age is not None and age < STALE_S
        return {
            "listening": self.listening,
            "bind": self.bind,
            "port": self.port,
            "multicast": self._is_multicast(self.bind),
            "error": self.error,
            "connected": fresh,
            "age_s": round(age) if age is not None else None,
            "version": self.version,
            "packets": self.packets,
            "qsos": self.qsos,
            "logged": self.logged,
            "last_call": self.last_call,
            # WSJT-X sends a structured QSO *and* an ADIF record for the
            # same contact. Only the ADIF is logged - it is the complete
            # record and goes through the one importer, so it dedupes
            # like every other import. If the structured ones arrive and
            # the ADIF ones do not, the version at the other end is too
            # old to send them, and that is worth saying rather than
            # leaving the operator with contacts that never appear.
            "adif_missing": self.qsos > 0 and self.adifs == 0,
        }
