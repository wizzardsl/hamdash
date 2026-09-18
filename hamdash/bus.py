"""Thread-safe pub/sub with an SSE view of it.

Topics are dotted strings: radio.state, log.qso_added, lookup.result,
<card_id>.<whatever>. Subscribers give a comma list of prefixes or "*".
Last value per topic is kept so a card that loads late still gets state.

Publishing is namespaced: the host owns RESERVED, a card owns its own id
and nothing else. See refusal() for what that buys and what it does not.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from typing import Iterator

# Namespaces the host publishes on. A card that could publish radio.state
# could make every tile on the board believe the rig had moved, so these are
# refused for card callers rather than merely discouraged. "rig." is reserved
# although nothing publishes it: the docs have called the radio topic
# rig.state for a while, and a card squatting the name the docs point at is
# the confusing case worth preventing.
RESERVED = ("card.", "config.", "log.", "lookup.", "radio.", "rig.", "ui.")

# Delivered to every card whatever its `subscribes` says. Both are
# infrastructure the SDK and hamdash.js rely on, not opt-in subscriptions:
#   card.settings_changed  drops card.settings()'s cache when a tile is
#                          edited; a card with a narrow list would otherwise
#                          quietly stop noticing edits and look like it had a
#                          caching bug.
#   config.changed         is how a card page learns the theme changed. A card
#                          is an iframe with its own document, so it themes
#                          itself; without this the board would go light and
#                          every card would stay dark.
ALWAYS = ("card.settings_changed", "config.changed")

MAX_HISTORY = 300
QUEUE_DEPTH = 256

# An SSE stream occupies one server thread for its whole life, and a blocking
# WSGI server has a fixed pool of them. So streams are deliberately finite:
# every client (EventSource in the browser, requests in the SDK) reconnects on
# its own, and a bounded stream is how a dead client's thread gets released.
STREAM_SECONDS = 55
KEEPALIVE_SECONDS = 5
MAX_SUBSCRIBERS = 24


def owns(card_id: str, topic: str) -> bool:
    """Is this topic inside the card's own namespace?"""
    return topic == card_id or topic.startswith(card_id + ".")


def refusal(card_id: str, topic: str, declared: list[str] | None = None
            ) -> str | None:
    """Why this card may not publish this topic, or None if it may.

    A card owns <card_id>.* and nothing else. If its manifest declares
    `publishes`, that narrows the namespace further, which catches a typo
    (mycard.tunned) at the point of publishing rather than leaving a
    subscriber waiting for an event that never arrives.

    This is enforced for a card's *server* calls, which carry its token. A
    card's *page* runs in the dashboard origin and is identified by Referer,
    which the page itself controls - so treat this as a namespace policy that
    keeps honest cards from colliding, not as protection from a hostile one.
    The same caveat as `permissions`; see the README.
    """
    if not topic:
        return "topic is required"
    for prefix in RESERVED:
        if topic == prefix.rstrip(".") or topic.startswith(prefix):
            return (f"{topic!r} is in the reserved {prefix}* namespace, which "
                    f"only the host publishes. Publish {card_id}.* instead.")
    if not owns(card_id, topic):
        return (f"{card_id} may only publish {card_id}.*, not {topic!r}. "
                f"To reach another card, publish {card_id}.<something> and "
                f"have it subscribe.")
    if declared and not _matches(topic, declared):
        return (f"{topic!r} is not in the publishes list in {card_id}'s "
                f"card.json ({', '.join(declared)}). Add it there, or fix "
                f"the topic if it is a typo.")
    return None


def covered(topic: str, filters: list[str]) -> bool:
    """Public form of the subscription match: does any filter cover topic?"""
    return _matches(topic, filters)


def _matches(topic: str, filters: list[str]) -> bool:
    if not filters or "*" in filters:
        return True
    return any(topic == f or topic.startswith(f.rstrip("*").rstrip(".") + ".")
               for f in filters)


class Bus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: set[queue.Queue] = set()
        self._history: list[dict] = []
        self.last: dict[str, dict] = {}

    def publish(self, topic: str, payload: dict | None = None,
                source: str = "host") -> dict:
        evt = {"topic": topic, "payload": payload or {}, "source": source,
               "ts": round(time.time(), 3)}
        with self._lock:
            self.last[topic] = evt
            self._history.append(evt)
            del self._history[:-MAX_HISTORY]
            dead = []
            for q in self._subs:
                try:
                    q.put_nowait(evt)
                except queue.Full:
                    dead.append(q)      # a stalled consumer gets dropped, not queued forever
            for q in dead:
                self._subs.discard(q)
        return evt

    def recent(self, topics: str = "*", limit: int = 50,
               allow: list[str] | None = None) -> list[dict]:
        # `allow` mirrors stream(): without it, a card held to a subscribes
        # list could read the same events back out of history instead.
        filters = [t.strip() for t in topics.split(",") if t.strip()]
        with self._lock:
            hits = [e for e in self._history
                    if _matches(e["topic"], filters)
                    and (allow is None or e["topic"] in ALWAYS
                         or _matches(e["topic"], allow))]
        return hits[-limit:]

    @property
    def subscribers(self) -> int:
        return len(self._subs)

    def full(self) -> bool:
        return len(self._subs) >= MAX_SUBSCRIBERS

    def stream(self, topics: str = "*",
               allow: list[str] | None = None) -> Iterator[str]:
        """SSE generator, deliberately short-lived.

        Ends itself after STREAM_SECONDS and lets the client reconnect. Two
        reasons: it returns the thread to the pool, and it is the only way a
        thread held by a client that vanished (closed laptop, killed card,
        reloaded page) ever comes back on a blocking server. The keepalive is
        frequent so a dead socket is noticed in seconds, not minutes.

        `allow` is the caller's declared `subscribes`, applied on top of what
        it asked for: None means undeclared, i.e. unrestricted. An empty list
        is normalised to None upstream, so "subscribes": [] reads as "didn't
        say" rather than "wants nothing" - a card that wanted nothing would
        not open a stream.
        """
        filters = [t.strip() for t in topics.split(",") if t.strip()]

        def wanted(topic: str) -> bool:
            return (_matches(topic, filters)
                    and (allow is None or topic in ALWAYS
                         or _matches(topic, allow)))
        q: queue.Queue = queue.Queue(maxsize=QUEUE_DEPTH)
        with self._lock:
            self._subs.add(q)
            snapshot = [e for e in self.last.values() if wanted(e["topic"])]
        ends_at = time.time() + STREAM_SECONDS
        try:
            for evt in snapshot:
                yield _sse(evt)
            while time.time() < ends_at:
                try:
                    evt = q.get(timeout=KEEPALIVE_SECONDS)
                except queue.Empty:
                    yield ": keepalive\n\n"   # write fails fast on a dead socket
                    continue
                if wanted(evt["topic"]):
                    yield _sse(evt)
            yield "event: reconnect\ndata: {}\n\n"
        finally:
            with self._lock:
                self._subs.discard(q)


def _sse(evt: dict) -> str:
    # No `event:` name on purpose: the UI listens with EventSource.onmessage,
    # which never fires for named events. Topic routing happens client side.
    return f"data: {json.dumps(evt)}\n\n"


BUS = Bus()
