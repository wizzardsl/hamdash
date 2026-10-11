"""SPIKE: the radio's receive audio, in a browser, over the ordinary card API.

The question this exists to answer is narrow: **can audio reach a LAN
browser by polling, through the host's card proxy, with no new dependency,
no new open port and no held server thread?** If it can, remote listening
costs the architecture nothing - no WebRTC, no WebSocket, no aiortc in
everybody's PyInstaller build (a frozen card runs inside the hamdash
binary, so a card's dependency is every user's dependency).

So this deliberately uses the shape the audio cards already use: ring the
recent audio, number it, serve everything since a sequence number. The page
schedules what arrives with Web Audio. Nothing holds a thread in the host
(invariant 4) and nothing is new to the build.

What it measures, because "it sounds fine" is not a finding:

  - capture-to-ear latency, from a timestamp carried on every block
  - underruns, where the player had nothing to play
  - gaps, where blocks fell out of the ring before the page asked
  - what it costs in kbit/s at each send rate

The mock source carries a click on every whole second, so latency can be
measured with no radio and no sound card at all - and measured against the
clock rather than against an impression.

NOT a feature. Receive only. No authentication, no access control.
"""
from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np
from flask import Response, request

from hamdash.sdk import Card

card = Card(__file__)

HISTORY = 400           # blocks kept; at 20 ms that is 8 s of catch-up
IDLE_SECONDS = 20.0     # close an input nobody has asked for
RETRY_SECONDS = 5.0

_lock = threading.Lock()
# PortAudio is not thread-safe across enumerate/open - the same lock the
# spectrum card takes, for the same reason.
_pa_lock = threading.Lock()
_taps: dict[str, "Tap"] = {}
_wanted: dict[str, float] = {}


class Tap:
    """One audio input, decimated and ringed as numbered int16 blocks."""

    def __init__(self, device: str, rate: int, block_ms: int) -> None:
        self.device = device
        self.out_rate = rate
        self.block_ms = block_ms
        self.block_n = max(32, int(rate * block_ms / 1000))
        self.started_at = time.time()
        self.in_rate = 48000
        self.status = "starting"
        self.error = ""
        self.seq = 0                       # sequence of the newest block
        self.first_seq = 0
        self.blocks: deque[bytes] = deque(maxlen=HISTORY)
        self.stamps: deque[float] = deque(maxlen=HISTORY)
        self.overflows = 0                 # PortAudio said it dropped input
        self._acc = np.zeros(0, dtype=np.float32)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"tap-{device}")
        self._thread.start()

    # -- capture ---------------------------------------------------------
    def _run(self) -> None:
        if self.device == "mock":
            self._run_mock()
            return
        try:
            import sounddevice as sd
        except Exception as exc:                        # noqa: BLE001
            self._fail(f"sounddevice is not available ({exc}). Set this "
                       f"tile's input to 'mock'.")
            return
        try:
            with _pa_lock:
                index = _resolve_device(sd, self.device)
                info = sd.query_devices(index)
                self.in_rate = int(info["default_samplerate"])
                stream = sd.InputStream(
                    device=index, channels=1, dtype="float32",
                    samplerate=self.in_rate,
                    blocksize=int(self.in_rate * self.block_ms / 1000),
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
            self._fail(f"could not open {self.device or 'the default input'}"
                       f": {exc}. Pick another input, or check whether "
                       f"another program is holding it.")

    def _fail(self, why: str) -> None:
        self.status = "error"
        self.error = why

    def _on_audio(self, indata, frames, time_info, status) -> None:
        if status and getattr(status, "input_overflow", False):
            self.overflows += 1
        self._feed(indata[:, 0].copy())

    def _run_mock(self) -> None:
        """A 700 Hz tone with a click on every whole second.

        The click is the instrument: it happens at a time both ends agree
        on, so what the page hears can be compared with when it was made.
        A tone alone proves audio arrives and says nothing about when.
        """
        self.status = "running"
        self.in_rate = 48000
        n = int(self.in_rate * self.block_ms / 1000)
        phase = 0.0
        nxt = time.time()
        while not self._stop.is_set():
            t0 = time.time()
            t = np.arange(n, dtype=np.float32) / self.in_rate
            block = 0.25 * np.sin(2 * np.pi * 700.0 * t + phase)
            phase = (phase + 2 * np.pi * 700.0 * n / self.in_rate) % (2 * np.pi)
            # A click wherever this block straddles a whole second.
            edge = np.ceil(t0) - t0
            if 0 <= edge < n / self.in_rate:
                i = int(edge * self.in_rate)
                block[i:i + 240] = 0.9          # 5 ms at 48 kHz
            self._feed(block.astype(np.float32))
            nxt += n / self.in_rate
            time.sleep(max(0.0, nxt - time.time()))

    # -- ring ------------------------------------------------------------
    def _feed(self, samples: np.ndarray) -> None:
        """Decimate to the send rate and cut into numbered blocks.

        Decimation is a box average over whole samples, which is a crude
        low-pass: good enough to answer the transport question and NOT good
        enough to ship. A real one needs a proper anti-alias filter, or the
        hiss above the cut folds back down into the passband.
        """
        step = max(1, int(round(self.in_rate / self.out_rate)))
        if step > 1:
            usable = (len(samples) // step) * step
            samples = (samples[:usable].reshape(-1, step).mean(axis=1)
                       if usable else samples[:0])
        self._acc = np.concatenate([self._acc, samples.astype(np.float32)])
        now = time.time()
        while len(self._acc) >= self.block_n:
            chunk = self._acc[:self.block_n]
            self._acc = self._acc[self.block_n:]
            pcm = np.clip(chunk * 32767.0, -32768, 32767).astype("<i2")
            with _lock:
                if len(self.blocks) == HISTORY:
                    self.first_seq += 1
                self.blocks.append(pcm.tobytes())
                self.stamps.append(now)
                self.seq += 1
                if len(self.blocks) == 1:
                    self.first_seq = self.seq

    def since(self, seq: int, limit: int = 64):
        """Blocks after `seq`, oldest first, with the first one's stamp."""
        with _lock:
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
        with _lock:
            held = len(self.blocks)
        return {"device": self.device, "status": self.status,
                "error": self.error, "in_rate": self.in_rate,
                "rate": self.out_rate, "block_n": self.block_n,
                "block_ms": self.block_ms, "seq": self.seq,
                "first_seq": self.first_seq, "held": held,
                "overflows": self.overflows, "now": time.time()}

    def stop(self) -> None:
        self._stop.set()


def _resolve_device(sd, wanted: str):
    if not wanted:
        return None
    if wanted.isdigit():
        return int(wanted)
    low = wanted.lower()
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and low in d["name"].lower():
            return i
    raise RuntimeError(f"no input device matching {wanted!r}")


def _device_for(conf: dict) -> str:
    """'rig' follows whatever the active radio's audio input is set to.

    That already rides on radio.state per rig, so this needs no second copy
    of "which sound card is this radio's".
    """
    want = str(conf.get("device", "mock")).strip()
    if want != "rig":
        return want
    try:
        st = card.host.radio() or {}
        return str(st.get("audio_in") or "")
    except Exception:                                   # noqa: BLE001
        return ""


def tap_for(conf: dict) -> Tap:
    device = _device_for(conf)
    rate = int(conf.get("rate", 12000) or 12000)
    block_ms = max(5, int(conf.get("block_ms", 20) or 20))
    key = f"{device}|{rate}|{block_ms}"
    now = time.time()
    _wanted[key] = now
    t = _taps.get(key)
    if t is None:
        t = _taps[key] = Tap(device, rate, block_ms)
    elif not t._thread.is_alive() and now - t.started_at > RETRY_SECONDS:
        # Not on every request: respinning a failed input means the caller
        # only ever sees a brand new one in its "starting" state and never
        # the error that says what is wrong.
        t = _taps[key] = Tap(device, rate, block_ms)
    return t


def _reap_idle() -> None:
    """Nothing tells a card that a tile was deleted, and this one holds the
    radio's sound card. Same rule as the spectrum card."""
    while True:
        time.sleep(5.0)
        now = time.time()
        for key in list(_taps):
            if now - _wanted.get(key, 0) > IDLE_SECONDS:
                print(f"closing {key}: nobody has asked for it in "
                      f"{IDLE_SECONDS:.0f}s")
                _taps.pop(key).stop()
                _wanted.pop(key, None)


@card.get("/info")
def info():
    return tap_for(card.settings()).info()


@card.get("/pcm")
def pcm():
    """Every block after `since`, as one lump of little-endian int16.

    Binary rather than JSON: at 12 kHz a second of audio is 24 kB, and
    base64 would spend 32 kB saying the same thing. What a caller needs to
    place it in time rides in headers.
    """
    t = tap_for(card.settings())
    try:
        seq = int(request.args.get("since", "0"))
    except ValueError:
        seq = 0
    first, last, body, stamp = t.since(seq)
    r = Response(body, mimetype="application/octet-stream")
    r.headers["X-First-Seq"] = str(first)
    r.headers["X-Last-Seq"] = str(last)
    r.headers["X-Head-Seq"] = str(t.seq)
    r.headers["X-Rate"] = str(t.out_rate)
    r.headers["X-Block-N"] = str(t.block_n)
    r.headers["X-Captured-At"] = f"{stamp:.6f}"     # of the FIRST block here
    r.headers["X-Server-Now"] = f"{time.time():.6f}"
    r.headers["X-Status"] = t.status
    r.headers["Cache-Control"] = "no-store"
    return r


@card.get("/devices")
def devices():
    """The host's folded, cached list - not a second enumeration here."""
    try:
        return {"ok": True, "devices": card.host.audio_devices()}
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": str(exc), "devices": []}


threading.Thread(target=_reap_idle, daemon=True, name="reap").start()
card.run()
