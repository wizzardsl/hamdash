"""One capture per sound card, fanned out to everybody who wants it.

This is the part of the receive-audio engine that owns devices, split out
of `card.py` so it can be driven with fake sources playing known tones -
`card.py` calls `card.run()` at import, which blocks and binds a port.
It imports nothing from Flask or the card, like `dsp.py` and
`decoder/cw.py`.

Why it exists: PortAudio on Windows will not reliably give one capture
device to two openers. The spectrum, the decoder and Listen each used to
open the radio's input in their own process, so whichever got there
second failed - and named WSJT-X as the likely culprit. Now this engine is
the only thing that opens a real input, and the others read numbered
blocks from it over HTTP, the way the browser always has (`AudioFeed` in
the SDK).

Three layers, each with one job:

* `Capture` - one per device. The only thing that opens it, and it is
  closed exactly once, when its last `Tap` goes.
* `Tap` - one per (device, rate). A decimator and a ring of numbered
  int16 blocks. Listen at 12 kHz and a waterfall at 24 kHz are two taps
  on one capture: two filters, one open.
* `Hub` - the wants. A want is per *listener* (a page, or a card's
  feed), never per tile, and a tap goes when its last listener leaves.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

import numpy as np

import dsp

BLOCK_MS = 20           # one numbered block of audio
HISTORY = 150           # blocks kept: 3 s, enough to ride out a stall
IDLE_SECONDS = 20.0     # close an input no listener has asked for
RETRY_SECONDS = 5.0     # before respinning a failed input
# How long a Stop waits for the capture thread to actually let the
# device go. The event is already set by then, so this is reached
# only if PortAudio is slow to close; the normal case is a
# millisecond. Bounded because it blocks a request thread.
CLOSE_WAIT = 1.0
MIN_RATE, MAX_RATE = 4000, 48000

# What 'rig' resolves to when the radio has no audio input set. Never ""
# - that is the system default input, which is somebody's microphone.
NO_RIG_INPUT = "<no radio input>"

# How a card's feed says "the system default input": a blank `device`
# on a request means "this tile's own settings" instead.
SYSTEM_DEFAULT = "<system default>"

# PortAudio is not thread-safe across enumerate/open: one lock for both.
# Only this process opens inputs now, so this one lock covers every open.
_pa_lock = threading.Lock()


class Capture:
    """One audio input, opened once, its samples handed to every tap."""

    def __init__(self, device: str,
                 source: Callable[["Capture"], None] | None = None) -> None:
        self.device = device
        self.started_at = time.time()
        self.in_rate = 48000
        self.status = "starting"
        self.error = ""
        self.hint = ""
        self.overflows = 0
        self.taps: list[Tap] = []
        self._source = source
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"rx-{device or 'default'}")

    def start(self) -> "Capture":
        self._thread.start()
        return self

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # -- capture ---------------------------------------------------------
    def _run(self) -> None:
        if self._source is not None:
            # A test's fake, or anything else that is not PortAudio. It
            # calls `begin(rate)` and then `feed()` until `stopping`.
            try:
                self._source(self)
                if self.status == "running":
                    self.status = "stopped"
            except Exception as exc:                    # noqa: BLE001
                self._fail(f"source failed: {exc}")
            return
        if self.device == "mock":
            self._run_mock()
            return
        if self.device == NO_RIG_INPUT:
            # Not the system default. That used to be where 'rig' ended up
            # for a radio with no audio input set, and the system default
            # input is usually a microphone - which is why this card had
            # to default to a test tone rather than to the radio.
            return self._fail(
                "This radio has no audio input set.",
                "Set it in Settings -> Radio, or choose an input in this "
                "tile's settings.")
        try:
            import sounddevice as sd
        except Exception as exc:                        # noqa: BLE001
            return self._fail(
                f"sounddevice is not installed ({exc}).",
                "Set this tile's input to 'mock' to try the card without a "
                "radio, or install the sounddevice package.")
        # Bound *outside* the try, so the close below runs for a stream
        # that was built and then failed to start. `stream.start()` used
        # to sit inside the `with _pa_lock` block, ahead of the try that
        # closes it: a host error there jumped straight to the outer
        # handler with a constructed stream holding the device and no
        # reference left anywhere to close it.
        stream = None
        try:
            try:
                with _pa_lock:
                    index = _resolve_device(sd, self.device)
                    info = sd.query_devices(index)
                    self.in_rate = int(info["default_samplerate"])
                    stream = sd.InputStream(
                        device=index, channels=1, dtype="float32",
                        samplerate=self.in_rate,
                        blocksize=int(self.in_rate * BLOCK_MS / 1000),
                        callback=self._on_audio)
                    stream.start()
                self.status = "running"
                while not self._stop.wait(0.25):
                    pass
            finally:
                # Closing must not be able to fail its way out of
                # closing. A raise from `stop()` here used to propagate
                # to the outer handler, which called `_fail()` and left
                # the stream open - and by then the Capture has been
                # popped from the hub, so nothing holds a reference and
                # nothing can ever close it. The device is held for the
                # life of the card process, and every later open answers
                # DSERR_ALLOCATED, which this card reports as "another
                # program may be holding that input" - sending the
                # operator to look for a WSJT-X that is not running.
                if stream is not None:
                    with _pa_lock:
                        for shut in (stream.stop, stream.close):
                            try:
                                shut()
                            except Exception as exc:    # noqa: BLE001
                                print(f"rx_audio: {self.device or 'default'}:"
                                      f" {shut.__name__} failed: {exc}")
            self.status = "stopped"
        except Exception as exc:                        # noqa: BLE001
            # Naming the likely cause matters more here than anywhere: a
            # sound card can only be held by one program at a time on
            # Windows. Not "a spectrum or decoder tile here" any more -
            # they read from this capture rather than opening their own.
            self._fail(
                f"could not open {self.device or 'the default input'}: {exc}",
                "Another program may be holding that input - WSJT-X, "
                "fldigi, or a second copy of hamdash. Close it, or choose "
                "a different input in this tile's settings.")

    def _fail(self, why: str, hint: str = "") -> None:
        self.status = "error"
        self.error = why
        self.hint = hint

    def begin(self, in_rate: int) -> None:
        """A source says what rate it runs at, and that it is running."""
        self.in_rate = int(in_rate)
        self.status = "running"

    def _on_audio(self, indata, frames, time_info, status) -> None:
        if status and getattr(status, "input_overflow", False):
            self.overflows += 1
        self.feed(indata[:, 0].copy())

    def _run_mock(self) -> None:
        """A tone that sweeps, with a click on each whole second.

        Mock by default like the rig, so the card can be set up, placed on
        a board and listened to with no radio attached. The click is also
        what makes latency measurable, which is how the transport was
        proven in the first place.
        """
        self.begin(48000)
        n = int(self.in_rate * BLOCK_MS / 1000)
        phase = 0.0
        nxt = time.time()
        while not self._stop.is_set():
            t0 = time.time()
            t = np.arange(n, dtype=np.float32) / self.in_rate
            hz = 600.0 + 120.0 * np.sin(t0 * 0.25)
            block = 0.22 * np.sin(2 * np.pi * hz * t + phase)
            phase = (phase + 2 * np.pi * hz * n / self.in_rate) % (2 * np.pi)
            edge = np.ceil(t0) - t0
            if 0 <= edge < n / self.in_rate:
                i = int(edge * self.in_rate)
                block[i:i + 240] = 0.85
            self.feed(block.astype(np.float32))
            nxt += n / self.in_rate
            time.sleep(max(0.0, nxt - time.time()))

    # -- fan-out ---------------------------------------------------------
    def feed(self, samples: np.ndarray) -> None:
        """Every tap gets the same samples, through its own filter."""
        for tap in list(self.taps):
            tap.feed(samples)

    def stop(self, wait: float = CLOSE_WAIT) -> None:
        """Ask the capture thread to stop, and wait for it to let go.

        Setting the flag is not releasing the device. The thread still
        has to leave its loop, take `_pa_lock` and close the stream, and
        `release()` used to return before any of that - so a Listen
        pressed straight after a Stop, or the page's own status poll
        which lands immediately after one, opened the device while the
        previous stream still held it. PortAudio answers
        DSERR_ALLOCATED, the card reports "another program may be
        holding that input", and the failed capture is then cached for
        `RETRY_SECONDS` - so the error outlives its cause and names the
        wrong culprit.

        The wait is bounded and normally microscopic: the event is
        already set, so the thread only has to reach its `finally`. It
        is a blocking wait in a request thread (invariant 4), which is
        the trade - a tenth of a second at the one moment the operator
        asked for the device to be handed over, against a card that
        cannot be restarted until the process is.
        """
        self._stop.set()
        # Never from the capture thread itself: that is a deadlock, and
        # it is the thread already doing the closing.
        if (threading.current_thread() is not self._thread
                and self._thread.is_alive()):
            self._thread.join(wait)


class Tap:
    """One rate's view of a capture: decimated, ringed, numbered."""

    def __init__(self, capture: Capture, rate: int) -> None:
        self.capture = capture
        self.wanted_rate = rate
        self.out_rate = rate
        self.block_n = max(32, int(rate * BLOCK_MS / 1000))
        self.seq = 0
        self.first_seq = 0
        self.peak = 0.0                    # for the level meter
        self.blocks: deque[bytes] = deque(maxlen=HISTORY)
        self.stamps: deque[float] = deque(maxlen=HISTORY)
        self._dec: dsp.Decimator | None = None
        self._acc = np.zeros(0, dtype=np.float32)
        self._lock = threading.Lock()

    @property
    def device(self) -> str:
        return self.capture.device

    @property
    def status(self) -> str:
        return self.capture.status

    @property
    def in_rate(self) -> int:
        return self.capture.in_rate

    def feed(self, samples: np.ndarray) -> None:
        # Built from the capture's rate on first use, not at construction:
        # a tap added before the device has said its rate would otherwise
        # filter 44.1 kHz audio as if it were 48.
        if self._dec is None or self._dec.in_rate != self.capture.in_rate:
            self._dec = dsp.Decimator(self.capture.in_rate, self.wanted_rate)
            self.out_rate = self._dec.out_rate
            self.block_n = max(32, int(self.out_rate * BLOCK_MS / 1000))
            self._acc = np.zeros(0, dtype=np.float32)
        out = self._dec(samples)
        if not len(out):
            return
        self.peak = float(np.abs(out).max())
        self._acc = np.concatenate([self._acc, out])
        now = time.time()
        while len(self._acc) >= self.block_n:
            chunk = self._acc[:self.block_n]
            self._acc = self._acc[self.block_n:]
            pcm = np.clip(chunk * 32767.0, -32768, 32767).astype("<i2")
            with self._lock:
                if len(self.blocks) == HISTORY:
                    self.first_seq += 1
                self.blocks.append(pcm.tobytes())
                self.stamps.append(now)
                self.seq += 1
                if len(self.blocks) == 1:
                    self.first_seq = self.seq

    def since(self, seq: int, limit: int = 32):
        """Blocks after `seq`, oldest first, with the first one's stamp."""
        with self._lock:
            if not self.blocks:
                return self.seq, self.seq, b"", 0.0
            start = max(seq + 1, self.first_seq)
            if start > self.seq:
                return self.seq, self.seq, b"", 0.0
            i = start - self.first_seq
            take = list(self.blocks)[i:i + limit]
            stamp = list(self.stamps)[i] if take else 0.0
            return start, start + len(take) - 1, b"".join(take), stamp

    def info(self) -> dict:
        c = self.capture
        return {"device": c.device, "status": c.status,
                "error": c.error, "hint": c.hint,
                "in_rate": c.in_rate, "rate": self.out_rate,
                "block_n": self.block_n, "block_ms": BLOCK_MS,
                "seq": self.seq, "first_seq": self.first_seq,
                "overflows": c.overflows,
                "peak": round(self.peak, 4), "now": time.time()}


def clamp_rate(rate) -> int:
    try:
        rate = int(rate or 12000)
    except (TypeError, ValueError):
        rate = 12000
    return max(MIN_RATE, min(MAX_RATE, rate))


class Hub:
    """Who wants which tap, and closing what nobody does.

    `source` makes captures from something other than PortAudio - the
    tests pass a tone generator. It is a function of the device name, so
    one fake can serve several "devices".
    """

    def __init__(self, source: Callable[[str], Callable | None] | None = None,
                 log: Callable[[str], None] = print) -> None:
        self._source = source
        self._log = log
        self._lock = threading.Lock()
        self.captures: dict[str, Capture] = {}
        self.taps: dict[str, Tap] = {}
        # "device|rate" -> {listener: when it last asked}. A listener is a
        # *page* or a card's feed, not a tile: every browser showing one
        # tile sends the same uid, so counting by tile made two browsers
        # one entry and the first Stop closed the device for both.
        self.wanted: dict[str, dict[str, float]] = {}

    @staticmethod
    def key(device: str, rate: int) -> str:
        return f"{device}|{rate}"

    def look(self, device: str, rate: int) -> Tap | None:
        """The tap for these settings if one is open, without opening one.

        Asking must not be the same as wanting. The page polls for a
        status line whether or not anybody is listening, and if that
        opened the input then pressing Stop would hand the sound card back
        for about two seconds before the next status poll took it again -
        which defeats the only reason to press Stop.
        """
        return self.taps.get(self.key(device, clamp_rate(rate)))

    def listeners(self, device: str) -> dict[str, list[str]]:
        """Who is on this device, by rate - what makes "is it shared, and
        with whom" answerable from outside, which is the first question
        when an input will not open."""
        with self._lock:
            return {str(t.wanted_rate): sorted(self.wanted.get(k, {}))
                    for k, t in self.taps.items() if t.device == device}

    def tap_for(self, device: str, rate: int, who: str) -> Tap:
        """The tap for these settings, opened if need be, and marked
        wanted by `who`. One capture per device whatever the rate."""
        rate = clamp_rate(rate)
        key = self.key(device, rate)
        now = time.time()
        stale: Capture | None = None
        with self._lock:
            self.wanted.setdefault(key, {})[who or "-"] = now
            cap = self.captures.get(device)
            if (cap is not None and cap.status == "error"
                    and now - cap.started_at > RETRY_SECONDS):
                # On a timer, not on every request: respinning a failed
                # input each time means the caller only ever sees a brand
                # new capture in its "starting" state and never the error
                # that says what is wrong. Its taps move to the new one,
                # so their sequence numbers carry on.
                stale, cap = cap, None
            if cap is None:
                src = self._source(device) if self._source else None
                cap = Capture(device, src)
                if stale is not None:
                    cap.taps = stale.taps
                    for t in cap.taps:
                        t.capture = cap
                self.captures[device] = cap.start()
            tap = self.taps.get(key)
            if tap is None:
                tap = self.taps[key] = Tap(cap, rate)
                cap.taps.append(tap)
        if stale is not None:
            stale.stop()
        return tap

    def release(self, who: str) -> list[str]:
        """One listener has stopped. Let go of the device now if nothing
        else wants it - the reason to press Stop is to give the sound
        card to another program, and in 20 seconds is not now.

        *If nothing else wants it*: another page, or a card's feed, at
        any rate on the same device keeps its own entry, so stopping here
        no longer stops them. Returns the devices actually closed.
        """
        gone = []
        with self._lock:
            for key, tiles in list(self.wanted.items()):
                if tiles.pop(who, None) is not None and not tiles:
                    gone.append(key)
            closing = self._drop(gone)
        return self._close(closing, "last listener left")

    def reap(self, now: float | None = None) -> list[str]:
        """Nothing tells a card that a tile was deleted, and this one holds
        the radio's sound card open."""
        now = time.time() if now is None else now
        gone = []
        with self._lock:
            for key in list(self.taps):
                tiles = self.wanted.get(key, {})
                for uid, when in list(tiles.items()):
                    if now - when > IDLE_SECONDS:
                        tiles.pop(uid, None)
                if not tiles:
                    gone.append(key)
            closing = self._drop(gone)
        return self._close(closing, f"nobody has asked for it in "
                                    f"{IDLE_SECONDS:.0f}s")

    def _drop(self, keys: list[str]) -> list[Capture]:
        """Take these taps off their captures; return the captures left
        with none. Under `_lock`."""
        closing = []
        for key in keys:
            self.wanted.pop(key, None)
            tap = self.taps.pop(key, None)
            if tap is None:
                continue
            cap = tap.capture
            if tap in cap.taps:
                cap.taps.remove(tap)
            if not cap.taps and self.captures.get(cap.device) is cap:
                # Popped before it is stopped, and stopped below without
                # fail: nothing else will ever hold a reference to it.
                self.captures.pop(cap.device)
                closing.append(cap)
        return closing

    def _close(self, closing: list[Capture], why: str) -> list[str]:
        # Outside the lock: the stop waits for the thread, which may be
        # waiting on PortAudio, and a waiting request must not hold up
        # every other listener's poll.
        for cap in closing:
            self._log(f"closing {cap.device or 'default input'}: {why}")
            cap.stop()
        return [c.device for c in closing]

    def close_all(self) -> None:
        with self._lock:
            closing = self._drop(list(self.taps))
        self._close(closing, "shutting down")


def _resolve_device(sd, wanted: str):
    """By name, never by index.

    PortAudio indices shuffle whenever anything is plugged in or a
    Bluetooth headset wakes, so a saved index silently becomes a
    different device - which presents as the card being deaf rather than
    as a settings problem.
    """
    if not wanted:
        return None                                 # the system default
    low = wanted.lower()
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and low in d["name"].lower():
            return i
    raise RuntimeError(f"no input device whose name contains {wanted!r}")
