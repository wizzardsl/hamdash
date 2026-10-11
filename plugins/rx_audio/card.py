"""Receive audio: the radio's audio, in the browser.

Mostly it is for listening to the radio from another machine on the
network, but it is just as useful on the same one - a tile that lets you
hear the band while the speaker is turned down.

How it moves audio is deliberately the same shape as the spectrum and
decoder cards: ring the recent audio, number it, and serve everything
after a sequence number. Polling rather than a stream, because a stream
holds a server thread for its whole life (invariant 4). Measured at the
far end: 50 blocks a second delivered against 50 produced, nothing
dropped, about 40 ms between a sample being captured and the browser
having it, 192 kbit/s at 12 kHz. That is what makes this possible without
WebRTC - which matters, because a frozen card runs inside the hamdash
binary, so a card's dependency would be in every build.

Receive only. Transmit needs a microphone, `getUserMedia` needs a secure
context, and nothing here is authenticated - see `docs/built-in-cards.md`.
"""
from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np
from flask import Response, request

from hamdash.sdk import Card

import dsp

card = Card(__file__)

BLOCK_MS = 20           # one numbered block of audio
HISTORY = 150           # blocks kept: 3 s, enough to ride out a stall
IDLE_SECONDS = 20.0     # close an input no tile has asked for
RETRY_SECONDS = 5.0     # before respinning a failed input

_lock = threading.Lock()
# PortAudio is not thread-safe across enumerate/open: one lock for both,
# the same rule the spectrum card follows.
_pa_lock = threading.Lock()
_taps: dict[str, "Tap"] = {}
# device -> {tile uid: when it last asked}. Per tile, not per device, so
# one tile closing its player does not take the audio away from another
# that is still listening to the same radio.
_wanted: dict[str, dict[str, float]] = {}


class Tap:
    """One audio input, decimated and ringed as numbered int16 blocks."""

    def __init__(self, device: str, rate: int) -> None:
        self.device = device
        self.wanted_rate = rate
        self.started_at = time.time()
        self.in_rate = 48000
        self.out_rate = rate
        self.block_n = max(32, int(rate * BLOCK_MS / 1000))
        self.status = "starting"
        self.error = ""
        self.hint = ""
        self.seq = 0
        self.first_seq = 0
        self.peak = 0.0                    # for the level meter
        self.blocks: deque[bytes] = deque(maxlen=HISTORY)
        self.stamps: deque[float] = deque(maxlen=HISTORY)
        self.overflows = 0
        self._dec: dsp.Decimator | None = None
        self._acc = np.zeros(0, dtype=np.float32)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"rx-{device or 'default'}")
        self._thread.start()

    # -- capture ---------------------------------------------------------
    def _run(self) -> None:
        if self.device == "mock":
            self._run_mock()
            return
        try:
            import sounddevice as sd
        except Exception as exc:                        # noqa: BLE001
            return self._fail(
                f"sounddevice is not installed ({exc}).",
                "Set this tile's input to 'mock' to try the card without a "
                "radio, or install the sounddevice package.")
        try:
            with _pa_lock:
                index = _resolve_device(sd, self.device)
                info = sd.query_devices(index)
                self.in_rate = int(info["default_samplerate"])
                self._start_dec()
                stream = sd.InputStream(
                    device=index, channels=1, dtype="float32",
                    samplerate=self.in_rate,
                    blocksize=int(self.in_rate * BLOCK_MS / 1000),
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
            # Naming the likely cause matters more here than anywhere: a
            # sound card can only be held by one program at a time on
            # Windows, and "the card is deaf" is what the operator sees.
            self._fail(
                f"could not open {self.device or 'the default input'}: {exc}",
                "Another program may be holding that input - a spectrum or "
                "decoder tile here, WSJT-X, or fldigi. Close it, or choose "
                "a different input in this tile's settings.")

    def _fail(self, why: str, hint: str = "") -> None:
        self.status = "error"
        self.error = why
        self.hint = hint

    def _start_dec(self) -> None:
        self._dec = dsp.Decimator(self.in_rate, self.wanted_rate)
        self.out_rate = self._dec.out_rate
        self.block_n = max(32, int(self.out_rate * BLOCK_MS / 1000))

    def _on_audio(self, indata, frames, time_info, status) -> None:
        if status and getattr(status, "input_overflow", False):
            self.overflows += 1
        self._feed(indata[:, 0].copy())

    def _run_mock(self) -> None:
        """A tone that sweeps, with a click on each whole second.

        Mock by default like the rig, so the card can be set up, placed on
        a board and listened to with no radio attached. The click is also
        what makes latency measurable, which is how the transport was
        proven in the first place.
        """
        self.in_rate = 48000
        self._start_dec()
        self.status = "running"
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
            self._feed(block.astype(np.float32))
            nxt += n / self.in_rate
            time.sleep(max(0.0, nxt - time.time()))

    # -- ring ------------------------------------------------------------
    def _feed(self, samples: np.ndarray) -> None:
        if self._dec is None:
            return
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
            with _lock:
                if len(self.blocks) == HISTORY:
                    self.first_seq += 1
                self.blocks.append(pcm.tobytes())
                self.stamps.append(now)
                self.seq += 1
                if len(self.blocks) == 1:
                    self.first_seq = self.seq

    def since(self, seq: int, limit: int = 32):
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
        return {"device": self.device, "status": self.status,
                "error": self.error, "hint": self.hint,
                "in_rate": self.in_rate, "rate": self.out_rate,
                "block_n": self.block_n, "block_ms": BLOCK_MS,
                "seq": self.seq, "first_seq": self.first_seq,
                "overflows": self.overflows,
                "peak": round(self.peak, 4), "now": time.time()}

    def stop(self) -> None:
        self._stop.set()


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


def _device_for(conf: dict) -> str:
    """'rig' follows the active radio's own audio input.

    That setting already exists per rig and already rides on radio.state,
    so this needs no second answer to "which sound card is the radio's" -
    and changing radios moves the audio with it.
    """
    want = str(conf.get("device", "mock")).strip()
    if want != "rig":
        return want
    try:
        return str((card.host.radio() or {}).get("audio_in") or "")
    except Exception:                                   # noqa: BLE001
        return ""


def _key_for(conf: dict) -> tuple[str, int]:
    device = _device_for(conf)
    try:
        rate = int(conf.get("rate", 12000) or 12000)
    except (TypeError, ValueError):
        rate = 12000
    return f"{device}|{rate}", rate


def look(conf: dict) -> Tap | None:
    """The tap for these settings if one is open, without opening one.

    Asking must not be the same as wanting. The page polls for a status
    line whether or not anybody is listening, and if that opened the
    input then pressing Stop would hand the sound card back for about two
    seconds before the next status poll took it again - which defeats the
    only reason to press Stop.
    """
    return _taps.get(_key_for(conf)[0])


def tap_for(conf: dict, uid: str) -> Tap:
    """The tap for these settings, opened if need be, and marked wanted."""
    key, rate = _key_for(conf)
    device = key.rsplit("|", 1)[0]
    now = time.time()
    _wanted.setdefault(key, {})[uid or "-"] = now
    t = _taps.get(key)
    if t is None:
        t = _taps[key] = Tap(device, rate)
    elif t.status == "error" and now - t.started_at > RETRY_SECONDS:
        # On a timer, not on every request: respinning a failed input each
        # time means the caller only ever sees a brand new tap in its
        # "starting" state and never the error that says what is wrong.
        t = _taps[key] = Tap(device, rate)
    return t


def release(uid: str) -> None:
    """A tile has stopped listening. Let go of the device now if nothing
    else wants it - the reason to press Stop is to give the sound card to
    another program, and in 20 seconds is not now."""
    for key, tiles in list(_wanted.items()):
        if tiles.pop(uid, None) is not None and not tiles:
            t = _taps.pop(key, None)
            if t is not None:
                print(f"closing {key or 'default input'}: last listener left")
                t.stop()
            _wanted.pop(key, None)


def _reap_idle() -> None:
    """Nothing tells a card that a tile was deleted, and this one holds
    the radio's sound card open."""
    while True:
        time.sleep(5.0)
        now = time.time()
        for key in list(_taps):
            tiles = _wanted.get(key, {})
            for uid, when in list(tiles.items()):
                if now - when > IDLE_SECONDS:
                    tiles.pop(uid, None)
            if not tiles:
                print(f"closing {key or 'default input'}: no tile has asked "
                      f"for it in {IDLE_SECONDS:.0f}s")
                _taps.pop(key).stop()
                _wanted.pop(key, None)


# ---------------------------------------------------------------- routes
@card.get("/info")
def info():
    """What the input is doing - without starting it. See `look`."""
    conf = card.settings()
    t = look(conf)
    if t is not None:
        return t.info()
    device, rate = _device_for(conf), _key_for(conf)[1]
    return {"status": "idle", "device": device, "error": "", "hint": "",
            "rate": rate, "in_rate": 0, "block_n": 0, "block_ms": BLOCK_MS,
            "seq": 0, "first_seq": 0, "overflows": 0, "peak": 0.0,
            "now": time.time()}


@card.get("/pcm")
def pcm():
    """Every block after `since`, as one lump of little-endian int16.

    Binary rather than JSON: a second of audio at 12 kHz is 24 kB, and
    base64 would spend 32 kB saying it. What the caller needs to place it
    in time rides in headers.
    """
    t = tap_for(card.settings(), card.tile_uid())
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
    r.headers["X-Captured-At"] = f"{stamp:.6f}"
    r.headers["X-Server-Now"] = f"{time.time():.6f}"
    r.headers["X-Status"] = t.status
    r.headers["X-Peak"] = f"{t.peak:.4f}"
    r.headers["Cache-Control"] = "no-store"
    return r


@card.post("/stop")
def stop():
    """This tile has stopped listening."""
    release(card.tile_uid())
    return {"ok": True}


@card.get("/devices")
def devices():
    """The host's folded, cached list rather than a second enumeration -
    Windows lists one sound card several times and the host already knows
    how to collapse that."""
    try:
        return {"ok": True, "devices": card.host.audio_devices()}
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": str(exc), "devices": []}


threading.Thread(target=_reap_idle, daemon=True, name="reap").start()
card.run()
