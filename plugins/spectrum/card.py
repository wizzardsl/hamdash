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

* **One capture per device, shared by tiles.** A sound card input can usually
  only be opened once, and two tiles pointed at the same device want the same
  audio anyway. Analysis is shared; contrast, span and palette are per tile
  and applied in the browser, so two tiles can look different without
  reopening anything.

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

from hamdash.sdk import Card

card = Card(__file__)

FFT_N = 2048            # ~23 Hz bins at 48 kHz
HOP = 2048              # no overlap: ~23 rows/second, which is plenty
HISTORY = 400           # rows kept per analyser (~17 s)
DB_FLOOR = -140.0       # of the uint8 encoding, not of the display
DB_RANGE = 140.0

_lock = threading.Lock()
_analysers: dict[str, "Analyser"] = {}
_wanted: dict[str, float] = {}      # device -> last time a tile asked for it
IDLE_SECONDS = 30.0
RETRY_SECONDS = 5.0

# PortAudio does not enjoy having its device list enumerated on one thread
# while another opens a stream: on Windows that can block for a long time or
# not come back at all. Everything that touches the library goes through here.
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
        try:
            import sounddevice as sd
        except Exception as exc:                        # noqa: BLE001
            self.status = "error"
            self.error = (f"sounddevice is not available ({exc}). "
                          f"Install it, or set this tile's input to 'mock'.")
            return
        try:
            with _pa_lock:
                index = _resolve_device(sd, self.device)
                info = sd.query_devices(index)
                self.samplerate = int(info["default_samplerate"])
                stream = sd.InputStream(device=index, channels=1,
                                        dtype="float32",
                                        samplerate=self.samplerate,
                                        blocksize=HOP,
                                        callback=self._on_audio)
                stream.start()
            try:
                self.status = "running"
                while not self._stop.wait(0.25):
                    pass
            finally:
                with _pa_lock:
                    stream.stop()
                    stream.close()
            self.status = "stopped"
        except Exception as exc:                        # noqa: BLE001
            self.status = "error"
            self.error = (f"could not open {self.device or 'the default input'}"
                          f": {exc}. Pick another input in this tile's "
                          f"settings, or check whether another program has it.")

    def _on_audio(self, indata, frames, time_info, status) -> None:
        # status is a sounddevice CallbackFlags; overflow just means we were
        # slow, and dropping old audio is the right answer for a waterfall.
        self._feed(indata[:, 0].copy())

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
        while len(self._buf) >= FFT_N:
            frame = self._buf[:FFT_N] * self._window
            self._buf = self._buf[HOP:]
            spec = np.abs(np.fft.rfft(frame)) / FFT_N
            db = 20.0 * np.log10(np.maximum(spec, 1e-12))
            with _lock:
                self.rows.append(_to_u8(db))
                self.seq += 1
                self.first_seq = self.seq - len(self.rows) + 1
            idx = int(np.argmax(db[1:]) + 1)
            self.peak_hz = idx * self.samplerate / FFT_N
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
                "bin_hz": self.samplerate / FFT_N, "bins": FFT_N // 2 + 1,
                "seq": self.seq, "db_floor": DB_FLOOR, "db_range": DB_RANGE,
                "peak_hz": round(self.peak_hz, 1),
                "peak_db": round(self.peak_db, 1)}

    def stop(self) -> None:
        self._stop.set()


def _resolve_device(sd, wanted: str):
    """Match a device by name fragment, so a saved setting survives the index
    shuffling that Windows does when something is plugged in."""
    if not wanted:
        return None                                  # default input
    if wanted.isdigit():
        return int(wanted)
    low = wanted.lower()
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and low in d["name"].lower():
            return i
    raise RuntimeError(f"no input device matching {wanted!r}")


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
