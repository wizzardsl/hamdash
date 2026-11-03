"""Spectrum and waterfall of the receiver's audio.

Feed the radio's audio into a sound card input (a USB codec, or line-in) and
this draws what is in the passband: a live spectrum trace and a scrolling
waterfall underneath it.

Design notes that are not obvious:

* **The page polls; nothing streams.** A waterfall wants ~20 rows a second,
  and every SSE stream through the host proxy holds a host server thread for
  its whole life (hamdash invariant 4). So the card keeps a ring of recent
  rows and hands over every row since the client's last sequence number in one
  request. The page polls a few times a second and still renders every row.

* **It never opens a sound card.** The `rx_audio` engine does, once per
  device, and this reads from it with `AudioFeed` - so the waterfall,
  the decoder and Listen can all have the radio's input at once, which
  PortAudio on Windows would not allow three openers. Only the mock is
  synthesised here. Analysis is still one per device, shared by tiles;
  contrast, span and palette are per tile and applied in the browser.

* **Rows are bytes, not numbers.** A row is `BINS` uint8 values base64'd -
  about 200 characters. As a JSON array of floats the same row is 10x that,
  20 times a second, per tile.
"""
from __future__ import annotations

import base64
import threading
import time
from collections import deque

import numpy as np
from flask import request

from hamdash.sdk import AudioFeed, Card

card = Card(__file__)

FFT_N = 2048            # ~23 Hz bins at 48 kHz; scaled to keep them so
HOP = 2048              # no overlap: ~23 rows/second, which is plenty
# What the waterfall asks the engine for. 24 kHz passes 8.6 kHz alias-free,
# more than any receiver's audio and any `max_hz` worth setting, at half
# the arithmetic of 48. A different rate from Listen's 12 kHz is a second
# filter on the same capture, not a second open.
FEED_RATE = 24000
HISTORY = 400           # rows kept per analyser (~17 s)
DB_FLOOR = -140.0       # of the uint8 encoding, not of the display
DB_RANGE = 140.0

_lock = threading.Lock()
_analysers: dict[str, "Analyser"] = {}
_wanted: dict[str, float] = {}      # device -> last time a tile asked for it
IDLE_SECONDS = 30.0
RETRY_SECONDS = 5.0

# PortAudio does not enjoy having its device list enumerated on one thread
# while another opens a stream. Nothing here opens one any more - the engine
# does - but the /devices list still enumerates, so it keeps its lock.
_pa_lock = threading.Lock()


def _to_u8(db: np.ndarray) -> np.ndarray:
    """dB to a byte, so a row is 2 kB of JSON instead of 20 kB.

    Fixed scale on purpose: the client applies floor/ceiling itself, so two
    tiles can have different contrast without the card computing a row twice.
    """
    return np.clip((db - DB_FLOOR) * (255.0 / DB_RANGE), 0, 255).astype(np.uint8)


class Analyser:
    """One audio input, FFT'd into a ring of waterfall rows."""

    def __init__(self, device: str) -> None:
        self.device = device
        self.started_at = time.time()
        self.rows: deque[np.ndarray] = deque(maxlen=HISTORY)
        self.first_seq = 0          # sequence number of rows[0]
        self.seq = 0                # sequence of the newest row
        self.samplerate = 48000
        self.status = "starting"
        self.error = ""
        self.peak_hz = 0.0
        self.peak_db = -999.0
        self._buf = np.zeros(0, dtype=np.float32)
        self.fft_n = FFT_N
        self._window = np.hanning(FFT_N).astype(np.float32)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"spectrum-{device}")
        self._thread.start()

    # -- capture ---------------------------------------------------------
    def _run(self) -> None:
        if self.device == "mock":
            self._run_mock()
            return
        # Not opened here. The radio's input is opened once, by the
        # rx_audio engine, and read from it - so this waterfall, the
        # decoder and Listen can all have the same sound card at once,
        # which PortAudio on Windows would not allow three openers.
        feed = AudioFeed(card.host, self.device, FEED_RATE, self._on_feed,
                         who=f"spectrum-{id(self):x}").start()
        try:
            while not self._stop.wait(0.25):
                self.status = feed.status or "starting"
                self.error = (f"{feed.error} {feed.hint}".strip()
                              if feed.status == "error" else "")
        finally:
            feed.stop()
        self.status = "stopped"

    def _on_feed(self, samples: np.ndarray, rate: int) -> None:
        if rate and rate != self.samplerate:
            self._resize(rate)
        self._feed(samples)

    def _resize(self, rate: int) -> None:
        """Bins of the same width whatever rate the engine produced, so
        the page's arithmetic (`bin_hz`) is all that changes."""
        self.samplerate = int(rate)
        n = max(256, int(round(rate * FFT_N / 48000)))
        self.fft_n = 1 << (n - 1).bit_length() if n & (n - 1) else n
        self._window = np.hanning(self.fft_n).astype(np.float32)
        self._buf = np.zeros(0, dtype=np.float32)

    def _run_mock(self) -> None:
        """A synthetic signal, so the card is usable with no radio attached.

        The rig defaults to mock mode for the same reason. Two drifting
        carriers and a burst of noise: enough to see that the display,
        scaling and scrolling all work.
        """
        self.status = "running"
        self.samplerate = 48000
        t0 = time.time()
        phase = 0.0
        while not self._stop.is_set():
            n = HOP
            t = np.arange(n, dtype=np.float32) / self.samplerate
            age = time.time() - t0
            f1 = 700 + 120 * np.sin(age * 0.30)
            f2 = 1500 + 400 * np.sin(age * 0.11)
            sig = (0.28 * np.sin(2 * np.pi * f1 * t + phase)
                   + 0.16 * np.sin(2 * np.pi * f2 * t + phase * 1.7)
                   + 0.02 * np.random.randn(n).astype(np.float32))
            if int(age) % 7 == 0:               # an intermittent third signal
                sig += 0.10 * np.sin(2 * np.pi * 2400 * t)
            phase = (phase + 2 * np.pi * f1 * n / self.samplerate) % (2 * np.pi)
            self._feed(sig.astype(np.float32))
            time.sleep(n / self.samplerate)

    # -- analysis --------------------------------------------------------
    def _feed(self, samples: np.ndarray) -> None:
        self._buf = np.concatenate((self._buf, samples))
        n = self.fft_n                  # no overlap: the hop is the frame
        while len(self._buf) >= n:
            frame = self._buf[:n] * self._window
            self._buf = self._buf[n:]
            spec = np.abs(np.fft.rfft(frame)) / n
            db = 20.0 * np.log10(np.maximum(spec, 1e-12))
            with _lock:
                self.rows.append(_to_u8(db))
                self.seq += 1
                self.first_seq = self.seq - len(self.rows) + 1
            idx = int(np.argmax(db[1:]) + 1)
            self.peak_hz = idx * self.samplerate / n
            self.peak_db = float(db[idx])

    def since(self, seq: int, limit: int = 60,
              bins: int = 0) -> tuple[int, list[str]]:
        """Rows newer than `seq`, oldest first, plus the sequence of the first
        one returned. A client that falls behind the ring is told where the
        history actually starts rather than being handed a silent gap."""
        with _lock:
            if not self.rows:
                return self.seq + 1, []
            start = max(seq + 1, self.first_seq)
            offset = start - self.first_seq
            wanted = list(self.rows)[offset:][-limit:]
            first = self.seq - len(wanted) + 1
        # Only send the bins the tile actually shows. A full row is 1025
        # bytes and an SSB display uses about 150 of them; at six rows a poll
        # that is the difference between 12 kB and 82 kB per request, per tile.
        cut = bins or None
        return first, [base64.b64encode(r[:cut].tobytes()).decode("ascii")
                       for r in wanted]

    def auto_scale(self) -> tuple[float, float]:
        """Contrast that suits whatever is actually arriving.

        Sound card gain varies by tens of dB between a USB codec, a line
        input and whatever the operator left the mixer at, so a fixed floor
        is wrong on most machines. Sit the floor just under the noise and the
        ceiling just over the strongest bin, from recent history.
        """
        with _lock:
            sample = list(self.rows)[-40:]
        if not sample:
            return -110.0, -35.0
        flat = np.concatenate(sample)
        lo = float(np.percentile(flat, 30)) * (DB_RANGE / 255.0) + DB_FLOOR
        hi = float(np.percentile(flat, 99.7)) * (DB_RANGE / 255.0) + DB_FLOOR
        if hi - lo < 12:                  # a dead-quiet input: keep it legible
            hi = lo + 12
        return round(lo - 2, 1), round(hi + 3, 1)

    def info(self) -> dict:
        auto_floor, auto_ceil = self.auto_scale()
        return {"auto_floor": auto_floor, "auto_ceil": auto_ceil,
                "device": self.device, "status": self.status,
                "error": self.error, "samplerate": self.samplerate,
                "bin_hz": self.samplerate / self.fft_n,
                "bins": self.fft_n // 2 + 1,
                "seq": self.seq, "db_floor": DB_FLOOR, "db_range": DB_RANGE,
                "peak_hz": round(self.peak_hz, 1),
                "peak_db": round(self.peak_db, 1)}

    def stop(self) -> None:
        self._stop.set()


def analyser_for(device: str) -> Analyser:
    """The analyser for a device, started on demand.

    Tiles come and go and change device, and an analyser holds the sound card
    open for as long as it lives. Nothing tells this card that a tile was
    deleted, so instead every request marks its device as wanted and the
    reaper closes anything nobody has asked for recently. Without that, the
    radio's audio input stays locked against other software for the life of
    the process.
    """
    key = device or ""
    now = time.time()
    _wanted[key] = now
    a = _analysers.get(key)
    if a is None:
        a = _analysers[key] = Analyser(key)
    elif not a._thread.is_alive() and now - a.started_at > RETRY_SECONDS:
        # Retry a failed input, but not on every request: respinning it each
        # time means the caller only ever sees a brand new analyser in its
        # "starting" state and never the error that says what is wrong.
        a = _analysers[key] = Analyser(key)
    return a


def _reap_idle() -> None:
    while True:
        time.sleep(5.0)
        now = time.time()
        for key in list(_analysers):
            if now - _wanted.get(key, 0) > IDLE_SECONDS:
                print(f"closing {key or 'default input'}: no tile has asked "
                      f"for it in {IDLE_SECONDS:.0f}s")
                _analysers.pop(key).stop()
                _wanted.pop(key, None)


# ---------------------------------------------------------------- routes
@card.get("/frames")
def frames():
    """Every row since the caller's sequence number, in one response."""
    conf = card.settings()
    a = analyser_for(str(conf.get("device", "")))
    try:
        seq = int(request.args.get("since", "0"))
    except ValueError:
        seq = 0
    try:
        bins = max(0, int(request.args.get("bins", "0")))
    except ValueError:
        bins = 0
    first, rows = a.since(seq, bins=bins)
    out = a.info()
    out.update({"first": first, "rows": rows})
    return out


def _publish_peaks() -> None:
    """Announce the strongest audio tone, about once a second.

    Deliberately slow and deliberately quiet: a waterfall produces 23 rows a
    second and putting that on the shared bus would drown every other
    subscriber. Only a meaningful move is worth an event.
    """
    last_hz, last_at = 0.0, 0.0
    while True:
        time.sleep(1.0)
        for a in list(_analysers.values()):
            if a.status != "running" or a.peak_db < -90:
                continue
            now = time.time()
            if abs(a.peak_hz - last_hz) < 50 and now - last_at < 15:
                continue
            last_hz, last_at = a.peak_hz, now
            card.publish("spectrum.peak", {"hz": round(a.peak_hz, 1),
                                           "db": round(a.peak_db, 1),
                                           "device": a.device})
            break


@card.get("/devices")
def devices():
    out = [{"id": "mock", "name": "mock - synthesised test signal"}]
    try:
        import sounddevice as sd
        with _pa_lock:
            found = list(enumerate(sd.query_devices()))
        for i, d in found:
            if d["max_input_channels"] > 0:
                out.append({"id": d["name"], "name": f"{i}: {d['name']}"})
    except Exception as exc:                            # noqa: BLE001
        return {"devices": out, "error": str(exc)}
    return {"devices": out}


@card.get("/state")
def state():
    conf = card.settings()
    return analyser_for(str(conf.get("device", ""))).info()


if __name__ == "__main__":
    threading.Thread(target=_publish_peaks, daemon=True,
                     name="spectrum-peaks").start()
    threading.Thread(target=_reap_idle, daemon=True,
                     name="spectrum-reaper").start()
    card.run()
