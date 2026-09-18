"""Thread-safe pub/sub with an SSE view of it.

Topics are dotted strings: rig.state, log.qso_added, lookup.result,
<card_id>.<whatever>. Subscribers give a comma list of prefixes or "*".
Last value per topic is kept so a card that loads late still gets state.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from typing import Iterator

MAX_HISTORY = 300
QUEUE_DEPTH = 256

# An SSE stream occupies one server thread for its whole life, and a blocking
# WSGI server has a fixed pool of them. So streams are deliberately finite:
# every client (EventSource in the browser, requests in the SDK) reconnects on
# its own, and a bounded stream is how a dead client's thread gets released.
STREAM_SECONDS = 55
KEEPALIVE_SECONDS = 5
MAX_SUBSCRIBERS = 24


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

    def recent(self, topics: str = "*", limit: int = 50) -> list[dict]:
        filters = [t.strip() for t in topics.split(",") if t.strip()]
        with self._lock:
            hits = [e for e in self._history if _matches(e["topic"], filters)]
        return hits[-limit:]

    @property
    def subscribers(self) -> int:
        return len(self._subs)

    def full(self) -> bool:
        return len(self._subs) >= MAX_SUBSCRIBERS

    def stream(self, topics: str = "*") -> Iterator[str]:
        """SSE generator, deliberately short-lived.

        Ends itself after STREAM_SECONDS and lets the client reconnect. Two
        reasons: it returns the thread to the pool, and it is the only way a
        thread held by a client that vanished (closed laptop, killed card,
        reloaded page) ever comes back on a blocking server. The keepalive is
        frequent so a dead socket is noticed in seconds, not minutes.
        """
        filters = [t.strip() for t in topics.split(",") if t.strip()]
        q: queue.Queue = queue.Queue(maxsize=QUEUE_DEPTH)
        with self._lock:
            self._subs.add(q)
            snapshot = [e for e in self.last.values() if _matches(e["topic"], filters)]
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
                if _matches(evt["topic"], filters):
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
