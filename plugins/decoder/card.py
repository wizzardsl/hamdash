"""Decode digital modes from the radio's audio. CW to start with.

The decoding itself is in `cw.py`, which takes samples and returns text and
knows nothing about audio devices or HTTP - that is what lets
`tests/test_cw.py` drive it with synthesised Morse at known speeds and
signal-to-noise ratios. This file is the part that owns a sound card.

It follows the shape `plugins/spectrum` settled on, for the same reasons:

* **The page polls; nothing streams.** Every SSE stream through the host
  proxy holds a host server thread for its whole life (hamdash invariant 4),
  and text arrives a character at a time. The card keeps what it has decoded
  with a sequence number and the page asks for everything since its last one.

* **One listener per device, shared by tiles.** A sound card input can
  usually only be opened once, and two tiles pointed at the same radio want
  the same text anyway.

* **Nothing tells a card that a tile was deleted**, so a listener that nobody
  has asked for in `IDLE_SECONDS` is closed. Without that, changing the input
  leaks the old one and the radio's audio stays locked against other
  software. Wants are counted per tile rather than per device, so Stop on one
  tile can close the input at once while another tile on the same device
  keeps it - waiting out the idle timer would hold the radio's audio against
  WSJT-X for half a minute after the operator asked for it back.

* **A failed input is not respun on every request.** Doing that means the
  caller only ever sees a brand new listener in its "starting" state and
  never the error that says what is wrong.
"""
from __future__ import annotations

import threading
import time

import numpy as np
from flask import request

from hamdash.sdk import Card

import cw

card = Card(__file__)

BLOCK = 2048                    # samples per callback, ~43 ms at 48 kHz
KEEP_CHARS = 20000              # text held per listener, about a long ragchew
IDLE_SECONDS = 30.0
RETRY_SECONDS = 5.0

# What the mock sends. Long enough to watch the speed estimate settle and to
# show word spacing working, and a real exchange rather than lorem ipsum.
MOCK_TEXT = "CQ CQ DE VA7HDX VA7HDX K = RST 599 599 NAME STEVE = BK"
MOCK_WPM = 22.0

# Modes the dropdown offers. Only CW decodes; the rest are listed because a
# dropdown with one entry does not look like a choice, and leaving them out
# makes the card look finished when it is not. They are offered disabled, so
# nobody picks one and waits for text that is never coming.
MODES = [
    {"id": "cw", "name": "CW (Morse)", "ready": True},
    {"id": "rtty", "name": "RTTY", "ready": False},
    {"id": "psk31", "name": "PSK31", "ready": False},
    {"id": "ft8", "name": "FT8", "ready": False},
]

_lock = threading.Lock()
_listeners: dict[str, "Listener"] = {}
# device -> {tile uid: when it last asked}. Per tile, not per device: the
# question "may this input be closed" is "has every tile finished with it".
_wanted: dict[str, dict[str, float]] = {}
# PortAudio does not enjoy having its device list enumerated on one thread
# while another opens a stream: on Windows that can block for a long time or
# never come back. Everything touching the library goes through here.
_pa_lock = threading.Lock()


class Listener:
    """One audio input, decoded into a growing piece of text."""

    def __init__(self, device: str) -> None:
        self.device = device
        self.started_at = time.time()
        self.samplerate = 48000
        self.status = "starting"
        self.error = ""
        self.text = ""
        self.first_seq = 0          # sequence number of text[0]
        self.seq = 0                # one past the last character
        self.last_char_at = 0.0
        self._decoder = cw.CwDecoder(self.samplerate)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"decoder-{device}")
        self._thread.start()

    # -- capture -----------------------------------------------------------
    def _run(self) -> None:
        if self.device == "mock":
            self._run_mock()
            return
        try:
            import sounddevice as sd
        except Exception as exc:                        # noqa: BLE001
            self.status = "error"
            self.error = (f"sounddevice is not available ({exc}). Install "
                          f"it, or set this tile's input to the mock.")
            return
        try:
            with _pa_lock:
                index = _resolve_device(sd, self.device)
                info = sd.query_devices(index)
                self.samplerate = int(info["default_samplerate"])
                self._decoder = cw.CwDecoder(self.samplerate)
                stream = sd.InputStream(device=index, channels=1,
                                        dtype="float32",
                                        samplerate=self.samplerate,
                                        blocksize=BLOCK,
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
        self._feed(indata[:, 0].copy())

    def _run_mock(self) -> None:
        """Synthesised Morse, so the card works with no radio attached.

        The rig and the spectrum card default to mock for the same reason: a
        card that needs hardware to show anything cannot be developed, and
        cannot be looked at by somebody deciding whether they want it.
        """
        self.status = "running"
        audio = cw.keyed_audio(MOCK_TEXT, wpm=MOCK_WPM, tone_hz=620,
                               samplerate=self.samplerate, amplitude=0.4)
        rng = np.random.default_rng()
        at = 0
        while not self._stop.is_set():
            block = audio[at:at + BLOCK]
            at += BLOCK
            if at >= audio.size:
                at = 0              # round again, with a fresh gap between
            if block.size < BLOCK:
                block = np.concatenate(
                    (block, np.zeros(BLOCK - block.size, dtype=np.float32)))
            # A little noise, or the mock is an easier signal than anything
            # the decoder will ever meet and flatters itself.
            noisy = block + (0.012 * rng.standard_normal(BLOCK)
                             ).astype(np.float32)
            self._feed(noisy)
            time.sleep(BLOCK / self.samplerate)

    # -- decoding ----------------------------------------------------------
    def _feed(self, samples: np.ndarray) -> None:
        try:
            got = self._decoder.feed(samples)
        except Exception as exc:                        # noqa: BLE001
            # A decoder fault must not kill the capture thread and take the
            # sound card down with it.
            self.status = "error"
            self.error = f"decoding failed: {exc}"
            return
        if not got:
            return
        with _lock:
            self.text += got
            self.seq += len(got)
            if len(self.text) > KEEP_CHARS:
                drop = len(self.text) - KEEP_CHARS
                self.text = self.text[drop:]
            self.first_seq = self.seq - len(self.text)
            self.last_char_at = time.time()

    def since(self, seq: int) -> tuple[int, str]:
        """Everything decoded after `seq`, and where it actually starts.

        A page that has been asleep, or a tile added to a card that has been
        running for an hour, is told where the history begins rather than
        being handed a silent gap.
        """
        with _lock:
            start = max(seq, self.first_seq)
            return start, self.text[start - self.first_seq:]

    def info(self) -> dict:
        d = self._decoder
        return {"device": self.device, "status": self.status,
                "error": self.error, "samplerate": self.samplerate,
                "seq": self.seq, "first": self.first_seq,
                "wpm": d.wpm, "tone_hz": d.tone_hz, "snr_db": d.snr_db,
                "hearing": d.hearing,
                "quiet_for": round(time.time() - self.last_char_at, 1)
                if self.last_char_at else None}

    def clear(self) -> None:
        """Empty the text without touching the decoder's timing.

        Clearing is about the screen, not about the signal: making it reset
        the speed estimate would mean the next character after a Clear was
        decoded worse than the one before it.
        """
        with _lock:
            self.first_seq = self.seq
            self.text = ""

    def stop(self) -> None:
        self._stop.set()


def _resolve_device(sd, wanted: str):
    """Match an input by name, so a saved setting survives the index
    shuffling Windows does when something is plugged in."""
    if not wanted:
        return None                                  # the system default
    low = wanted.lower()
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and low in d["name"].lower():
            return i
    raise RuntimeError(f"no input device matching {wanted!r}")


def radio_audio() -> str:
    """The sound device the active radio says is its own."""
    try:
        return str(card.host.radio().get("audio_in") or "")
    except Exception:                                   # noqa: BLE001
        return ""


def resolve_source(conf: dict) -> tuple[str, str]:
    """(device, why) for a tile's chosen source.

    "radio" means whatever the rig in use is wired to, which is the setting
    worth having: it follows the operator switching radios without them
    having to remember that this card has its own idea of the input.
    """
    source = str(conf.get("source") or "mock")
    if source != "radio":
        return source, ""
    device = radio_audio()
    if device:
        return device, ""
    return "", ("The radio in use has no audio input set. "
                "Settings → Radio, or pick an input below.")


def listener_for(device: str, uid: str = "") -> Listener:
    key = device or ""
    now = time.time()
    _wanted.setdefault(key, {})[uid] = now
    li = _listeners.get(key)
    if li is None:
        li = _listeners[key] = Listener(key)
    elif not li._thread.is_alive() and now - li.started_at > RETRY_SECONDS:
        # Retry a failed input, but on a timer rather than on every request:
        # respinning it each time means the caller only ever sees a brand new
        # listener in its "starting" state and never the error.
        li = _listeners[key] = Listener(key)
    return li


def release(device: str, uid: str) -> bool:
    """This tile has finished with an input. True if it was closed.

    Pressing Stop is the operator asking for the sound card back, usually so
    that WSJT-X or fldigi can have it, so it is given back now rather than
    when the idle timer next runs.
    """
    key = device or ""
    _wanted.get(key, {}).pop(uid, None)
    if _wanted.get(key):
        return False                    # another tile is still listening
    _wanted.pop(key, None)
    li = _listeners.pop(key, None)
    if li is None:
        return False
    li.stop()
    print(f"closing {key or 'default input'}: stopped")
    return True


def _reap_idle() -> None:
    while True:
        time.sleep(5.0)
        now = time.time()
        for key in list(_listeners):
            tiles = _wanted.get(key, {})
            for uid, at in list(tiles.items()):
                if now - at > IDLE_SECONDS:
                    tiles.pop(uid, None)
            if tiles:
                continue
            # Nobody is asking any more: a tile was deleted, a board was
            # closed, or a page stopped polling without saying so.
            print(f"closing {key or 'default input'}: no tile has asked "
                  f"for it in {IDLE_SECONDS:.0f}s")
            _listeners.pop(key).stop()
            _wanted.pop(key, None)


# ---------------------------------------------------------------- routes
@card.get("/text")
def text():
    """Everything decoded since the caller's sequence number."""
    conf = card.settings()
    device, note = resolve_source(conf)
    if note:
        return {"text": "", "first": 0, "seq": 0, "status": "idle",
                "error": note, "device": "", "hearing": False,
                "mode": str(conf.get("mode") or "cw")}
    li = listener_for(device, card.tile_uid())
    try:
        seq = int(request.args.get("since", "0"))
    except ValueError:
        seq = 0
    first, chunk = li.since(seq)
    out = li.info()
    out.update({"first": first, "text": chunk,
                "mode": str(conf.get("mode") or "cw"),
                "source": str(conf.get("source") or "mock")})
    return out


@card.post("/clear")
def clear():
    conf = card.settings()
    device, note = resolve_source(conf)
    if note:
        return {"ok": True, "seq": 0}
    li = listener_for(device, card.tile_uid())
    li.clear()
    return {"ok": True, "seq": li.seq}


@card.post("/stop")
def stop():
    """This tile has stopped decoding.

    The page simply stops polling, which the reaper would notice eventually;
    this says so immediately, because the usual reason to press Stop is to
    give the sound card to another program.
    """
    conf = card.settings()
    device, note = resolve_source(conf)
    uid = card.tile_uid()
    closed = release(device, uid) if not note else False
    # Any other input this tile had open - it may have been switched while
    # running - is released too, or stopping leaks whatever it was on before.
    for other in list(_wanted):
        if other != (device or ""):
            release(other, uid)
    return {"ok": True, "closed": closed}


@card.get("/inputs")
def inputs():
    """What this tile can listen to.

    The radio's own input is offered first and by name, because that is the
    answer nearly everyone wants and the one that keeps working when they
    switch radios.
    """
    out = [{"id": "radio", "name": f"Radio's audio in{_radio_suffix()}"},
           {"id": "mock", "name": "Mock — synthesised CW"}]
    try:
        import sounddevice as sd
        with _pa_lock:
            found = list(enumerate(sd.query_devices()))
        seen = set()
        for _, d in found:
            if d["max_input_channels"] > 0 and d["name"] not in seen:
                seen.add(d["name"])
                out.append({"id": d["name"],
                            "name": " ".join(str(d["name"]).split())})
    except Exception as exc:                            # noqa: BLE001
        return {"inputs": out, "modes": MODES, "error": str(exc)}
    return {"inputs": out, "modes": MODES}


def _radio_suffix() -> str:
    device = radio_audio()
    return f" ({' '.join(device.split())})" if device else " (not set)"


if __name__ == "__main__":
    threading.Thread(target=_reap_idle, daemon=True,
                     name="decoder-reaper").start()
    card.run()
