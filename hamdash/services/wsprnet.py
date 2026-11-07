"""WSPR spots to WSPRnet, as WSJT-X's "Upload spots" does.

The Decoder card publishes each WSPR slot's decodes on `decoder.decoded`;
this hears them in-process (`Bus.listen`) and posts each one to
wsprnet.org, the database every WSPR map is drawn from.

* **A host service, not part of the card**, for the reasons the PSK
  Reporter uploader is one: it is the host's outward traffic, and it must
  outlive any tile and span every decoder on every radio.
* **Off by default** - "nothing new widens the surface unasked". It sends
  the operator's callsign, locator and what they heard to a third party.
  No callsign or no grid in the Station tab is a refusal with a reason.
* **The protocol is not documented by WSPRnet.** What is here was read off
  WSJT-X's client (as facts about the wire, not code - WSJT-X is GPL):
  one form POST per spot to http://wsprnet.org/post/, `function=wspr`,
  frequencies in MHz to six decimals, `date` yymmdd and `time` HHMM UTC
  of the slot, `mode=2` for two-minute WSPR, and success is a reply
  containing "spot(s) added". It is plain HTTP because that is what
  WSPRnet takes.
* **No dial, no spot; simulated, no spot.** A spot's frequency is the
  dial plus the audio offset, and a spot on the wrong frequency is worse
  than none - the same rule as PSK Reporter. A type 3 message whose
  callsign is only a hash nobody has heard in full names nobody, so it
  is not sent; a hashed call that is known is sent by name with its
  six-character grid.
* **What a refusal means decides what to do with the spot.** A network
  failure keeps the queue and tries again later: WSPRnet goes down, a
  station's internet goes down, and the spots are still true an hour
  later. A reply that is not "spot(s) added" is WSPRnet looking at this
  spot and saying no, and asking again would get the same answer - that
  one is dropped and the reply kept for the status line. WSJT-X clears
  its whole queue on either, which loses a night's spots to a blip.
* **Never sent to the live server from development.** The tests post to a
  list.

`tests/test_wsprnet.py`.
"""
from __future__ import annotations

import threading
import time

URL = "http://wsprnet.org/post/"
PACE = 0.5                  # s between posts, one spot each
RETRY_S = 60.0              # after a network failure
MAX_QUEUE = 2000            # a long outage keeps the newest this many
TIMEOUT = 15.0
ADDED = "spot(s) added"


def _mhz(hz) -> str:
    return f"{int(hz) / 1e6:.6f}"


class Uploader:
    """Queues WSPR spots and posts them one at a time.

    `station` returns the config's station block, asked each time so a
    change in Settings applies at once. `post(url, form) -> reply text` is
    how a spot leaves - requests in real use, a list in the tests - and it
    raises for anything that is not a reply."""

    def __init__(self, station, version: str, post=None, clock=None) -> None:
        self.station = station
        self.version = f"hamdash {version}"
        self.clock = clock or time.time
        self._post = post
        self._session = None
        self._lock = threading.Lock()
        self.enabled = False
        self.queue: list[dict] = []
        self.total_sent = 0
        self.rejected = 0
        self.last_sent_at = None
        self.error = ""
        self.retry_at = 0.0
        self.skipped: dict[str, int] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None

    # -- switching -------------------------------------------------------
    def set_enabled(self, on: bool) -> None:
        with self._lock:
            if not on:
                self.queue.clear()
                self.error = ""
            self.enabled = bool(on)

    def start(self) -> "Uploader":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="wsprnet")
            self._thread.start()
        return self

    def stop(self, budget: float = 5.0) -> None:
        """Send what is waiting, for a few seconds at most - it was heard,
        and a shutdown must not hang on somebody else's server."""
        self._stop.set()
        self._wake.set()
        end = time.monotonic() + budget
        while self.enabled and self.queue and time.monotonic() < end:
            if not self.send_one():
                break

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(PACE)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.send_one()
            except Exception as exc:                    # noqa: BLE001
                # Nothing may end this thread: a thread that dies takes its
                # job with it while the process stays up looking healthy.
                self.error = f"{type(exc).__name__}: {exc}"

    # -- why not ---------------------------------------------------------
    def blocked(self) -> str:
        if not self.enabled:
            return "off"
        st = self.station() or {}
        if not str(st.get("callsign") or "").strip():
            return "no callsign"
        if len(str(st.get("grid") or "").strip()) < 4:
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
        if str(p.get("mode") or "").upper() != "WSPR":
            return 0                    # FT8 and FT4 go to PSK Reporter
        if p.get("simulated"):
            self._skip("simulated")
            return 0
        dial = p.get("dial_hz")
        if not dial:
            self._skip("no dial frequency")
            return 0
        st = self.station() or {}
        me = str(st.get("callsign") or "").strip().upper()
        try:
            at = time.gmtime(float(p.get("slot_ts")))
        except (TypeError, ValueError):
            self._skip("no slot time")
            return 0
        n = 0
        with self._lock:
            for d in p.get("decodes") or []:
                call = str(d.get("call") or "").strip().upper()
                if not call or call.startswith("<"):
                    self._skip("hashed call not yet known")
                    continue
                if call == me:
                    self._skip("own call")
                    continue
                if not d.get("rf_hz"):
                    self._skip("no dial frequency")
                    continue
                self.queue.append({
                    "function": "wspr",
                    "rcall": me,
                    "rgrid": str(st.get("grid") or "").strip(),
                    "rqrg": _mhz(dial),
                    "date": time.strftime("%y%m%d", at),
                    "time": time.strftime("%H%M", at),
                    "sig": str(int(d.get("snr") or 0)),
                    "dt": f"{float(d.get('dt') or 0):.1f}",
                    "drift": str(int(round(float(d.get("drift") or 0)))),
                    "tqrg": _mhz(d["rf_hz"]),
                    "tcall": call,
                    "tgrid": str(d.get("grid") or ""),
                    "dbm": str(int(d.get("dbm") or 0)),
                    "version": self.version,
                    "mode": "2",
                })
                n += 1
            del self.queue[:-MAX_QUEUE]
        if n:
            self._wake.set()
        return n

    # -- sending ---------------------------------------------------------
    def _send(self, form: dict) -> str:
        if self._post is not None:
            return self._post(URL, form)
        import requests
        if self._session is None:
            self._session = requests.Session()
            self._session.headers["User-Agent"] = self.version
        r = self._session.post(URL, data=form, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text

    def send_one(self) -> bool:
        """Post the oldest spot. True if the queue moved."""
        if self.blocked() or not self.queue or self.clock() < self.retry_at:
            return False
        with self._lock:
            form = self.queue[0]
        try:
            reply = self._send(form)
        except Exception as exc:                        # noqa: BLE001
            # The network, not the spot: keep it and try again later.
            self.error = f"could not reach WSPRnet ({type(exc).__name__}) - will retry"
            self.retry_at = self.clock() + RETRY_S
            return False
        with self._lock:
            if self.queue and self.queue[0] is form:
                self.queue.pop(0)
        if ADDED in (reply or ""):
            self.total_sent += 1
            self.last_sent_at = self.clock()
            self.error = ""
        else:
            # WSPRnet looked at this spot and said no: asking again gets
            # the same answer, so it goes, and what it said is kept.
            self.rejected += 1
            self.error = "WSPRnet refused a spot: " + " ".join(
                str(reply or "no reply").split())[:160]
        return True

    def status(self) -> dict:
        return {"enabled": self.enabled, "blocked": self.blocked(),
                "queued": len(self.queue), "total_sent": self.total_sent,
                "rejected": self.rejected,
                "last_sent_at": self.last_sent_at, "error": self.error,
                "retry_in": max(0, round(self.retry_at - self.clock()))
                if self.retry_at > self.clock() else None,
                "skipped": dict(self.skipped)}
