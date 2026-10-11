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


def new_id() -> str:
    return uuid.uuid4().hex[:8]


# --------------------------------------------------------------- backends
class Backend:
    """What every rig can be asked. Nothing above this layer knows which."""

    kind = "none"

    def state(self) -> dict:
        raise NotImplementedError

    def set_freq(self, hz: int) -> None:
        raise RigError(f"{self.kind} cannot set the frequency")

    def set_mode(self, mode: str, width: int = 0) -> None:
        raise RigError(f"{self.kind} cannot set the mode")

    def set_ptt(self, on: bool) -> None:
        raise RigError(f"{self.kind} cannot key the transmitter")

    def close(self) -> None:
        pass


class MockBackend(Backend):
    """A rig that is always there. The board has to be usable with no
    hardware, or it cannot be developed or demonstrated."""

    kind = "mock"

    def __init__(self, **_) -> None:
        self._f = {"freq": 14074000, "mode": "USB", "width": 3000, "ptt": False}

    def state(self) -> dict:
        return {"ok": True, "mock": True, **self._f,
                "band": band_for(self._f["freq"])}

    def set_freq(self, hz: int) -> None:
        self._f["freq"] = int(hz)

    def set_mode(self, mode: str, width: int = 0) -> None:
        self._f["mode"] = mode.upper()
        self._f["width"] = int(width) or 3000

    def set_ptt(self, on: bool) -> None:
        self._f["ptt"] = bool(on)


class RigctldBackend(Backend):
    """The rigctld text protocol over TCP, for a daemon already running."""

    kind = "rigctld"

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

    def state(self) -> dict:
        try:
            freq = int(float(self._cmd("f")[0]))
            m = self._cmd("m")
            ptt = int(float(self._cmd("t")[0]))
        except (RigError, IndexError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "freq": freq, "mode": m[0] if m else "?",
                "width": int(float(m[1])) if len(m) > 1 else 0,
                "ptt": bool(ptt), "band": band_for(freq)}

    def set_freq(self, hz: int) -> None:
        self._cmd(f"F {int(hz)}")

    def set_mode(self, mode: str, width: int = 0) -> None:
        self._cmd(f"M {mode.upper()} {int(width)}")

    def set_ptt(self, on: bool) -> None:
        self._cmd(f"T {1 if on else 0}")

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
            freq = int(float(self._call("rig.get_vfo")))
            mode = str(self._call("rig.get_mode") or "")
            ptt = bool(int(self._call("rig.get_ptt") or 0))
        except (RigError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "freq": freq, "mode": _adif_mode(mode),
                "rig_mode": mode, "width": 0, "ptt": ptt,
                "band": band_for(freq)}

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
                "rigctld was not found. Install Hamlib and put it on PATH, or "
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
        """The whole of a state read, in one apartment visit."""
        r = getattr(self._server(), f"Rig{self.slot}")
        status, says = int(r.Status), str(r.StatusStr)
        kind = str(r.RigType or "")
        if status != ST_ONLINE:
            return {"online": False, "status": says, "rig_type": kind}
        return {"online": True, "status": says, "rig_type": kind,
                "freq": int(r.Freq or 0), "mode": int(r.Mode or 0),
                "tx": int(r.Tx or 0)}

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
        return {"ok": True, "freq": got["freq"],
                "mode": OMNI_MODES.get(got["mode"], "?"), "width": 0,
                "ptt": tx == PM_TX, "ptt_known": tx in (PM_TX, PM_RX),
                "band": band_for(got["freq"]), "status": got["status"],
                "rig_type": got["rig_type"]}

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
            self._ptt_drop, self._ptt_drop_at = "", 0.0
        else:
            self._ptt_since = self._ptt_seen = 0.0
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
        if st.get("ok") and st.get("ptt") is False:
            # It stopped without us - the rig's own timeout, or the operator.
            # Holding a transmission that is not happening would have us send
            # a pointless unkey a few seconds later.
            self._ptt_since = self._ptt_seen = 0.0
            return st
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
                       st.get("audio_in"), st.get("audio_out"))
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
