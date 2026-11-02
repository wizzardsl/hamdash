"""Radio control over Hamlib's rigctld.

Talks the rigctld text protocol on a TCP socket instead of binding libhamlib.
Nothing to compile, and it works the same whether rigctld is local or on a
Pi in the shack:

    rigctld -m 2014 -r /dev/ttyUSB0 -s 38400    # -m 2 is the dummy rig

Set radio.mock=true in config.json to drive a fake rig with no hardware.
"""
from __future__ import annotations

import socket
import threading
import time

from ..bus import BUS
from .bands import for_hz as band_for

# hamdash's names for modes, which are Hamlib's. A radio that cannot say
# what it offers is offered these; one that can (flrig's `rig.get_modes`,
# rigctld's `M ?`) is offered its own list, in these names.
MODES = ["LSB", "USB", "CW", "CWR", "AM", "FM", "RTTY", "RTTYR",
         "PKTUSB", "PKTLSB"]

# **Every radio spells a mode its own way**, and the same radio spells it
# differently through each backend: an IC-7300 through flrig says USB-D,
# a Yaesu says DATA-U, Hamlib says PKTUSB, and a memory saved under one
# was refused by the next. Every name maps to one of ours here, once, and
# both directions go through it - reading (`canon_mode`) and writing
# (`radio_mode`) - so they cannot disagree.
_ALIASES = {
    "CWR": ("CW-R", "CWR"),
    "RTTY": ("RTTY-L", "RTTY-LSB", "FSK", "RTTY"),
    "RTTYR": ("RTTY-R", "RTTY-U", "RTTY-USB", "FSK-R", "RTTYR"),
    "PKTUSB": ("PKTUSB", "PKT-U", "USB-D", "DATA-U", "DATA-USB", "DIG-U",
               "DIGU", "USB-DATA", "D-USB"),
    "PKTLSB": ("PKTLSB", "PKT-L", "LSB-D", "DATA-L", "DATA-LSB", "DIG-L",
               "DIGL", "LSB-DATA", "D-LSB"),
    "PKTFM": ("PKTFM", "PKT-FM", "FM-D", "DATA-FM", "D-FM"),
    "FM": ("FM", "FM-N", "NFM", "FMN"),
    "CW": ("CW", "CW-U", "CW-L", "CWU", "CWL"),
}
_TO_CANON = {alias: canon for canon, names in _ALIASES.items()
             for alias in names}
# A filter width welded onto a mode name: an FT-890 offers CW-W and CW-N,
# which are one mode and two filters.
_WIDTHS = ("-W", "-N", "-WIDE", "-NARR", "-NAR")


def canon_mode(name) -> str:
    """A radio's name for a mode, in ours. Unknown names come back as they
    are, upper-cased - shown to the operator rather than guessed at."""
    raw = str(name or "").strip().upper()
    if raw in _TO_CANON:
        return _TO_CANON[raw]
    for suffix in _WIDTHS:
        if raw.endswith(suffix) and len(raw) > len(suffix):
            base = raw[:-len(suffix)]
            return _TO_CANON.get(base, base)
    return raw


def radio_mode(want, offered) -> str:
    """The name in `offered` (a radio's own list) for the mode `want`, or ""
    if the radio has no such mode. An exact name wins; otherwise the first
    that means the same thing, so CW picks CW-W on an FT-890."""
    w = str(want or "").strip().upper()
    if not w:
        return ""
    for name in offered:
        if str(name).upper() == w:
            return str(name)
    target = canon_mode(w)
    for name in offered:
        if canon_mode(name) == target:
            return str(name)
    return ""


class RigError(RuntimeError):
    pass


class Rig:
    def __init__(self, host="127.0.0.1", port=4532, mock=False, timeout=2.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.mock = mock
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._fake = {"freq": 14074000, "mode": "USB", "width": 3000,
                      "ptt": 0, "vfo": "VFOA"}
        self._poll_stop = threading.Event()

    # --- transport ------------------------------------------------------
    def _connect(self) -> socket.socket:
        if self._sock is None:
            try:
                self._sock = socket.create_connection((self.host, self.port),
                                                      self.timeout)
            except OSError as exc:
                raise RigError(f"rigctld unreachable at {self.host}:{self.port}"
                               f" ({exc}). Start rigctld, or set radio.mock.")
        return self._sock

    def _cmd(self, line: str) -> list[str]:
        if self.mock:
            return self._mock(line)
        with self._lock:
            s = self._connect()
            try:
                s.sendall((line + "\n").encode())
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = s.recv(4096)
                    if not chunk:
                        raise RigError("rigctld closed the connection")
                    buf += chunk
            except OSError as exc:
                self._sock = None
                raise RigError(f"rigctld I/O failed: {exc}")
        out = [x for x in buf.decode(errors="replace").splitlines() if x != ""]
        if out and out[0].startswith("RPRT "):
            code = out[0][5:].strip()
            if code not in ("0",):
                raise RigError(f"rig returned error {code} for {line!r}")
            return out[1:]
        return out

    def _mock(self, line: str) -> list[str]:
        parts = line.split()
        op = parts[0]
        f = self._fake
        if op == "f":
            return [str(f["freq"])]
        if op == "F":
            f["freq"] = int(float(parts[1]))
            return []
        if op == "m":
            return [f["mode"], str(f["width"])]
        if op == "M":
            f["mode"] = parts[1]
            f["width"] = int(parts[2]) if len(parts) > 2 else 3000
            return []
        if op == "t":
            return [str(f["ptt"])]
        if op == "T":
            f["ptt"] = int(parts[1])
            return []
        if op == "v":
            return [f["vfo"]]
        if op == "l":  # get_level
            return ["0.42"]
        if op == "\\dump_state":
            return ["1", "2", "Mock rig"]
        return []

    # --- operations -----------------------------------------------------
    def state(self) -> dict:
        try:
            freq = int(float(self._cmd("f")[0]))
            mode_out = self._cmd("m")
            mode = mode_out[0] if mode_out else "?"
            width = int(float(mode_out[1])) if len(mode_out) > 1 else 0
            ptt = int(float(self._cmd("t")[0]))
            return {"ok": True, "mock": self.mock, "freq": freq, "mode": mode,
                    "width": width, "ptt": bool(ptt),
                    "band": band_for(freq)}
        except (RigError, IndexError, ValueError) as exc:
            return {"ok": False, "mock": self.mock, "error": str(exc)}

    def set_freq(self, hz: int) -> dict:
        self._cmd(f"F {int(hz)}")
        return self.state()

    def set_mode(self, mode: str, width: int = 0) -> dict:
        self._cmd(f"M {mode.upper()} {int(width)}")
        return self.state()

    def set_ptt(self, on: bool) -> dict:
        self._cmd(f"T {1 if on else 0}")
        return self.state()

    # --- polling --------------------------------------------------------
    def start_polling(self, hz: float = 2.0) -> None:
        def loop():
            last = None
            while not self._poll_stop.wait(1.0 / hz):
                st = self.state()
                key = (st.get("freq"), st.get("mode"), st.get("ptt"),
                       st.get("ok"))
                if key != last:
                    last = key
                    BUS.publish("radio.state", st, source="radio")
        threading.Thread(target=loop, daemon=True).start()

    def stop(self) -> None:
        self._poll_stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
