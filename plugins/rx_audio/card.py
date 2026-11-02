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

It is also the one place a sound card is opened. PortAudio on Windows
will not reliably give one capture device to two openers, so the spectrum
and the decoder read the radio's input from here (`AudioFeed` in the SDK)
rather than opening it themselves: one capture per device, fanned out to
every rate and every listener. `capture.py` is that half, and is driven
by the tests with fake sources playing known tones.

Receive only. Transmit needs a microphone, `getUserMedia` needs a secure
context, and nothing here is authenticated - see `docs/built-in-cards.md`.
"""
from __future__ import annotations

import threading
import time

from flask import Response, request

from hamdash.sdk import Card

import capture
from capture import BLOCK_MS

card = Card(__file__)

# One capture per device, fanned out to every rate and every listener -
# Listen in the rig card, and the spectrum and decoder through their
# feeds. See `capture.py`.
hub = capture.Hub()
NO_RIG_INPUT = capture.NO_RIG_INPUT


def _device_for(conf: dict) -> str:
    """'rig' follows the active radio's own audio input.

    That setting already exists per rig and already rides on radio.state,
    so this needs no second answer to "which sound card is the radio's" -
    and changing radios moves the audio with it. A radio with none set
    resolves to NO_RIG_INPUT, which refuses, rather than to "", which
    would quietly open the system default input.
    """
    want = str(conf.get("device", "mock")).strip()
    if want == capture.SYSTEM_DEFAULT:
        # Said in words by a card's feed (a spectrum tile left blank),
        # because a blank override means "my tile's own settings".
        return ""
    if want != "rig":
        return want
    st = {}
    try:
        # The tile's own radio when the rig card names one, so two rig
        # tiles on two radios each hear their own.
        st = card.host.radio(conf.get("radio", "")) or {}
        got = str(st.get("audio_in") or "").strip()
    except Exception:                                   # noqa: BLE001
        got = ""
    # A simulated radio with no input set plays the simulated audio - the
    # test tone - so pressing Listen on it does something. Only then: a
    # simulated radio given a real input still uses it, and a real radio
    # with none set still refuses, rather than opening a microphone.
    if not got and st.get("mock") is True:
        return "mock"
    return got or NO_RIG_INPUT


def _conf() -> dict:
    """This request's settings: the tile's, with the caller's overrides.

    The rig card plays the radio's audio through this card, and a card
    may read only its own tiles' settings - the host answers anybody
    else with 403 - so the rig card's page says which input and quality
    its own tile chose, as `device` and `rate` on the request.
    """
    device = (request.args.get("device") or "").strip()
    # Not asked of the host when the page has said: the rig card's tile is
    # not this card's, the host answers 403, and the log said so as if it
    # were a fault.
    conf = {} if device else dict(card.settings() or {})
    if device:
        conf["device"] = device
    rig = (request.args.get("rig") or "").strip()
    if rig:
        conf["radio"] = rig
    rate = request.args.get("rate")
    if rate:
        try:
            conf["rate"] = int(rate)
        except ValueError:
            pass
    return conf


def _key_for(conf: dict) -> tuple[str, int]:
    """(device, rate) for these settings - the device resolved, so 'rig'
    and the radio's own input name share one capture."""
    return _device_for(conf), capture.clamp_rate(conf.get("rate", 12000))


def look(conf: dict):
    """The tap for these settings if one is open, without opening one.
    Asking must not be the same as wanting: see `Hub.look`."""
    device, rate = _key_for(conf)
    return hub.look(device, rate)


def who() -> str:
    """Who is asking - a *page*, not a tile.

    The want used to be keyed on the tile uid, which is the same for
    every browser showing that tile. Open the board in two places, which
    is the entire point of a card that plays the radio's audio over the
    network, and the two are indistinguishable here: one of them pressing
    Stop takes the device away from the other, whose next poll opens it
    again. It shows up in the log as a row of "last listener left" and to
    the operator as audio that will not start - which is exactly how it
    was reported.

    The page sends `who`, generated once when it loads. Falling back to
    the tile uid keeps an older page working rather than leaving it with
    no want at all, which would have the reaper close the device under
    it every 20 seconds.
    """
    return request.args.get("who") or card.tile_uid() or "-"


def tap_for(conf: dict, who: str):
    """The tap for these settings, opened if need be, and marked wanted."""
    device, rate = _key_for(conf)
    return hub.tap_for(device, rate, who)


def release(who: str) -> None:
    """One listener has stopped; the device closes if it was the last.
    See `Hub.release`."""
    hub.release(who)


def _reap_idle() -> None:
    while True:
        time.sleep(5.0)
        hub.reap()


# ---------------------------------------------------------------- routes
@card.get("/info")
def info():
    """What the input is doing - without starting it. See `look`."""
    conf = _conf()
    t = look(conf)
    if t is not None:
        return {**t.info(), "listeners": hub.listeners(t.device)}
    device, rate = _device_for(conf), _key_for(conf)[1]
    if device == NO_RIG_INPUT:
        # Said before Listen is pressed, not after: there is nothing to
        # press it for.
        return {"status": "error", "device": "",
                "error": "This radio has no audio input set.",
                "hint": "Set it in Settings -> Radio, or choose an input in "
                        "this tile's settings.",
                "rate": rate, "in_rate": 0, "block_n": 0,
                "block_ms": BLOCK_MS, "seq": 0, "first_seq": 0,
                "overflows": 0, "peak": 0.0, "now": time.time()}
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
    t = tap_for(_conf(), who())
    since = request.args.get("since", "0")
    if since == "head":
        # Join live. A newcomer - a decoder starting up, a waterfall
        # whose tile was just added - is somebody else's backlog's
        # stranger: it gets what arrives from now, not the three seconds
        # the ring holds for whoever was here first.
        seq = t.seq
    else:
        try:
            seq = int(since)
        except ValueError:
            seq = 0
    try:
        limit = max(1, min(capture.HISTORY, int(request.args.get("limit", "32"))))
    except ValueError:
        limit = 32
    first, last, body, stamp = t.since(seq, limit)
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
    # For a card's feed, which has no /info poll of its own: why there is
    # no audio, in words. Headers are latin-1, so anything else goes.
    if t.status == "error":
        c = t.capture
        r.headers["X-Error"] = c.error.encode("ascii", "replace").decode()
        r.headers["X-Hint"] = c.hint.encode("ascii", "replace").decode()
    r.headers["Cache-Control"] = "no-store"
    return r


@card.post("/stop")
def stop():
    """This tile has stopped listening."""
    release(who())
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
