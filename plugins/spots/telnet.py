"""Spots that arrive on a socket: the Reverse Beacon Network, and clusters.

The HTTP sources in `sources.py` are polled; these push. One TCP connection
per feed, held open in a thread, lines parsed as they arrive.

Why it is worth the trouble: **RBN is the only thing that answers "who is
hearing *me*"**. `psk_reporter` does it for FT8 and friends; nothing else
here does it for CW, and the answer comes from machines listening all the
time rather than from whoever bothered to spot. Set it to your own callsign
and the card is a live map of your own signal.

Three things were read off the live services rather than assumed:

* **The login is a prompt, except when it isn't.** RBN asks `Please enter
  your call:` with no newline after it, so a reader waiting for a line never
  sees it. Clusters differ from each other - VE7CC sends a page of banner
  first and lists its own commands. So: answer a prompt if one appears in
  what has arrived, and otherwise send the callsign once the line has gone
  quiet.
* **A spot line is not whitespace separated.** `DX de JI1HFJ-#:430051.30`
  has no space after the colon, because the frequency field is a fixed width
  and 430 MHz fills it. Splitting on spaces puts the frequency in the
  spotter's name and drops the spot.
* **The rate is real.** RBN announced `Spot rate: 5/s` on CW and `8/s` on
  FT8 while this was being written. Everything is bounded: a ring of recent
  spots, and nothing is kept that nobody asked for.
"""
from __future__ import annotations

import itertools
import re
import socket
import threading
import time
from collections import deque

from hamdash.services.bands import for_hz as band_for

from sources import mode_by_frequency, mode_in_comment, sideband_in_comment

# RBN's own, and a cluster is whatever the operator points us at.
RBN_HOST = "telnet.reversebeacon.net"
RBN_CW_PORT = 7000             # CW, RTTY and the other skimmed modes
RBN_FT_PORT = 7001             # FT8 and FT4

# A spot line, from either. The frequency is a fixed-width field, so there
# may be no space between the spotter's colon and it.
SPOT = re.compile(
    r"^DX de\s+(?P<spotter>[^\s:]+)\s*:\s*"
    r"(?P<khz>\d+(?:\.\d+)?)\s+"
    r"(?P<call>[A-Z0-9/\-]+)\s*"
    r"(?P<info>.*?)\s*"
    r"(?P<utc>\d{3,4})Z\s*$",
    re.IGNORECASE)

# What the skimmers report, pulled out of the comment so they can be shown
# and filtered on rather than left as text.
SNR = re.compile(r"(-?\d+)\s*dB\b", re.IGNORECASE)
WPM = re.compile(r"(\d+)\s*WPM\b", re.IGNORECASE)
GRID = re.compile(r"\b([A-R]{2}\d{2})\b")
MODES = ("CW", "RTTY", "PSK31", "PSK63", "FT8", "FT4", "JT9", "JT65", "SSB",
         "USB", "LSB", "AM", "FM", "DATA", "BPSK", "MFSK")

# Arrival order, across every feed in this process. A wall clock cannot do
# this job: `time.time()` ticks about every 15.6 ms on Windows, so a burst of
# spots all carry the same stamp, the sort stays stable, and the oldest of
# them comes out on top - which is the bug this exists to fix. It survived a
# live test because RBN's five a second are further apart than the tick.
_arrivals = itertools.count()          # next() is atomic; no lock needed


# How many spots a feed keeps. At RBN's five a second this is about a
# minute and a half of everything, which is far more than the page shows -
# and it is bounded, which is the point.
KEEP = 600

# Reconnect backoff. A network that is down does not get better for being
# asked more often, and these are somebody else's servers.
RETRY_MIN = 5.0
RETRY_MAX = 300.0

# If nothing at all arrives for this long the connection is dead even though
# the socket says otherwise - which is how a dropped TCP session presents.
QUIET_DEATH = 420.0


def parse(line: str, kind: str = "rbn", now: float | None = None) -> dict | None:
    """One `DX de` line as a spot, or None if it is not one.

    Times on the wire are `HHMM` with no date, so they are anchored to the
    clock here: a spot stamped 2359 arriving at 0001 belongs to yesterday,
    and dating it today would put it almost a day in the future and sort it
    to the top of the list for ever.
    """
    m = SPOT.match(line.strip())
    if not m:
        return None
    try:
        hz = int(round(float(m.group("khz")) * 1000))
    except (TypeError, ValueError):
        return None
    info = m.group("info") or ""
    if kind == "rbn":
        # A skimmer decoded the signal, so its mode is a measurement and is
        # the first word of the line.
        first = info.split()[0].upper() if info.split() else ""
        mode, mode_from = (first if first in MODES else ""), ""
        sideband = ""
    else:
        # A DX cluster line is a person's comment: the same rule, and the
        # same mark, as a cluster spot from DX Summit (`mode_in_comment`).
        mode = mode_in_comment(info)
        mode_from = "comment" if mode else ""
        sideband = sideband_in_comment(info)
    if not mode:
        # Last resort, as for the polled sources: FT8 or FT4 by where it is.
        mode = mode_by_frequency(hz)
        mode_from = "frequency" if mode else ""

    snr = SNR.search(info)
    wpm = WPM.search(info)
    grid = GRID.search(info)
    return {
        "kind": kind,
        "call": m.group("call").upper(),
        "hz": hz,
        "band": band_for(hz),
        "mode": mode,
        "mode_from": mode_from,
        "sideband": sideband,
        "at": _when(m.group("utc"), now),
        "ref": "",
        "place": "",
        "where": "",
        "grid": grid.group(1) if grid else "",
        "lat": None, "lon": None,
        "spotter": m.group("spotter").upper(),
        "comment": info.strip(),
        "url": "",
        "snr_db": int(snr.group(1)) if snr else None,
        "wpm": int(wpm.group(1)) if wpm else None,
    }


def _when(hhmm: str, now: float | None = None) -> float:
    """`HHMM` on the wire, as epoch seconds, anchored to the clock here."""
    now = time.time() if now is None else now
    try:
        hh, mm = int(hhmm[:-2] or 0), int(hhmm[-2:])
    except (TypeError, ValueError):
        return now
    day = now - (now % 86400)
    at = day + hh * 3600 + mm * 60
    # More than a few minutes in the future means it was yesterday's clock.
    if at > now + 300:
        at -= 86400
    return at


class SpotStream:
    """One telnet feed, held open, reconnecting on its own.

    Nothing is ever sent except the login and whatever setup commands the
    operator configured. This never spots, announces or talks to anybody.
    """

    def __init__(self, kind: str, host: str, port: int, call: str,
                 commands=(), on_spot=None) -> None:
        self.kind = kind
        self.host, self.port = host, int(port)
        self.call = (call or "").strip().upper()
        self.commands = [c for c in (commands or []) if c.strip()]
        self.on_spot = on_spot
        self.spots: deque = deque(maxlen=KEEP)
        self.status = "starting"
        self.error = ""
        self.connected_at = 0.0
        self.last_line = 0.0
        self.seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=f"spots-{kind}")
        self._thread.start()

    # -- the connection ----------------------------------------------------
    def _run(self) -> None:
        wait = RETRY_MIN
        while not self._stop.is_set():
            if not self.call:
                # Every one of these networks identifies you by callsign.
                # Connecting without one, or with somebody else's, is not
                # something to do on the operator's behalf.
                self.status = "error"
                self.error = ("Set your callsign in Settings → Station "
                              "first: these networks log you in by it.")
                self._stop.wait(30)
                continue
            try:
                self._session()
                wait = RETRY_MIN                # a clean run resets the wait
            except Exception as exc:            # noqa: BLE001 - reported
                self.status = "error"
                self.error = str(exc)[:160]
            if self._stop.is_set():
                break
            self.status = "reconnecting"
            self._stop.wait(wait)
            wait = min(RETRY_MAX, wait * 2)

    def _session(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=20)
        self._sock = sock
        sock.settimeout(5)
        self.connected_at = self.last_line = time.time()
        self.status = "connecting"
        buf = b""
        logged_in = False
        quiet_since = time.time()
        try:
            while not self._stop.is_set():
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    chunk = b""
                else:
                    if not chunk:
                        raise OSError("the other end closed the connection")
                if chunk:
                    buf += chunk
                    quiet_since = time.time()
                    self.last_line = time.time()
                elif (time.time() - self.last_line) > QUIET_DEATH:
                    # The socket is open and nothing is coming. On these
                    # feeds that is a dead session, not a quiet band.
                    raise OSError("nothing has arrived for "
                                  f"{QUIET_DEATH / 60:.0f} minutes")

                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    self._line(raw.decode("utf-8", "replace").rstrip("\r"))

                if logged_in:
                    continue
                # A prompt has no newline after it, so it sits in `buf`
                # rather than arriving as a line. Answer it if it is there,
                # and otherwise once the far end has gone quiet - every
                # cluster has a different banner and some never prompt.
                tail = buf.decode("utf-8", "replace").lower()
                prompted = any(k in tail for k in
                               ("call", "login", "user", "please enter"))
                if prompted or (time.time() - quiet_since) > 3.0:
                    sock.sendall((self.call + "\r\n").encode())
                    for cmd in self.commands:
                        time.sleep(0.3)
                        sock.sendall((cmd.strip() + "\r\n").encode())
                    logged_in = True
                    buf = b""
                    self.status = "running"
        finally:
            self._sock = None
            try:
                sock.close()
            except OSError:
                pass

    def _line(self, line: str) -> None:
        if not line.strip():
            return
        self.last_line = time.time()
        spot = parse(line, self.kind)
        if not spot:
            # Banner, prompt, chatter. Kept only as the last thing seen, so
            # a feed that is connected but silent can say what it last got.
            return
        self.status = "running"
        with self._lock:
            # The wire carries HHMM and nothing finer, so every spot in the
            # same minute has the same time - and a sort on that alone falls
            # back to insertion order, which puts the oldest of them on top.
            # `at` stays honest; `arrived` breaks the tie, and it counts
            # rather than reading a clock (see `_arrivals`).
            spot["arrived"] = next(_arrivals)
            self.spots.append(spot)
            self.seq += 1
        if self.on_spot:
            try:
                self.on_spot(spot)
            except Exception:                   # noqa: BLE001
                pass

    # -- reading it --------------------------------------------------------
    def recent(self, max_age: float, mine: str = "") -> list:
        """Spots newer than `max_age`, newest first.

        `mine` is the headline: the spots *of* one callsign, which on RBN is
        every skimmer that can hear that station right now.
        """
        cutoff = time.time() - max_age
        want = (mine or "").strip().upper()
        with self._lock:
            rows = list(self.spots)
        out = [s for s in rows if s["at"] >= cutoff
               and (not want or s["call"] == want)]
        out.sort(key=lambda s: (s["at"], s.get("arrived") or 0), reverse=True)
        return out

    def status_line(self) -> dict:
        return {"name": self.kind, "status": self.status,
                "error": self.error, "held": len(self.spots),
                "ok": self.status == "running" or bool(self.spots),
                "age_s": round(time.time() - self.last_line)
                if self.last_line else 0,
                "up_s": round(time.time() - self.connected_at)
                if self.connected_at else 0}

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
