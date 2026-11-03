"""Decode digital modes from the radio's audio: CW and RTTY.

The decoding itself is in `cw.py` and `rtty.py`, which take samples and
return text and know nothing about audio devices or HTTP - that is what lets
`tests/test_cw.py` and `tests/test_rtty.py` drive them with synthesised
signals at known speeds, offsets and signal-to-noise ratios. This file is
the part that owns a sound card.

* **One input, as many decoders as are wanted.** A sound card can usually
  be opened once, and two tiles on one radio may want different modes - so
  a listener holds a decoder per mode (and per RTTY polarity), made the
  first time a tile asks for it and dropped when nobody has asked for
  `IDLE_SECONDS`. The mock is the exception: it is a listener per mode,
  because a mock playing CW and RTTY at once would have the CW tracker,
  which follows whichever bin is keyed, lock onto an RTTY tone.

It follows the shape `plugins/spectrum` settled on, for the same reasons:

* **The page polls; nothing streams.** Every SSE stream through the host
  proxy holds a host server thread for its whole life (hamdash invariant 4),
  and text arrives a character at a time. The card keeps what it has decoded
  with a sequence number and the page asks for everything since its last one.

* **It never opens a sound card.** The `rx_audio` engine does, once per
  device, and this reads from it with `AudioFeed`, so the decoder can run
  while Listen and the waterfall have the same input. One listener per
  device here, shared by tiles, because two tiles on one radio want the
  same text anyway.

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

from hamdash.sdk import AudioFeed, Card

import cw
import rtty

card = Card(__file__)

BLOCK = 2048                    # samples per mock block, ~43 ms at 48 kHz
# What the decoder asks the shared engine for. The passband is 250-2700
# Hz, so 12 kHz carries it with room to spare - and it is the rate Listen
# asks for, so the two share one filter as well as one capture.
FEED_RATE = 12000
KEEP_CHARS = 20000              # text held per listener, about a long ragchew
IDLE_SECONDS = 30.0
RETRY_SECONDS = 5.0

# What the mock sends. Long enough to watch the speed estimate settle and to
# show word spacing working, and a real exchange rather than lorem ipsum.
MOCK_TEXT = "CQ CQ DE VA7HDX VA7HDX K = RST 599 599 NAME STEVE = BK"
MOCK_WPM = 22.0

# And for RTTY: a real exchange, in USB at the classic 2125/2295 pair, so
# normal polarity reads it. Figures, a shift back and a new line are all in
# it, because those are what break.
MOCK_RTTY = ("RYRYRY CQ CQ CQ DE VA7HDX VA7HDX PSE K\n"
             "VE7XYZ DE VA7HDX UR RST 599 599 QTH VANCOUVER BC BK\n")
MOCK_RTTY_MARK = 2295.0

# Modes the dropdown offers. The ones not built yet are listed because
# leaving them out makes the card look finished when it is not. They are
# offered disabled, so nobody picks one and waits for text that never comes.
MODES = [
    {"id": "cw", "name": "CW (Morse)", "ready": True},
    {"id": "rtty", "name": "RTTY 45.45", "ready": True},
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


class Stream:
    """One decoder's text, with the sequence numbers the page polls by."""

    def __init__(self, spec: dict, samplerate: int) -> None:
        self.spec = spec
        self.decoder = make_decoder(spec, samplerate)
        self.text = ""
        self.first_seq = 0          # sequence number of text[0]
        self.seq = 0                # one past the last character
        self.last_char_at = 0.0
        self.asked_at = time.time()


class Listener:
    """One audio input, decoded by every decoder a tile has asked for."""

    def __init__(self, device: str) -> None:
        self.device = device
        self.started_at = time.time()
        self.samplerate = 48000
        self.status = "starting"
        self.error = ""
        self.streams: dict[str, Stream] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"decoder-{device}")
        self._thread.start()

    def stream(self, spec: dict) -> Stream:
        """The decoder for this spec, made the first time it is asked for."""
        key = spec_key(spec)
        with _lock:
            st = self.streams.get(key)
            if st is None:
                st = self.streams[key] = Stream(spec, self.samplerate)
            st.asked_at = time.time()
            return st

    # -- capture -----------------------------------------------------------
    def _run(self) -> None:
        if self.device == "mock":
            self._run_mock(_mock_cw(self.samplerate))
            return
        if self.device == "mock:rtty":
            self._run_mock(_mock_rtty(self.samplerate))
            return
        # Not opened here. The radio's input is opened once, by the
        # rx_audio engine, and read from it - so the decoder, the
        # waterfall and Listen can all have the same sound card at once,
        # which PortAudio on Windows would not allow three openers.
        feed = AudioFeed(card.host, self.device, FEED_RATE, self._on_feed,
                         who=f"decoder-{id(self):x}").start()
        try:
            while not self._stop.wait(0.25):
                self.status = feed.status or "starting"
                self.error = (f"{feed.error} {feed.hint}".strip()
                              if feed.status == "error" else "")
        finally:
            # Releases this listener in the engine, which closes the
            # input only if nobody else - Listen, a waterfall - has it.
            feed.stop()
        self.status = "stopped"

    def _set_rate(self, rate: int) -> None:
        """The capture's own rate. Decoders made before it was known are
        made again, since every one of them is built around its sample
        rate."""
        with _lock:
            self.samplerate = rate
            for st in self.streams.values():
                st.decoder = make_decoder(st.spec, rate)

    def _on_feed(self, samples: np.ndarray, rate: int) -> None:
        if rate and rate != self.samplerate:
            self._set_rate(int(rate))
        self._feed(samples)

    def _run_mock(self, audio: np.ndarray) -> None:
        """A synthesised signal, so the card works with no radio attached.

        The rig and the spectrum card default to mock for the same reason: a
        card that needs hardware to show anything cannot be developed, and
        cannot be looked at by somebody deciding whether they want it.
        """
        self.status = "running"
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
        now = time.time()
        with _lock:
            for key, st in list(self.streams.items()):
                if now - st.asked_at > IDLE_SECONDS:
                    # No tile has wanted this mode for a while: stop paying
                    # for it. Another mode on the same input carries on.
                    del self.streams[key]
            streams = list(self.streams.values())
        for st in streams:
            try:
                got = st.decoder.feed(samples)
            except Exception as exc:                    # noqa: BLE001
                # A decoder fault must not kill the capture thread and take
                # the sound card down with it.
                self.status = "error"
                self.error = f"decoding failed: {exc}"
                continue
            if not got:
                continue
            with _lock:
                st.text += got
                st.seq += len(got)
                if len(st.text) > KEEP_CHARS:
                    st.text = st.text[len(st.text) - KEEP_CHARS:]
                st.first_seq = st.seq - len(st.text)
                st.last_char_at = time.time()

    def since(self, st: Stream, seq: int) -> tuple[int, str]:
        """Everything decoded after `seq`, and where it actually starts.

        A page that has been asleep, or a tile added to a card that has been
        running for an hour, is told where the history begins rather than
        being handed a silent gap.
        """
        with _lock:
            start = max(seq, st.first_seq)
            return start, st.text[start - st.first_seq:]

    def info(self, st: Stream) -> dict:
        d = st.decoder
        out = {"device": self.device, "status": self.status,
               "error": self.error, "samplerate": self.samplerate,
               "seq": st.seq, "first": st.first_seq,
               "tone_hz": d.tone_hz, "snr_db": d.snr_db,
               "hearing": d.hearing,
               "quiet_for": round(time.time() - st.last_char_at, 1)
               if st.last_char_at else None}
        if isinstance(d, rtty.RttyDecoder):
            out.update({"baud": d.baud, "reverse": d.reverse,
                        "mark_hz": round(d.mark_hz, 1),
                        "space_hz": round(d.space_hz, 1)})
        else:
            out["wpm"] = d.wpm
        return out

    def clear(self, st: Stream) -> None:
        """Empty the text without touching the decoder's timing.

        Clearing is about the screen, not about the signal: making it reset
        the speed estimate would mean the next character after a Clear was
        decoded worse than the one before it.
        """
        with _lock:
            st.first_seq = st.seq
            st.text = ""

    def stop(self) -> None:
        self._stop.set()


def _mock_cw(rate: int) -> np.ndarray:
    return cw.keyed_audio(MOCK_TEXT, wpm=MOCK_WPM, tone_hz=620,
                          samplerate=rate, amplitude=0.4)


def _mock_rtty(rate: int) -> np.ndarray:
    return rtty.rtty_audio(MOCK_RTTY, mark_hz=MOCK_RTTY_MARK,
                           samplerate=rate, amplitude=0.4,
                           lead_seconds=1.0, idle_seconds=0.6,
                           tail_seconds=1.0)


def make_decoder(spec: dict, samplerate: int):
    if spec.get("mode") == "rtty":
        return rtty.RttyDecoder(samplerate, reverse=spec["reverse"],
                                usos=spec["usos"],
                                stop_bits=spec["stop_bits"])
    return cw.CwDecoder(samplerate)


def spec_key(spec: dict) -> str:
    if spec.get("mode") == "rtty":
        return (f"rtty:{'rev' if spec['reverse'] else 'norm'}:"
                f"{int(spec['usos'])}:{spec['stop_bits']}")
    return "cw"


def decoder_spec(conf: dict, source: str, radio: dict) -> dict:
    """What a tile's settings ask to be decoded, as a spec for a decoder."""
    mode = str(conf.get("mode") or "cw")
    if mode != "rtty":
        return {"mode": "cw"}
    try:
        stop_bits = float(conf.get("stop_bits") or rtty.STOP_BITS)
    except (TypeError, ValueError):
        stop_bits = rtty.STOP_BITS
    return {"mode": "rtty", "reverse": reversed_for(conf, source, radio),
            "usos": conf.get("usos") is not False,
            "stop_bits": stop_bits}


def reversed_for(conf: dict, source: str, radio: dict) -> bool:
    """Which way up to read RTTY.

    Mark is the higher RF frequency, so a receiver in USB hears it as the
    higher audio tone and one in LSB as the lower. "auto" asks the radio
    which sideband it is in. The mock is a USB signal whatever the radio is
    doing - reading the simulated radio's sideband would make the mock
    unreadable whenever somebody tuned it below 10 MHz.
    """
    want = str(conf.get("reverse") or "auto")
    if want in ("normal", "reversed"):
        return want == "reversed"
    if source == "mock":
        return False
    return str(radio.get("mode") or "").upper() in ("LSB", "PKTLSB")


def listen_key(device: str, mode: str) -> str:
    """The input a listener is opened on. The mock is one per mode."""
    if device == "mock" and mode == "rtty":
        return "mock:rtty"
    return device


def radio_state() -> dict:
    """The active radio's state, or nothing if the host will not say."""
    try:
        return card.host.radio() or {}
    except Exception:                                   # noqa: BLE001
        return {}


def radio_audio(state: dict | None = None) -> str:
    """The sound device the active radio says is its own."""
    st = radio_state() if state is None else state
    return str(st.get("audio_in") or "")


def resolve_source(conf: dict, state: dict | None = None
                   ) -> tuple[str, str]:
    """(device, why) for a tile's chosen source.

    "radio" means whatever the rig in use is wired to, which is the setting
    worth having: it follows the operator switching radios without them
    having to remember that this card has its own idea of the input.
    """
    source = str(conf.get("source") or "mock")
    if source != "radio":
        return source, ""
    device = radio_audio(state)
    if device:
        return device, ""
    return "", ("The radio in use has no audio input set. "
                "Settings → Radio, or pick an input below.")


def who() -> str:
    """Who is asking - a *page*, not a tile.

    The want was keyed on the tile uid, which every browser showing that
    tile sends, so two of them were one entry and either one letting go
    took the input from the other.

    Not the Stop button, which is what `rx_audio` was fixed for: here
    `running` is a tile setting, so a press saves it and the bus stops
    every browser showing the tile - both listeners leave and closing is
    right. The gesture that is per page is **going hidden** (invariant
    5b): the Decoder tab selected in one browser and Spots in another
    had the hidden one's `/api/stop` take the input from the visible
    one.

    The page sends `who`, generated once when it loads. Falling back to
    the tile uid keeps an older page registering a want rather than none
    at all, which would have the reaper close the input under it.
    """
    return request.args.get("who") or card.tile_uid() or "-"


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
def _tile() -> tuple[dict, str, str, dict]:
    """(settings, device key, note, decoder spec) for the asking tile."""
    conf = card.settings()
    state = radio_state()
    device, note = resolve_source(conf, state)
    spec = decoder_spec(conf, str(conf.get("source") or "mock"), state)
    return conf, listen_key(device, spec["mode"]), note, spec


@card.get("/text")
def text():
    """Everything decoded since the caller's sequence number."""
    conf, device, note, spec = _tile()
    if note:
        return {"text": "", "first": 0, "seq": 0, "status": "idle",
                "error": note, "device": "", "hearing": False,
                "mode": spec["mode"]}
    li = listener_for(device, who())
    st = li.stream(spec)
    try:
        seq = int(request.args.get("since", "0"))
    except ValueError:
        seq = 0
    first, chunk = li.since(st, seq)
    out = li.info(st)
    out.update({"first": first, "text": chunk, "mode": spec["mode"],
                "source": str(conf.get("source") or "mock")})
    return out


@card.post("/clear")
def clear():
    _conf, device, note, spec = _tile()
    if note:
        return {"ok": True, "seq": 0}
    li = listener_for(device, who())
    st = li.stream(spec)
    li.clear(st)
    return {"ok": True, "seq": st.seq}


@card.post("/stop")
def stop():
    """This tile has stopped decoding.

    The page simply stops polling, which the reaper would notice eventually;
    this says so immediately, because the usual reason to press Stop is to
    give the sound card to another program.
    """
    _conf, device, note, _spec = _tile()
    uid = who()
    closed = release(device, uid) if not note else False
    # Any other input this tile had open - it may have been switched while
    # running, or the mode changed under the mock - is released too, or
    # stopping leaks whatever it was on before.
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
           {"id": "mock", "name": "Mock — a synthesised signal"}]
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
