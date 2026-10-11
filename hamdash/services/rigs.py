"""Several radios, three ways of reaching one.

`radio.py` spoke to one rigctld and nothing else. A shack usually has more
than one radio and more than one way into it, so a rig is now a named entry in
`radio.rigs` with a backend:

  mock      a fake rig, so the board works with no hardware at all
  omnirig   Windows: OmniRig owns the serial port and hamdash asks it
  hamlib    hamdash starts `rigctld` for a model and a port and talks to it
  rigctld   a daemon somebody else is already running, host and port

Every backend answers the same four questions - what frequency, what mode, is
it transmitting, and are you there - so the cards and the API never learn
which kind of rig they are talking to.

**OmniRig configures itself.** Its own dialog owns the rig type, the port and
the baud rate, and two programs cannot share a serial port; that is the whole
reason to use it. So an OmniRig entry here is a slot number, 1 or 2, and
nothing else - no port, no baud - and `configure()` opens OmniRig's dialog
rather than reimplementing it.
"""
from __future__ import annotations

import os
import re
import shutil
import queue
import socket
import subprocess
import math
import threading
import time
import uuid

from ..bus import BUS
from . import audio
from .radio import MODES, RigError, band_for         # noqa: F401 - re-exported

# OmniRig's own enums, read from its type library rather than assumed. Worth
# saying out loud: ST_ONLINE is 4, and Status 3 is "not responding" - an
# off-by-one guess here would report a silent radio as working.
ST_NOTCONFIGURED, ST_DISABLED, ST_PORTBUSY, ST_NOTRESPONDING, ST_ONLINE = 0, 1, 2, 3, 4
PM_UNKNOWN = 1
# Mode flags. OmniRig thinks in sidebands, Hamlib and ADIF think in mode
# names, so this is the whole of the translation.
OMNI_MODES = {
    8388608: "CW", 16777216: "CWR",
    33554432: "USB", 67108864: "LSB",
    134217728: "PKTUSB", 268435456: "PKTLSB",
    536870912: "AM", 1073741824: "FM",
}
MODE_TO_OMNI = {v: k for k, v in OMNI_MODES.items()}
PM_TX, PM_RX = 4194304, 2097152
# VFO and split, from the same type library (OmniRig.exe carries it; reading
# it creates no object and opens no port). The AA/AB/BA/BB forms are
# (receive, transmit) pairs, which is how OmniRig says "split" on a radio
# whose CAT has no separate split flag - so AB and BA *are* split, and a
# reader that only looks at `Split` reports a split radio as simplex.
PM_VFOAA, PM_VFOAB, PM_VFOBA, PM_VFOBB = 128, 256, 512, 1024
PM_VFOA, PM_VFOB = 2048, 4096
PM_VFOEQUAL, PM_VFOSWAP = 8192, 16384
PM_SPLITON, PM_SPLITOFF = 32768, 65536
PM_FREQA, PM_FREQB = 4, 8
# Which VFO is receiving, for each value `Vfo` can take.
OMNI_VFO = {PM_VFOA: "A", PM_VFOB: "B", PM_VFOAA: "A", PM_VFOAB: "A",
            PM_VFOBA: "B", PM_VFOBB: "B"}
OMNI_SPLIT_VFO = {PM_VFOAB, PM_VFOBA}

# What a backend can be asked for beyond frequency and mode. A card draws
# from this rather than from the backend's name: OmniRig has no meters at
# all (its interface has no such property), and a rig file may not support
# a second VFO, so "it is an OmniRig" does not answer the question.
F_VFO = "vfo"               # two VFOs, and which is in use
F_SPLIT = "split"           # split transmit
F_SWAP = "swap"             # exchange A and B
F_SMETER = "smeter"         # a receive signal meter
F_POWER = "power"           # a transmit power meter
F_SWR = "swr"

BAUDS = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
BACKENDS = ("mock", "omnirig", "flrig", "hamlib", "rigctld")

# flrig's own default. It is one XML-RPC server per instance, so a
# second flrig for a second radio is on a port the operator chose.
FLRIG_PORT = 12345

# How long any one call to it may take. flrig is on this machine and
# answers in well under a millisecond; this is for when it has gone away
# mid-call, because the thread waiting is one of the server's (invariant
# 4) rather than one of ours.
FLRIG_TIMEOUT = 2.0

# --------------------------------------------------------------- PTT safety
# Nothing else in the shack will take hamdash's transmission off the air. A
# card that keys the rig and then crashes, or a board that is closed mid
# transmission, leaves a transmitter running into an antenna with nobody
# watching - the one failure here with consequences outside the computer.
#
# Two clocks, because there are two ways to lose the operator:
#
#   PTT_HOLD     whoever keyed it has to keep saying so. The card pings
#                while it transmits; the pings stop when the page is closed,
#                the tile removed, the card crashes or the browser dies, and
#                a few seconds later the rig is unkeyed.
#   PTT_TIMEOUT  a ceiling on any one transmission, for when the pings keep
#                coming but nobody is there - the board left open on a
#                keyed rig.
#
# **Only transmissions hamdash started are ever ended.** If the rig reports
# PTT and hamdash did not key it - the operator on the mic, a footswitch,
# another program - it is left alone. Cutting somebody off mid-over because
# a dashboard thought it knew better is worse than the thing being guarded
# against.
PTT_HOLD = 6.0
PTT_TIMEOUT = 120.0
# How long the card goes on saying why a transmission ended.
PTT_DROP_NOTICE = 30.0
MAX_RIGS = 10


def _as_float(v):
    """A meter reading, or None. flrig answers `'7'`, Hamlib `'0.142'`, and
    a radio that cannot measure answers something that is neither - None is
    "it did not say", which a card draws as nothing rather than as zero."""
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


# ---- the S-meter scale -------------------------------------------------
# flrig's own help calls its meter "0...9...+60", and the numbers agree with
# it: a deflection of 8 on this FT-890 is what flrig itself reports as
# "S 1". So S0 sits at 0, S9 at the half way mark, and the top half is the
# 60 dB above S9 that a meter scale gives far less room to than S-units.
# Where a backend will tell us the S-units itself, that answer is used
# instead of this - flrig does, and its own radio tables beat any curve.
S9_PCT = 50.0
S_UNIT_DB = 6.0


def _s_units_for(pct: float) -> str:
    """A 0-100 meter deflection as "S 6" or "S 9+20"."""
    pct = max(0.0, min(100.0, float(pct)))
    if pct <= S9_PCT:
        return f"S {int(pct / (S9_PCT / 9))}"
    over = (pct - S9_PCT) / (S9_PCT / 60)
    return f"S 9+{int(round(over / 10) * 10)}"


def _pct_for_db(db: float) -> float:
    """Hamlib's STRENGTH is dB relative to S9. That is a real scale, so it
    converts properly rather than being drawn as a raw number."""
    db = float(db)
    if db <= 0:
        return max(0.0, S9_PCT + db * (S9_PCT / (9 * S_UNIT_DB)))
    return min(100.0, S9_PCT + db * (S9_PCT / 60.0))


def _s_units_for_db(db: float) -> str:
    db = float(db)
    if db >= 0:
        return f"S 9+{int(round(db / 10) * 10)}" if db >= 5 else "S 9"
    return f"S {max(0, int(round(9 + db / S_UNIT_DB)))}"


def new_id() -> str:
    return uuid.uuid4().hex[:8]


# --------------------------------------------------------------- backends
class Backend:
    """What every rig can be asked. Nothing above this layer knows which."""

    kind = "none"
    # What this backend can do beyond frequency and mode. The default is
    # "nothing extra", so a backend grows a control only by claiming it and
    # a card never offers a button that cannot work.
    features: frozenset = frozenset()

    def state(self) -> dict:
        raise NotImplementedError

    def meters(self, tx: bool) -> dict:
        """The needles. Separate from `state` on purpose.

        A meter changes on every read, and `radio.state` is published on
        change - putting them together would turn an event into a 2 Hz
        firehose waking every card on the board. So the card that draws
        them polls for them (the shape the audio cards settled on) and the
        bus keeps carrying only what actually changed.

        `tx` says which meters are meaningful, and it is not a detail: a
        radio has one physical meter, so asking for transmit power while
        receiving returns the S-meter reading. See `FlrigBackend.meters`.
        """
        return {}

    def set_freq(self, hz: int) -> None:
        raise RigError(f"{self.kind} cannot set the frequency")

    def set_mode(self, mode: str, width: int = 0) -> None:
        raise RigError(f"{self.kind} cannot set the mode")

    def set_ptt(self, on: bool) -> None:
        raise RigError(f"{self.kind} cannot key the transmitter")

    def set_vfo(self, which: str) -> None:
        raise RigError(f"{self.kind} cannot choose a VFO")

    def set_split(self, on: bool) -> None:
        raise RigError(f"{self.kind} cannot set split")

    def swap_vfo(self) -> None:
        raise RigError(f"{self.kind} cannot swap the VFOs")

    def close(self) -> None:
        pass


def _vfo_name(which) -> str:
    """'a', 'VFOB', 'B' -> 'A'/'B'. Anything else is refused by name."""
    got = str(which or "").strip().upper()
    got = got[-1] if got.startswith("VFO") else got
    if got not in ("A", "B"):
        raise RigError(f"there is no VFO {which!r}; it is A or B")
    return got


class MockBackend(Backend):
    """A rig that is always there. The board has to be usable with no
    hardware, or it cannot be developed or demonstrated."""

    kind = "mock"
    features = frozenset({F_VFO, F_SPLIT, F_SWAP, F_SMETER, F_POWER, F_SWR})

    def __init__(self, **_) -> None:
        self._f = {"mode": "USB", "width": 3000, "ptt": False}
        # A and B are kept, and the dial is whichever one is in use.
        # Selecting a VFO and swapping them are different operations:
        # selecting changes which is live, swapping exchanges what they
        # hold and leaves the live one alone.
        self._vfo = "A"
        self._freq = {"A": 14074000, "B": 14200000}
        self._split = False

    def state(self) -> dict:
        freq = self._freq[self._vfo]
        return {"ok": True, "mock": True, "freq": freq, **self._f,
                "band": band_for(freq), "vfo": self._vfo,
                "freq_a": self._freq["A"], "freq_b": self._freq["B"],
                "split": self._split}

    def meters(self, tx: bool) -> dict:
        """A needle that moves, because one that does not cannot be told
        from a broken one while the card is being built."""
        wobble = math.sin(time.time() * 2.0)
        if tx:
            # Nothing is transmitting, so this is openly made up - but it
            # has to be shaped like the real thing to lay out against.
            return {"power_pct": round(72 + 8 * wobble, 1),
                    "power_w": round(72 + 8 * wobble, 1),
                    "swr": round(1.3 + 0.15 * wobble, 2), "mock": True}
        s = 34 + 18 * wobble            # about S6, breathing
        return {"s_pct": round(s, 1), "s_units": _s_units_for(s),
                "s_dbm": round(-127 + s * 0.9), "mock": True}

    def set_freq(self, hz: int) -> None:
        self._freq[self._vfo] = int(hz)

    def set_mode(self, mode: str, width: int = 0) -> None:
        self._f["mode"] = mode.upper()
        self._f["width"] = int(width) or 3000

    def set_ptt(self, on: bool) -> None:
        self._f["ptt"] = bool(on)

    def set_vfo(self, which: str) -> None:
        self._vfo = _vfo_name(which)

    def set_split(self, on: bool) -> None:
        self._split = bool(on)

    def swap_vfo(self) -> None:
        self._freq["A"], self._freq["B"] = self._freq["B"], self._freq["A"]


class RigctldBackend(Backend):
    """The rigctld text protocol over TCP, for a daemon already running."""

    kind = "rigctld"
    features = frozenset({F_VFO, F_SPLIT, F_SMETER, F_POWER, F_SWR})

    def __init__(self, host="127.0.0.1", port=4532, timeout=2.0, **_) -> None:
        self.host, self.port, self.timeout = host, int(port), timeout
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None

    def where(self) -> str:
        return f"{self.host}:{self.port}"

    def _cmd(self, line: str) -> list[str]:
        with self._lock:
            if self._sock is None:
                try:
                    self._sock = socket.create_connection(
                        (self.host, self.port), self.timeout)
                except OSError as exc:
                    raise RigError(f"no rigctld at {self.where()} ({exc})")
            try:
                self._sock.sendall((line + "\n").encode())
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = self._sock.recv(4096)
                    if not chunk:
                        raise RigError("rigctld closed the connection")
                    buf += chunk
            except OSError as exc:
                self.close()
                raise RigError(f"rigctld I/O failed: {exc}")
        out = [x for x in buf.decode(errors="replace").splitlines() if x]
        if out and out[0].startswith("RPRT "):
            code = out[0][5:].strip()
            if code != "0":
                raise RigError(f"rig returned error {code} for {line!r}")
            return out[1:]
        return out

    def _maybe(self, line: str) -> list[str]:
        """A command whose absence is not a failure. Hamlib refuses what a
        rig cannot do, and a radio with one VFO is not a broken radio."""
        try:
            return self._cmd(line)
        except RigError:
            return []

    def state(self) -> dict:
        try:
            freq = int(float(self._cmd("f")[0]))
            m = self._cmd("m")
            ptt = int(float(self._cmd("t")[0]))
        except (RigError, IndexError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        out = {"ok": True, "freq": freq, "mode": m[0] if m else "?",
               "width": int(float(m[1])) if len(m) > 1 else 0,
               "ptt": bool(ptt), "band": band_for(freq)}

        vfo = self._maybe("v")
        which = (vfo[0] if vfo else "").strip().upper()
        if which.startswith("VFO"):
            which = which[3:]
        if which in ("A", "B"):
            out["vfo"] = which
            out["freq_" + which.lower()] = freq
            # Hamlib has no "read the other VFO" that works everywhere;
            # `get_vfo_info` does where it is supported, and where it is
            # not the other VFO is simply not reported. A missing number is
            # better than the dial's number labelled as the other VFO.
            other = "B" if which == "A" else "A"
            # `\get_vfo_info VFOB` answers with bare values, one per line -
            # measured against rigctld 4.7.2 on an FT-900:
            # ['14300000', 'USB', '2200', '0', '0']. Looking for a "Freq:"
            # label (which other versions and the -b modes do print) found
            # nothing and dropped the second VFO silently, with the number
            # sitting right there in the reply.
            for line in self._maybe(f"\\get_vfo_info VFO{other}"):
                line = line.strip()
                if line.lower().startswith("freq") and ":" in line:
                    line = line.split(":", 1)[1].strip()
                elif not line.isdigit():
                    continue
                try:
                    out["freq_" + other.lower()] = int(float(line))
                    break
                except ValueError:
                    continue
        split = self._maybe("s")
        if split:
            try:
                out["split"] = bool(int(float(split[0])))
            except ValueError:
                pass
        return out

    def meters(self, tx: bool) -> dict:
        """Hamlib levels. STRENGTH is dB relative to S9 - a real scale, so
        it becomes real S-units rather than a bare number."""
        out: dict = {}
        if tx:
            # RFPOWER_METER is 0..1 of full scale; the _WATTS form is
            # Hamlib 4.x and not every rig has it.
            pwr = _as_float((self._maybe("l RFPOWER_METER") or [""])[0])
            if pwr is not None:
                out["power_pct"] = round(pwr * 100.0, 1)
            watts = _as_float(
                (self._maybe("l RFPOWER_METER_WATTS") or [""])[0])
            if watts:
                out["power_w"] = round(watts, 1)
            swr = _as_float((self._maybe("l SWR") or [""])[0])
            if swr:
                out["swr"] = round(swr, 2)
            return out
        db = _as_float((self._maybe("l STRENGTH") or [""])[0])
        if db is not None:
            out["s_pct"] = round(_pct_for_db(db), 1)
            out["s_units"] = _s_units_for_db(db)
            out["s_db9"] = db
        return out

    def set_freq(self, hz: int) -> None:
        self._cmd(f"F {int(hz)}")

    def set_mode(self, mode: str, width: int = 0) -> None:
        try:
            self._cmd(f"M {mode.upper()} {int(width)}")
        except RigError:
            if int(width):
                raise
            # A passband of 0 means "the rig's own default" in Hamlib's
            # protocol, and some backends simply refuse it: measured
            # against one FT-900 on COM13, where `M USB 0` is an error and
            # `M USB -1` is fine - and the same on Hamlib's FT-890 backend,
            # so it is not one rig file being odd. -1 is "leave the
            # passband alone", which is the honest fallback when we were
            # never given a width to ask for. Without this, every mode
            # change from the card failed on that radio.
            self._cmd(f"M {mode.upper()} -1")

    def set_ptt(self, on: bool) -> None:
        self._cmd(f"T {1 if on else 0}")

    def set_vfo(self, which: str) -> None:
        self._cmd(f"V VFO{_vfo_name(which)}")

    def set_split(self, on: bool) -> None:
        # Hamlib wants to be told which VFO transmits, not just "on".
        self._cmd(f"S {1 if on else 0} VFOB")

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


class FlrigBackend(Backend):
    """flrig, over the XML-RPC server it runs on 127.0.0.1:12345.

    Worth having for the same reason as OmniRig: flrig already owns the
    serial port, and two programs cannot share one. Where flrig is running
    the choice is to go through it or to not talk to the radio at all.

    Two things about its interface are not what you would guess, and both
    were read off the running program rather than assumed:

    * **Frequencies come back as strings.** `rig.get_vfo` answers
      `'14199460'`, not an integer.
    * **Modes are the radio's own names**, so an FT-890 offers LSB, USB,
      CW-W, CW-N, AM-W, AM-N, FM - the width is welded onto the mode. Worse,
      `rig.set_mode` with a name the radio does not have **returns success
      and does nothing**: asking for PKTUSB leaves it on USB and says it
      worked. So a mode is matched against `rig.get_modes` here, and an
      unmatched one is refused with what the radio does offer.
    """

    kind = "flrig"
    features = frozenset({F_VFO, F_SPLIT, F_SWAP, F_SMETER, F_POWER, F_SWR})

    def __init__(self, host="127.0.0.1", port=FLRIG_PORT,
                 timeout=FLRIG_TIMEOUT, **_) -> None:
        self.host, self.port = host or "127.0.0.1", int(port or FLRIG_PORT)
        self.timeout = timeout
        # xmlrpc.client keeps one connection per proxy and is not safe to
        # share: the polling thread and a request thread would interleave two
        # calls on it. One lock, one proxy.
        self._lock = threading.Lock()
        self._rpc = None
        self._modes: list[str] = []

    def where(self) -> str:
        return f"{self.host}:{self.port}"

    def _proxy(self):
        if self._rpc is None:
            import xmlrpc.client

            class _Transport(xmlrpc.client.Transport):
                """The stdlib transport has no timeout, so a flrig that has
                stopped answering would hold the caller for ever."""

                def __init__(self, timeout):
                    super().__init__()
                    self._timeout = timeout

                def make_connection(self, host):
                    conn = super().make_connection(host)
                    conn.timeout = self._timeout
                    return conn

            self._rpc = xmlrpc.client.ServerProxy(
                f"http://{self.host}:{self.port}/",
                transport=_Transport(self.timeout), allow_none=True)
        return self._rpc

    def _call(self, name: str, *args):
        import xmlrpc.client
        with self._lock:
            try:
                fn = self._proxy()
                for part in name.split("."):
                    fn = getattr(fn, part)
                return fn(*args)
            except xmlrpc.client.Fault as exc:
                raise RigError(f"flrig refused {name}: {exc.faultString}")
            except Exception as exc:                # noqa: BLE001 - reported
                self.close()
                raise RigError(
                    f"no flrig at {self.where()} ({exc}). Start flrig, or "
                    f"check its Config -> Xcvr -> XML port.")

    # -- reading -----------------------------------------------------------
    def state(self) -> dict:
        try:
            a = int(float(self._call("rig.get_vfoA")))
            b = int(float(self._call("rig.get_vfoB")))
            which = str(self._call("rig.get_AB") or "A").strip().upper()[:1]
            split = bool(int(self._call("rig.get_split") or 0))
            mode = str(self._call("rig.get_mode") or "")
            ptt = bool(int(self._call("rig.get_ptt") or 0))
        except (RigError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        if which not in ("A", "B"):
            which = "A"
        # The dial is whichever VFO is in use, so it is read off A and B
        # rather than asked for separately - `rig.get_vfo` would be a
        # seventh call for a number we already have.
        freq = a if which == "A" else b
        return {"ok": True, "freq": freq, "mode": _adif_mode(mode),
                "rig_mode": mode, "width": 0, "ptt": ptt,
                "band": band_for(freq), "vfo": which,
                "freq_a": a, "freq_b": b, "split": split}

    def meters(self, tx: bool) -> dict:
        """What the needle says - and only what it can honestly say.

        **The power meter reads the S-meter while receiving.** A radio has
        one meter; flrig reads the same value for both, so `get_pwrmeter`
        answered 5-8 on an FT-890 that was doing nothing but listening.
        Drawn at rest that is "7 watts" out of a silent transmitter, which
        is a wrong answer that looks like a right one. So which meters are
        read follows the PTT, and the other ones are not reported at all
        rather than reported as zero.
        """
        try:
            if tx:
                out = {"power_pct": _as_float(self._call("rig.get_pwrmeter"))}
                # An SWR below 1 is not a reading. It is physically the
                # floor of the scale, so a radio answering 0 is saying it
                # does not measure it - the FT-890 does exactly that, with
                # 78% showing on the power meter beside it. Drawn as "SWR
                # 0" that is a perfect match into an open circuit.
                swr = _as_float(self._call("rig.get_swrmeter"))
                if swr is not None and swr >= 1.0:
                    out["swr"] = round(swr, 2)
                # Watts only where the radio says what full scale is; the
                # FT-890 answers 0, so it gets a bar and no false number.
                top = _as_float(self._call("rig.get_maxpwr"))
                if top and out.get("power_pct") is not None:
                    out["power_w"] = round(out["power_pct"] * top / 100.0, 1)
                return out
            pct = _as_float(self._call("rig.get_smeter"))
            # flrig knows its own radio's meter curve, so its S-units beat
            # anything computed here. `_s_units_for` is the fallback.
            units = str(self._call("rig.get_Sunits") or "").strip()
            dbm = _as_float(self._call("rig.get_DBM"))
            return {"s_pct": pct, "s_dbm": dbm,
                    "s_units": units or (_s_units_for(pct)
                                         if pct is not None else "")}
        except (RigError, TypeError, ValueError) as exc:
            return {"error": str(exc)}

    def info(self) -> dict:
        try:
            return {"ok": True, "rig_type": str(self._call("rig.get_xcvr")),
                    "version": str(self._call("main.get_version"))}
        except RigError as exc:
            return {"ok": False, "error": str(exc)}

    def settle(self) -> dict:
        """What Test shows: the state, plus which radio flrig is driving."""
        st = self.state()
        if not st.get("ok"):
            return st
        got = self.info()
        if got.get("ok"):
            st["rig_type"] = got["rig_type"]
        return st

    def modes(self) -> list[str]:
        if not self._modes:
            try:
                got = self._call("rig.get_modes")
            except RigError:
                return []
            self._modes = [str(m) for m in (got or [])]
        return self._modes

    # -- writing -----------------------------------------------------------
    def set_freq(self, hz: int) -> None:
        self._call("rig.set_frequency", float(int(hz)))

    def set_mode(self, mode: str, width: int = 0) -> None:
        want = _rig_mode(mode, self.modes())
        if not want:
            offers = ", ".join(self.modes()) or "nothing"
            raise RigError(
                f"flrig's radio has no mode like {mode!r}. It offers "
                f"{offers}.")
        self._call("rig.set_mode", want)

    def set_ptt(self, on: bool) -> None:
        # flrig's own help says this "waits for state change", so it is the
        # one call here that can take a moment.
        self._call("rig.set_ptt", 1 if on else 0)

    def set_vfo(self, which: str) -> None:
        # "sets & verifies vfo in use A or B", in flrig's own words.
        self._call("rig.set_AB", _vfo_name(which))

    def set_split(self, on: bool) -> None:
        self._call("rig.set_split", 1 if on else 0)

    def swap_vfo(self) -> None:
        self._call("rig.swap")

    def close(self) -> None:
        rpc, self._rpc = self._rpc, None
        if rpc is None:
            return
        try:
            # ServerProxy keeps the HTTP connection open between calls, and
            # ("close") is the documented way to reach the transport and let
            # go of it. Without this, deselecting the rig leaves a socket on
            # flrig until the process ends.
            rpc("close")()
        except Exception:                           # noqa: BLE001
            pass


def _adif_mode(mode: str) -> str:
    """A radio's mode name as the rest of hamdash spells it.

    An FT-890 calls its two CW filters CW-W and CW-N, and the log wants CW:
    the width is a filter setting, not a different mode, and writing CW-N
    into a QSO record would make it fail to match anybody else's log.
    """
    name = str(mode or "").strip().upper()
    for suffix in ("-W", "-N", "-WIDE", "-NARR", "-U", "-L"):
        if name.endswith(suffix) and name[:-len(suffix)] in MODES:
            return name[:-len(suffix)]
    return name


def _rig_mode(mode: str, offered: list[str]) -> str:
    """The name this radio knows for a mode, or "" if it has none.

    Returning "" matters: `rig.set_mode` with a name the radio does not have
    answers success and changes nothing, so the only way the operator finds
    out is by looking at the radio.
    """
    want = str(mode or "").strip().upper()
    if not want:
        return ""
    for name in offered:
        if name.upper() == want:
            return name
    # CW -> CW-W: the first filter width the radio offers for that mode.
    for name in offered:
        base = name.upper().split("-")[0]
        if base == want:
            return name
    return ""


class HamlibBackend(RigctldBackend):
    """A rigctld this process starts, for one model on one port.

    The daemon is ours: it is killed when the rig is deselected, because two
    rigctlds on one serial port is the same mistake as two programs on one.
    """

    kind = "hamlib"

    def __init__(self, model=0, port="", baud=0, data_bits=8, stop_bits=1,
                 parity="None", handshake="None", rigctld_path="", **_) -> None:
        super().__init__("127.0.0.1", free_port())
        self.model, self.port_name, self.baud = int(model or 0), port, int(baud or 0)
        self.data_bits, self.stop_bits = int(data_bits or 8), int(stop_bits or 1)
        self.parity, self.handshake = parity or "None", handshake or "None"
        self.rigctld_path = rigctld_path
        self.proc: subprocess.Popen | None = None
        self.log: list[str] = []
        self._start()

    def _start(self) -> None:
        exe = find_rigctld(self.rigctld_path)
        if not exe:
            raise RigError(
                "rigctld was not found. Run `python tools/get_hamlib.py` to "
                "fetch it, or install Hamlib yourself and put it on PATH, or "
                "set the path to rigctld in Settings -> Radio.")
        if not self.model:
            raise RigError("this rig has no Hamlib model set")
        if not self.port_name:
            raise RigError("this rig has no serial port set")
        cmd = [exe, "-m", str(self.model), "-r", self.port_name,
               "-t", str(self.port), "-T", "127.0.0.1"]
        if self.baud:
            cmd += ["-s", str(self.baud)]
        for name, value in (("data_bits", self.data_bits),
                            ("stop_bits", self.stop_bits),
                            ("serial_parity", self.parity),
                            ("serial_handshake", self.handshake)):
            cmd += ["-C", f"{name}={value}"]
        extra = {}
        if os.name == "nt":
            # Without this every rig flashes up a console window, which looks
            # exactly like the app misbehaving. Same reason as proc.py.
            extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", **extra)
        except OSError as exc:
            raise RigError(f"could not start rigctld: {exc}")
        threading.Thread(target=self._drain, daemon=True).start()
        # rigctld opens the serial port before it listens, and a radio that is
        # off can make that slow. Wait for the socket rather than guessing.
        deadline = time.time() + 6.0
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RigError(f"rigctld exited at once: {self.why() or 'no output'}")
            try:
                socket.create_connection((self.host, self.port), 0.3).close()
                return
            except OSError:
                time.sleep(0.15)
        raise RigError(f"rigctld did not start listening: {self.why() or 'no output'}")

    def _drain(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.rstrip()
            self.log.append(line)
            del self.log[:-60]
            print(f"[rigctld {self.model}] {line}")

    def why(self) -> str:
        """rigctld's own last words, which say far more than a closed socket."""
        return " / ".join(self.log[-3:])

    def close(self) -> None:
        super().close()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


class _ComThread:
    """One thread that owns every COM call.

    COM is per-apartment, not per-process: `CoInitialize` has to be called on
    the thread that uses an object, and an object made on one thread is not
    simply usable from another. Waitress hands each request to a different
    pool thread, so dispatching OmniRig inside a request handler fails with
    "CoInitialize has not been called" on whichever thread drew the short
    straw - and intermittently, which is worse than never.

    So every OmniRig call is posted here and the caller waits for the answer.
    One apartment, one object, no marshalling.
    """

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._fail = ""
        threading.Thread(target=self._run, daemon=True,
                         name="omnirig-com").start()

    def _run(self) -> None:
        try:
            import pythoncom
        except ImportError:
            self._fail = ("OmniRig needs pywin32, which is not installed. "
                          "`pip install pywin32`, then restart hamdash.")
            self._ready.set()
            return
        pythoncom.CoInitialize()
        self._ready.set()
        try:
            while True:
                fn, box = self._q.get()
                if fn is None:
                    return
                try:
                    box.put((True, fn()))
                except BaseException as exc:        # noqa: BLE001 - relayed
                    box.put((False, exc))
        finally:
            pythoncom.CoUninitialize()

    def call(self, fn, timeout: float = 15.0):
        self._ready.wait(5)
        if self._fail:
            raise RigError(self._fail)
        box: queue.Queue = queue.Queue(1)
        self._q.put((fn, box))
        try:
            ok, value = box.get(timeout=timeout)
        except queue.Empty:
            raise RigError("OmniRig did not answer in time") from None
        if ok:
            return value
        raise value


_COM: _ComThread | None = None
_COM_LOCK = threading.Lock()


def com_call(fn, timeout: float = 15.0):
    """Run `fn` on the COM thread, starting it the first time."""
    global _COM
    with _COM_LOCK:
        if _COM is None:
            _COM = _ComThread()
    return _COM.call(fn, timeout)


class OmniRigBackend(Backend):
    """OmniRig, which already owns the port and the rig's dialect.

    Deliberately thin. OmniRig's own window configures the rig type, port and
    baud rate; duplicating those here would be two places to get it wrong and
    two programs fighting over one serial port.

    **Every method crosses to the COM thread exactly once**, with all the COM
    work inside. Fetching a COM object on one thread and reading a property
    from another is the error this was first written as: `Rig1` is itself a
    property, so even reaching for it counted, and the result was
    "marshalled for a different thread" or "CoInitialize has not been
    called", depending on which pool thread served the request.
    """

    kind = "omnirig"
    # OmniRig's interface has no meter of any kind - it is a CAT
    # abstraction, not a receiver - so a card must not draw an empty
    # S-meter for it. What VFO and split it can really do is per rig file
    # and comes back on the state; this is the most it ever offers.
    features = frozenset({F_VFO, F_SPLIT, F_SWAP})
    # OmniRig starts polling when it starts, so a Dispatch that has only just
    # launched it reports "not responding" for a moment. Reading that as a
    # dead radio is wrong, and it is the first thing anyone would see.
    SETTLE = 2.5

    _shared = None
    _shared_at = 0.0

    def __init__(self, slot=1, **_) -> None:
        self.slot = 2 if int(slot or 1) == 2 else 1

    # --- inside the apartment -------------------------------------------
    @classmethod
    def _server(cls):
        """Only ever called on the COM thread."""
        if cls._shared is None:
            try:
                import win32com.client
            except ImportError:
                raise RigError(
                    "OmniRig needs pywin32, which is not installed. "
                    "`pip install pywin32`, then restart hamdash.") from None
            try:
                # Late binding on purpose: EnsureDispatch writes a cache that
                # a frozen build has nowhere to put.
                cls._shared = win32com.client.Dispatch("OmniRig.OmniRigX")
                cls._shared_at = time.time()
            except RigError:
                raise
            except Exception as exc:                # noqa: BLE001 - reported
                raise RigError(
                    f"OmniRig would not start ({exc}). It is Windows only, "
                    f"and has to be installed and run at least once.") from exc
        return cls._shared

    def _read(self) -> dict:
        """The whole of a state read, in one apartment visit.

        Everything the state needs is reached here, including the VFO and
        split, because `Rig1` is itself a property: crossing once per value
        is the bug this file already carries a note about.

        **What a rig can do is asked, not assumed.** OmniRig drives whatever
        its rig file describes, and plenty have one VFO or no split, so
        `IsParamWriteable` answers it - offering a Split button that the
        radio will ignore is worse than not offering one.
        """
        r = getattr(self._server(), f"Rig{self.slot}")
        status, says = int(r.Status), str(r.StatusStr)
        kind = str(r.RigType or "")
        if status != ST_ONLINE:
            return {"online": False, "status": says, "rig_type": kind}
        can = []
        if r.IsParamWriteable(PM_VFOA) and r.IsParamWriteable(PM_VFOB):
            can.append(F_VFO)
        # Plenty of rigs can be *told* which VFO to use and cannot say which
        # one they are on. `Vfo` still answers - with the last thing it was
        # told - so without this the echo of our own command comes back as a
        # reading. Measured on an FT-900: PM_VFOA and PM_VFOB both unreadable
        # and `Vfo` stuck on PM_VFOBA while the dial was plainly on A.
        vfo_known = bool(r.IsParamReadable(PM_VFOA)
                         or r.IsParamReadable(PM_VFOB))
        if r.IsParamWriteable(PM_SPLITON):
            can.append(F_SPLIT)
        if r.IsParamWriteable(PM_VFOSWAP):
            can.append(F_SWAP)
        return {"online": True, "status": says, "rig_type": kind,
                "freq": int(r.Freq or 0), "mode": int(r.Mode or 0),
                "tx": int(r.Tx or 0), "vfo": int(r.Vfo or 0),
                "vfo_known": vfo_known,
                "split": int(r.Split or 0), "can": can,
                "freq_a": int(r.FreqA or 0), "freq_b": int(r.FreqB or 0)}

    # --- outside ---------------------------------------------------------
    def state(self) -> dict:
        try:
            got = com_call(self._read)
        except RigError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:                    # noqa: BLE001 - reported
            return {"ok": False, "error": f"OmniRig: {exc}"}
        if not got["online"]:
            if time.time() - type(self)._shared_at < self.SETTLE:
                return {"ok": False, "starting": True,
                        "error": "OmniRig is starting…",
                        "status": got["status"]}
            # StatusStr is OmniRig's own words for it, which beat anything
            # invented here: "Port is busy" names the other program, "not
            # configured" names the dialog to open.
            return {"ok": False,
                    "error": f"OmniRig rig {self.slot}: {got['status']}",
                    "status": got["status"], "rig_type": got["rig_type"]}
        tx = got["tx"]
        # PM_UNKNOWN means OmniRig has not read it back yet. Calling that
        # "receiving" would be inventing an answer.
        out = {"ok": True, "freq": got["freq"],
               "mode": OMNI_MODES.get(got["mode"], "?"), "width": 0,
               "ptt": tx == PM_TX, "ptt_known": tx in (PM_TX, PM_RX),
               "band": band_for(got["freq"]), "status": got["status"],
               "rig_type": got["rig_type"], "features": got["can"]}
        if got["freq_a"]:
            out["freq_a"] = got["freq_a"]
        if got["freq_b"]:
            out["freq_b"] = got["freq_b"]
        if got["vfo_known"] and got["vfo"] in OMNI_VFO:
            out["vfo"] = OMNI_VFO[got["vfo"]]
        # **The flag wins.** Split is two answers in one: the `Split`
        # property, and the cross VFO forms - AB means receive on A and
        # transmit on B, which is split on a radio whose CAT has no split
        # flag of its own. The cross form is therefore only a *fallback*:
        # reading it over the top of an explicit PM_SPLITOFF reported an
        # FT-900 that was not split as split, and went on doing so after
        # the operator could see on the radio that it was not.
        if got["split"] in (PM_SPLITON, PM_SPLITOFF):
            out["split"] = got["split"] == PM_SPLITON
        elif got["vfo"] in OMNI_SPLIT_VFO:
            out["split"] = True
        return out

    def settle(self, seconds: float = 4.0) -> dict:
        """Wait out OmniRig's first poll, for an answer somebody asked for.

        Only used by Test and by selecting a rig: both are a deliberate
        press, where "starting..." is a non-answer. The polling path never
        blocks - it reports the status it sees.
        """
        deadline = time.time() + seconds
        st = self.state()
        while st.get("starting") and time.time() < deadline:
            time.sleep(0.25)
            st = self.state()
        return st

    def _set(self, prop: str, value) -> None:
        def go():
            r = getattr(self._server(), f"Rig{self.slot}")
            setattr(r, prop, value)
        com_call(go)

    def set_freq(self, hz: int) -> None:
        self._set("Freq", int(hz))

    def set_mode(self, mode: str, width: int = 0) -> None:
        flag = MODE_TO_OMNI.get((mode or "").upper())
        if not flag:
            raise RigError(f"OmniRig has no mode called {mode!r}. It knows "
                           + ", ".join(sorted(MODE_TO_OMNI)) + ".")
        self._set("Mode", flag)

    def set_ptt(self, on: bool) -> None:
        self._set("Tx", PM_TX if on else PM_RX)

    def set_vfo(self, which: str) -> None:
        self._set("Vfo", PM_VFOA if _vfo_name(which) == "A" else PM_VFOB)

    def set_split(self, on: bool) -> None:
        self._set("Split", PM_SPLITON if on else PM_SPLITOFF)

    def swap_vfo(self) -> None:
        self._set("Vfo", PM_VFOSWAP)

    def configure(self) -> None:
        """Open OmniRig's own dialog. The port and rig type live there."""
        def go():
            omni = self._server()
            omni.DialogVisible = True
        com_call(go)

    def info(self) -> dict:
        try:
            got = com_call(self._read)
        except RigError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:                    # noqa: BLE001 - reported
            return {"ok": False, "error": f"OmniRig: {exc}"}
        return {"ok": True, "rig_type": got["rig_type"],
                "status": got["status"], "online": got["online"]}


def make_backend(rig: dict):
    kind = (rig or {}).get("backend", "mock")
    if kind == "omnirig":
        return OmniRigBackend(**rig)
    if kind == "hamlib":
        return HamlibBackend(**rig)
    if kind == "flrig":
        return FlrigBackend(**rig)
    if kind == "rigctld":
        return RigctldBackend(**rig)
    return MockBackend(**rig)


# ----------------------------------------------------------------- helpers
def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def find_rigctld(hint: str = "") -> str:
    """Where rigctld is, if anywhere."""
    if hint and os.path.exists(hint):
        return hint
    found = shutil.which("rigctld") or shutil.which("rigctld.exe")
    if found:
        return found
    # Where `tools/get_hamlib.py` puts it. Imported here rather than at the
    # top: a card process imports this module for `band_for`, and config is
    # not its business.
    try:
        from ..config import default_home

        base = default_home() / "hamlib"
        for name in ("rigctld.exe", "rigctld"):
            for found in sorted(base.glob(f"**/{name}"), reverse=True):
                if found.is_file():
                    return str(found)
    except Exception:                               # noqa: BLE001
        pass
    # The usual Windows installs, since Hamlib's own installer does not put
    # itself on PATH.
    for base in (os.environ.get("ProgramFiles", ""),
                 os.environ.get("ProgramFiles(x86)", "")):
        if not base:
            continue
        try:
            for name in sorted(os.listdir(base), reverse=True):
                if not name.lower().startswith("hamlib"):
                    continue
                cand = os.path.join(base, name, "bin", "rigctld.exe")
                if os.path.exists(cand):
                    return cand
        except OSError:
            continue
    return ""


_MODEL_LINE = re.compile(r"^\s*(\d+)\s+(\S+)\s+(.+?)\s{2,}", re.M)


def hamlib_models(hint: str = "") -> dict:
    """Every rig Hamlib supports, from `rigctl -l`.

    Asked of Hamlib rather than kept here: the list is theirs, it grows every
    release, and a stale copy would offer a model number that means a
    different radio.
    """
    exe = find_rigctld(hint)
    rigctl = exe.replace("rigctld", "rigctl") if exe else ""
    if not rigctl or not os.path.exists(rigctl):
        return {"ok": False, "models": [],
                "error": "Hamlib was not found, so there is no model list to "
                         "offer. Install Hamlib (or set the path to rigctld "
                         "below) and press Rescan."}
    extra = {}
    if os.name == "nt":
        extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run([rigctl, "-l"], capture_output=True, text=True,
                             timeout=20, encoding="utf-8", errors="replace",
                             **extra).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "models": [], "error": f"`rigctl -l` failed: {exc}"}
    models = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        num = int(parts[0])
        if num == 0:
            continue
        rest = line[len(parts[0]):].strip()
        bits = rest.split()
        maker = bits[0]
        # The remainder is "Model  Version  Status  Backend"; the model name
        # can contain spaces, so take everything before the version column.
        name = " ".join(bits[1:]).strip()
        models.append({"model": num, "maker": maker, "name": name,
                       "label": f"{maker} {name.split('  ')[0]}".strip()})
    models.sort(key=lambda m: (m["maker"].lower(), m["label"].lower()))
    return {"ok": True, "models": models, "rigctl": rigctl,
            "count": len(models)}


def serial_ports() -> list[str]:
    """Which ports exist, so the operator picks rather than types."""
    try:
        from serial.tools import list_ports
        return sorted(p.device for p in list_ports.comports())
    except Exception:                               # noqa: BLE001
        return []


def ensure_rigs(cfg) -> None:
    """Bring a config up to the list-of-rigs shape, once.

    The old shape was one rigctld and a mock flag. Nobody should lose their
    working radio to an upgrade, so that becomes an entry rather than being
    dropped, and a mock rig is always present - the board has to be usable
    with the shack powered down.
    """
    radio = cfg.data.setdefault("radio", {})
    rigs = radio.setdefault("rigs", [])
    if not isinstance(rigs, list):
        rigs = radio["rigs"] = []
    changed = False

    if not any(r.get("backend") == "mock" for r in rigs):
        rigs.insert(0, {"id": new_id(), "name": "Simulated rig",
                        "backend": "mock"})
        changed = True

    host = radio.get("rigctld_host")
    port = radio.get("rigctld_port")
    if host and port and not any(r.get("backend") == "rigctld" for r in rigs):
        rigs.append({"id": new_id(), "name": f"rigctld at {host}:{port}",
                     "backend": "rigctld", "host": host, "port": int(port)})
        changed = True

    # Ids first: a config somebody edited by hand can have a rig without one,
    # and choosing the active rig reads an id.
    for r in rigs:
        if not r.get("id"):
            r["id"] = new_id()
            changed = True
        # An absent sound device and one deliberately set to nothing are the
        # same thing, so say so rather than leaving a card to tell null from
        # "" and guess which means "this host is too old to know".
        for key in ("audio_in", "audio_out"):
            if key not in r:
                r[key] = ""
                changed = True

    if not radio.get("active") or not any(r.get("id") == radio.get("active")
                                          for r in rigs):
        # An upgrade that was not in mock mode was driving the daemon, so
        # that is the one to come back on.
        want = "mock" if radio.get("mock", True) else "rigctld"
        pick = next((r for r in rigs if r.get("backend") == want), rigs[0])
        radio["active"] = pick["id"]
        changed = True
    if changed:
        try:
            cfg.save()
        except Exception:                           # noqa: BLE001
            pass


def clean_rig(raw: dict) -> dict:
    """One rig entry, with only the fields its backend actually uses.

    Keeping a stale COM port on an OmniRig entry would be a field the UI
    hides and the operator still believes in.

    Sound devices are the exception: they are kept whatever the backend,
    because which sound card a radio is wired to has nothing to do with how
    its CAT is reached. A shack with the rig on OmniRig and the audio on a
    SignaLink is the ordinary case, and switching backend must not throw the
    audio away.
    """
    kind = raw.get("backend")
    kind = kind if kind in BACKENDS else "mock"
    out = {"id": str(raw.get("id") or new_id())[:32],
           "name": str(raw.get("name") or "").strip()[:60] or "Radio",
           "backend": kind,
           "audio_in": audio.clean_device(raw.get("audio_in")),
           "audio_out": audio.clean_device(raw.get("audio_out"))}
    if kind == "omnirig":
        out["slot"] = 2 if int(raw.get("slot") or 1) == 2 else 1
    elif kind == "hamlib":
        out.update({
            "model": int(raw.get("model") or 0),
            "port": str(raw.get("port") or "").strip(),
            "baud": int(raw.get("baud") or 0),
            "data_bits": int(raw.get("data_bits") or 8),
            "stop_bits": int(raw.get("stop_bits") or 1),
            "parity": str(raw.get("parity") or "None"),
            "handshake": str(raw.get("handshake") or "None"),
        })
    elif kind == "rigctld":
        out.update({"host": str(raw.get("host") or "127.0.0.1").strip(),
                    "port": int(raw.get("port") or 4532)})
    elif kind == "flrig":
        out.update({"host": str(raw.get("host") or "127.0.0.1").strip(),
                    "port": int(raw.get("port") or FLRIG_PORT)})
    return out


# ----------------------------------------------------------------- manager
class RigManager:
    """The configured rigs, and whichever one is live.

    One backend is open at a time. Two programs cannot share a serial port and
    neither can two of ours, so selecting a rig closes the one before it.
    """

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()
        self._backend: Backend | None = None
        self._active = ""
        self._error = ""
        self._stop = threading.Event()
        # When we keyed, when somebody last said they still wanted it, and
        # why it was last taken off the air. Zero means the rig is not
        # transmitting on our account - see PTT_HOLD.
        self._ptt_since = 0.0
        self._ptt_seen = 0.0
        # Has the radio itself said "transmitting" since we keyed it? Some
        # cannot: an FT-900 through OmniRig keys perfectly and goes on
        # reporting PM_RX while it is on the air.
        self._ptt_confirmed = False
        self._ptt_drop = ""
        self._ptt_drop_at = 0.0
        self.select(self.conf().get("active", ""), announce=False)

    # --- configuration ---------------------------------------------------
    def conf(self) -> dict:
        return self.cfg.data.setdefault("radio", {})

    def rigs(self) -> list[dict]:
        return self.conf().setdefault("rigs", [])

    def find(self, rig_id: str) -> dict | None:
        return next((r for r in self.rigs() if r.get("id") == rig_id), None)

    def active(self) -> dict | None:
        return self.find(self._active)

    # --- the live rig ----------------------------------------------------
    def select(self, rig_id: str, announce: bool = True) -> dict:
        with self._lock:
            if self._backend:
                self._backend.close()
                self._backend = None
            self._error = ""
            rig = self.find(rig_id) or (self.rigs()[0] if self.rigs() else None)
            self._active = (rig or {}).get("id", "")
            if rig:
                try:
                    self._backend = make_backend(rig)
                except RigError as exc:
                    self._error = str(exc)
                except Exception as exc:            # noqa: BLE001 - reported
                    self._error = str(exc)
            self.conf()["active"] = self._active
        try:
            self.cfg.save()
        except Exception:                           # noqa: BLE001
            pass
        st = self.state()
        if announce:
            BUS.publish("radio.state", st, source="radio")
        return st

    def state(self) -> dict:
        rig = self.active()
        # The sound devices ride along with the state rather than being
        # looked up from config by whoever wants them: a decoder card asks
        # one question - what is the radio doing - and the answer has to
        # include which sound card is its audio, or it would have to track
        # the active rig itself and could disagree about it.
        base = {"rig_id": self._active, "rig": (rig or {}).get("name", ""),
                "backend": (rig or {}).get("backend", ""),
                "audio_in": (rig or {}).get("audio_in", ""),
                "audio_out": (rig or {}).get("audio_out", ""),
                "mock": (rig or {}).get("backend") == "mock"}
        if self._error:
            return {**base, "ok": False, "error": self._error}
        if not self._backend:
            return {**base, "ok": False,
                    "error": "No radio selected. Add one in Settings -> Radio."}
        out = {**base, **self._backend.state()}
        # What the card may offer. A backend says what it can do in
        # general; OmniRig refines it per rig file, because one rig file
        # has two VFOs and the next does not.
        out.setdefault("features", sorted(self._backend.features))
        # Why the radio stopped transmitting, so the card can say rather than
        # the transmission just ending. It expires: a message about something
        # that happened ten minutes ago, still on screen, reads as a fault
        # happening now.
        if self._ptt_drop:
            if time.time() - self._ptt_drop_at < PTT_DROP_NOTICE:
                out["ptt_dropped"] = self._ptt_drop
            else:
                self._ptt_drop, self._ptt_drop_at = "", 0.0
        if self._ptt_since:
            out["ptt_ours"] = True
            # **A radio that cannot report PTT must not read as receiving
            # while we are holding it on the air.** Until it has confirmed
            # a transmission once, our own key-down is the better answer -
            # otherwise the Transmit button offers to transmit on a radio
            # that already is. `ptt_reported` keeps what the rig actually
            # said, because the watchdog has to see that and not this.
            if out.get("ok") and not self._ptt_confirmed:
                out["ptt_reported"] = out.get("ptt")
                if out.get("ptt") is not True:
                    out["ptt"] = True
                    out["ptt_known"] = False
        return out

    def _do(self, fn, *a):
        if self._error or not self._backend:
            raise RigError(self._error or "no radio selected")
        fn(self._backend, *a)
        return self.state()

    def set_freq(self, hz: int) -> dict:
        return self._do(lambda b, v: b.set_freq(v), hz)

    def set_mode(self, mode: str, width: int = 0) -> dict:
        return self._do(lambda b, m, w: b.set_mode(m, w), mode, width)

    def set_vfo(self, which: str) -> dict:
        return self._do(lambda b, v: b.set_vfo(v), which)

    def set_split(self, on: bool) -> dict:
        return self._do(lambda b, v: b.set_split(v), on)

    def swap_vfo(self) -> dict:
        return self._do(lambda b: b.swap_vfo())

    def meters(self) -> dict:
        """The needles, for whoever is drawing them.

        Which ones are read follows the radio's own PTT rather than what
        hamdash last did, because something else may be keying it.
        """
        if self._error or not self._backend:
            return {"ok": False, "error": self._error or "no radio selected"}
        st = self._backend.state()
        if not st.get("ok"):
            return {"ok": False,
                    "error": st.get("error", "the radio is not answering")}
        tx = bool(st.get("ptt"))
        got = self._backend.meters(tx)
        return {"ok": "error" not in got, "tx": tx, **got}

    def set_ptt(self, on: bool) -> dict:
        """Key or unkey, and start (or refresh) the watchdog.

        Called again with `on=True` while already transmitting, this is the
        keep-alive: it refreshes the hold without touching the radio.
        """
        now = time.time()
        if on and self._ptt_since:
            self._ptt_seen = now            # a ping, not a fresh key-down
            return self.state()
        got = self._do(lambda b, v: b.set_ptt(v), on)
        if on:
            self._ptt_since = self._ptt_seen = now
            self._ptt_confirmed = False
            self._ptt_drop, self._ptt_drop_at = "", 0.0
        else:
            self._ptt_since = self._ptt_seen = 0.0
            self._ptt_confirmed = False
        return got

    def ptt_limits(self) -> tuple[float, float]:
        """The hold and the ceiling, from config, with 0 meaning no ceiling."""
        conf = self.conf()
        try:
            ceiling = float(conf.get("ptt_timeout", PTT_TIMEOUT))
        except (TypeError, ValueError):
            ceiling = PTT_TIMEOUT
        return PTT_HOLD, max(0.0, ceiling)

    def _watch_ptt(self, st: dict) -> dict:
        """Take our own transmission off the air if nobody is holding it.

        Runs on the polling thread, so it keeps working when the thing that
        keyed the rig has stopped running - which is the whole point.
        """
        if not self._ptt_since:
            # Not ours. A rig transmitting because the operator is on the
            # mic is not hamdash's to interrupt.
            return st
        says = st.get("ptt_reported", st.get("ptt"))
        if st.get("ok") and says is True:
            self._ptt_confirmed = True
        elif st.get("ok") and says is False and self._ptt_confirmed:
            # It stopped without us - the rig's own timeout, or the operator.
            # Holding a transmission that is not happening would have us send
            # a pointless unkey a few seconds later.
            self._ptt_since = self._ptt_seen = 0.0
            return st
        # **A radio only gets to say "it stopped" if it ever said "it
        # started".** An FT-900 through OmniRig keys and reports PM_RX
        # throughout, so this used to read as "it stopped without us" on the
        # very first poll after key-down: the hold and the ceiling were both
        # disarmed within half a second, and the transmitter stayed on the
        # air with nothing left watching it but the radio's own timeout.
        # Silently, which is the worst part.
        hold, ceiling = self.ptt_limits()
        now = time.time()
        why = ""
        if now - self._ptt_seen > hold:
            why = (f"nothing has been holding the transmission for "
                   f"{hold:.0f}s")
        elif ceiling and now - self._ptt_since > ceiling:
            why = f"transmission reached the {ceiling:.0f}s limit"
        if not why:
            return st
        print(f"unkeying: {why}")
        self._ptt_since = self._ptt_seen = 0.0
        self._ptt_drop, self._ptt_drop_at = why, now
        try:
            self._do(lambda b: b.set_ptt(False))
        except Exception as exc:                        # noqa: BLE001
            # Worth shouting about: the radio may still be transmitting.
            self._ptt_drop = f"{why}, and unkeying it failed: {exc}"
            print(f"UNKEY FAILED: {exc}")
        return {**self.state(), "ptt_dropped": self._ptt_drop}

    def backend(self) -> Backend | None:
        return self._backend

    # --- polling ---------------------------------------------------------
    def start_polling(self, hz: float = 2.0) -> None:
        def loop():
            last = None
            while not self._stop.wait(1.0 / hz):
                st = self._watch_ptt(self.state())
                key = (st.get("freq"), st.get("mode"), st.get("ptt"),
                       st.get("ok"), st.get("rig_id"),
                       st.get("audio_in"), st.get("audio_out"),
                       st.get("vfo"), st.get("freq_a"), st.get("freq_b"),
                       st.get("split"))
                if key != last:
                    last = key
                    BUS.publish("radio.state", st, source="radio")
        threading.Thread(target=loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        # Closing the app while it is transmitting must not leave the
        # transmitter on. The watchdog cannot help here - it is going away
        # with everything else - so this is the last thing that can.
        if self._ptt_since and self._backend:
            try:
                self._backend.set_ptt(False)
                print("unkeying: hamdash is shutting down")
            except Exception as exc:                    # noqa: BLE001
                print(f"UNKEY ON SHUTDOWN FAILED: {exc}")
        self._ptt_since = self._ptt_seen = 0.0
        with self._lock:
            if self._backend:
                self._backend.close()
                self._backend = None

    # --- trying one without selecting it ---------------------------------
    def test(self, rig: dict) -> dict:
        """Open a rig, ask it one question, close it again.

        Separate from `select` so the operator can check a new entry without
        taking the radio they are using away from them - except where the
        hardware says otherwise, which is what the note is for.
        """
        same = rig.get("id") and rig.get("id") == self._active
        if same and self._backend:
            st = self.state()
            return {**st, "message": _say(st)}
        backend = None
        try:
            backend = make_backend(rig)
            # A press deserves an answer, not "starting".
            st = (backend.settle() if hasattr(backend, "settle")
                  else backend.state())
            return {**st, "message": _say(st)}
        except RigError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:                    # noqa: BLE001 - reported
            return {"ok": False, "error": str(exc)}
        finally:
            if backend:
                backend.close()


def _say(st: dict) -> str:
    if not st.get("ok"):
        return st.get("error", "no answer")
    bits = []
    if st.get("rig_type"):
        bits.append(str(st["rig_type"]))
    if st.get("freq"):
        bits.append(f"{st['freq'] / 1e6:.6f} MHz")
    if st.get("mode") and st["mode"] != "?":
        bits.append(st["mode"])
    if st.get("band"):
        bits.append(st["band"])
    return " · ".join(bits) or "answered"
