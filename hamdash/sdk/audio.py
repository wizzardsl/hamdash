"""The radio's audio, read from the one place that opens it.

    from hamdash.sdk import AudioFeed

    feed = AudioFeed(card.host, "USB Audio CODEC", 12000,
                     lambda samples, rate: decoder.feed(samples))
    feed.start()
    ...
    feed.stop()        # the engine closes the input if this was the last

PortAudio on Windows will not reliably give one capture device to two
openers, so a card that opens the radio's input itself locks every other
card out of it - the spectrum used to take it from Listen, Listen from the
decoder. The `rx_audio` engine is the only opener; this reads numbered
blocks from it through the host, exactly as the rig card's Listen does
from the browser. One capture per device, fanned out to every rate and
every reader.

It polls rather than streams for the reason everything here does: a
stream holds a host server thread for its life (invariant 4). At ten
polls a second through the host proxy (~2 ms each) that is cheap, and the
engine's 3 s ring covers any stall.

A feed is one *listener* to the engine, with an id of its own. Stopping it
releases that listener only - the device stays open while anybody else
(another card, a browser's Listen) still wants it - and a feed that
vanishes without stopping is reaped by the engine after 20 s.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Callable

import requests

ENGINE = "rx_audio"
SYSTEM_DEFAULT = "<system default>"    # rx_audio/capture.py says the same
POLL = 0.1              # seconds between polls: 5 blocks of 20 ms
RETRY = 2.0             # after the engine could not be reached
LIMIT = 50              # blocks per poll at most: 1 s, a stall's worth


class AudioFeed:
    """Numbered PCM from the shared engine, handed to `on_audio`.

    `device` is a name as stored (never an index), "" for the system
    default, "mock" for the engine's test tone, or "rig" for the selected
    radio's own audio input. `rate` is what you would like; the engine
    decimates by a whole number, so `on_audio(samples, rate)` is told the
    rate it actually got (44.1 kHz asked for 12000 gives 14700).
    `samples` is float32 in -1..1. It is called on the feed's thread.

    A new feed joins live: it hears what arrives after it starts, never
    the backlog another reader left in the ring.
    """

    def __init__(self, host, device: str, rate: int,
                 on_audio: Callable, who: str = "",
                 engine: str = ENGINE) -> None:
        self.host = host
        self.device = device
        self.wanted_rate = int(rate)
        self.on_audio = on_audio
        self.who = who or (f"{getattr(host, 'headers', {}).get('X-Hamdash-Card', 'card')}"
                           f"-{os.getpid()}-{uuid.uuid4().hex[:8]}")
        self.url = f"{host.base}/cards/{engine}/api"
        self.status = "starting"
        self.error = ""
        self.hint = ""
        self.rate = 0           # what the engine is actually producing
        self.seq = 0            # the last block handed on
        self.gaps = 0           # times this reader fell behind the ring
        self._since: int | str = "head"
        self._stop = threading.Event()
        self._session = requests.Session()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"feed-{device or 'default'}")

    def start(self) -> "AudioFeed":
        self._thread.start()
        return self

    @property
    def running(self) -> bool:
        return self._thread.is_alive() and not self._stop.is_set()

    def stop(self, wait: float = 1.0) -> None:
        """Stop reading and tell the engine this listener has gone, so it
        can hand the device back now rather than in 20 s."""
        self._stop.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(wait)
        try:
            self._session.post(f"{self.url}/stop", params={"who": self.who},
                               headers=self.host.headers, timeout=3)
        except requests.RequestException:
            pass        # the engine's reaper gets there in the end
        self._session.close()

    # -- the loop --------------------------------------------------------
    def _params(self) -> dict:
        # A blank `device` means "my own tile's settings" to the engine,
        # and a feed has none - so the system default is said in words.
        return {"device": self.device or SYSTEM_DEFAULT, "rate": self.wanted_rate,
                "who": self.who, "since": self._since, "limit": LIMIT}

    def _run(self) -> None:
        import numpy as np
        while not self._stop.is_set():
            try:
                r = self._session.get(f"{self.url}/pcm",
                                      params=self._params(),
                                      headers=self.host.headers, timeout=5)
                if r.status_code >= 400:
                    raise requests.HTTPError(
                        f"{r.status_code} from the audio engine: "
                        f"{r.text[:200]}")
            except requests.RequestException as exc:
                # Nothing that arrives on a socket may end this thread: a
                # feed that dies takes the card's audio with it while the
                # card goes on looking healthy.
                self.status, self.error = "error", str(exc)
                self.hint = "The receive-audio engine is not answering."
                self._since = "head"
                self._stop.wait(RETRY)
                continue
            try:
                self._take(r, np)
            except Exception as exc:                    # noqa: BLE001
                print(f"audio feed {self.device or 'default'}: {exc!r}")
            self._stop.wait(POLL)

    def _take(self, r, np) -> None:
        h = r.headers
        self.status = h.get("X-Status", "")
        self.error = h.get("X-Error", "")
        self.hint = h.get("X-Hint", "")
        self.rate = int(h.get("X-Rate", "0") or 0)
        head = int(h.get("X-Head-Seq", "0") or 0)
        first = int(h.get("X-First-Seq", "0") or 0)
        last = int(h.get("X-Last-Seq", "0") or 0)
        if self.status == "error":
            # Rejoin live once it recovers, not from wherever it was.
            self._since = "head"
            return
        if isinstance(self._since, int) and head < self._since:
            # The engine restarted and is counting from nothing again;
            # asking for "after 9000" would wait for ever.
            self._since = "head"
            return
        if not r.content:
            if self._since == "head":
                self._since = head
            return
        if isinstance(self._since, int) and first > self._since + 1:
            self.gaps += 1          # fell off the back of the ring
        self._since = self.seq = last
        pcm = np.frombuffer(r.content, dtype="<i2").astype(np.float32)
        self.on_audio(pcm / 32768.0, self.rate)

    def info(self) -> dict:
        return {"device": self.device, "status": self.status,
                "error": self.error, "hint": self.hint, "rate": self.rate,
                "seq": self.seq, "gaps": self.gaps}
