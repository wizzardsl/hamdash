"""Card SDK — the whole contract a python card needs.

    from hamdash.sdk import Card

    card = Card(__file__)

    @card.get("/state")
    def state():
        return {"hello": card.host.get("/api/radio/state")}

    @card.on("log.qso_added")
    def logged(topic, payload):
        card.publish("mycard.saw_qso", {"call": payload["call"]})

    card.run()

The host sets HAMDASH_PORT / HAMDASH_HOST / HAMDASH_TOKEN / HAMDASH_CARD_ID
in the environment. Everything here is optional: a card that just wants to
serve its own index.html gets that for free from `card.run()`.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_from_directory

from hamdash import bus     # topic policy only; no shared state

__all__ = ["Card", "HostClient"]


class HostClient:
    """Thin HTTP client for the host's shared services."""

    def __init__(self, base: str, token: str, card_id: str) -> None:
        self.base = base.rstrip("/")
        self.headers = {"X-Hamdash-Token": token, "X-Hamdash-Card": card_id}

    @staticmethod
    def _check(r):
        """Surface the host's own message. A bare raise_for_status turns
        "card X has no 'radio' permission" into "403 Forbidden", which tells
        a card author nothing about what to fix."""
        if r.status_code >= 400:
            detail = ""
            try:
                detail = (r.json() or {}).get("error", "")
            except ValueError:
                detail = (r.text or "")[:200]
            raise requests.HTTPError(
                f"{r.status_code} from {r.url}: {detail}" if detail
                else f"{r.status_code} from {r.url}", response=r)
        return r

    def get(self, path: str, **params):
        r = self._check(requests.get(self.base + path, params=params or None,
                                     headers=self.headers, timeout=10))
        return r.json()

    def post(self, path: str, payload: dict | None = None):
        r = self._check(requests.post(self.base + path, json=payload or {},
                                      headers=self.headers, timeout=15))
        return r.json() if r.content else {}

    # conveniences for the shared services
    def radio(self):
        return self.get("/api/radio/state")

    def set_freq(self, hz: int):
        return self.post("/api/radio/freq", {"hz": int(hz)})

    def lookup(self, call: str):
        return self.get(f"/api/lookup/{call}")

    def log_qso(self, qso: dict):
        return self.post("/api/log", qso)

    def station(self):
        return self.get("/api/config").get("station", {})

    def tiles(self, card_id: str):
        return self.get("/api/tiles", card=card_id)

    def tile_settings(self, uid: str):
        return self.get(f"/api/tiles/{uid}/settings")

    def save_tile_settings(self, uid: str, values: dict):
        r = self._check(requests.put(f"{self.base}/api/tiles/{uid}/settings",
                                     json={"values": values},
                                     headers=self.headers, timeout=10))
        return r.json()


class Card:
    def __init__(self, entry_file: str, name: str | None = None) -> None:
        self.dir = Path(entry_file).resolve().parent
        self.id = os.environ.get("HAMDASH_CARD_ID", self.dir.name)
        self.port = int(os.environ.get("HAMDASH_PORT", "0")) or _free()
        self.token = os.environ.get("HAMDASH_TOKEN", "")
        self.host_url = os.environ.get("HAMDASH_HOST", "http://127.0.0.1:8765")
        self._home = os.environ.get("HAMDASH_HOME", "")
        self.host = HostClient(self.host_url, self.token, self.id)
        self.manifest = _manifest(self.dir)
        self.name = name or self.manifest.get("name", self.id)
        self.app = Flask(self.id, static_folder=None)
        self._subs: list[tuple[str, callable]] = []
        self._settings: dict[str, tuple[float, dict]] = {}
        self._settings_ttl = 5.0
        self._routes()
        self.on("card.settings_changed")(self._settings_changed)

    # --- keeping things between runs ------------------------------------
    @property
    def store(self) -> Path:
        """A directory this card may write to, which survives a restart.

        For anything a card should not have to fetch again: a cache of
        somebody else's API, a marker of where it got to. It lives beside the
        host's own files rather than in the plugin directory, which may be
        read-only in a packaged build and is not the operator's to fill up.

        Falls back to a temporary directory when the card is run on its own,
        so a card being developed by hand still works.
        """
        base = Path(self._home) if self._home else Path(tempfile.gettempdir()) / "hamdash"
        d = base / "card-data" / self.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def load_json(self, name: str, default=None):
        """Read a file from `store`, or return `default`. Never raises.

        A cache that cannot be read is not a reason to fail to start: the
        worst case is doing the work again.
        """
        try:
            return json.loads((self.store / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def save_json(self, name: str, value) -> bool:
        """Write a file to `store`. Returns whether it worked, and never
        raises: losing a cache is not worth failing a request over.

        Written to a temporary file and moved into place, so an interrupted
        write leaves the previous contents rather than half of the new ones.
        """
        try:
            tmp = self.store / f".{name}.tmp"
            tmp.write_text(json.dumps(value), encoding="utf-8")
            tmp.replace(self.store / name)
            return True
        except (OSError, TypeError, ValueError):
            return False

    # --- routing --------------------------------------------------------
    def _routes(self) -> None:
        view = self.manifest.get("view", "index.html")

        @self.app.get("/")
        def _index():
            if (self.dir / view).exists():
                return send_from_directory(self.dir, view)
            return f"<pre>{self.id}: no {view} in {self.dir}</pre>", 404

        @self.app.get("/assets/<path:sub>")
        def _assets(sub):
            return send_from_directory(self.dir, sub)

        @self.app.get("/_health")
        def _health():
            return jsonify(ok=True, card=self.id)

    def get(self, rule: str, **kw):
        return self.app.get(self._api(rule), **kw)

    def post(self, rule: str, **kw):
        return self.app.post(self._api(rule), **kw)

    def route(self, rule: str, **kw):
        return self.app.route(self._api(rule), **kw)

    @staticmethod
    def _api(rule: str) -> str:
        rule = rule if rule.startswith("/") else "/" + rule
        return rule if rule.startswith("/api") else "/api" + rule

    # --- tiles and settings ---------------------------------------------
    @staticmethod
    def tile_uid() -> str:
        """Which tile is asking. The host puts it in the query string and in
        X-Hamdash-Tile; one card process serves every tile of its kind, so
        anything per-instance has to hang off this."""
        from flask import request
        return (request.headers.get("X-Hamdash-Tile")
                or request.args.get("uid") or "")

    def settings(self, uid: str | None = None) -> dict:
        """Settings for one tile, merged over the manifest defaults.

        Cached for a few seconds and invalidated by card.settings_changed, so
        a card can call this on every request without hammering the host and
        without serving stale values after the operator edits them.
        """
        uid = uid or self.tile_uid()
        if not uid:
            return self.defaults()
        hit = self._settings.get(uid)
        if hit and time.time() - hit[0] < self._settings_ttl:
            return dict(hit[1])
        try:
            values = self.host.tile_settings(uid).get("values", {})
        except requests.RequestException as exc:
            print(f"settings for {uid} unavailable ({exc}); using "
                  f"{'last known' if hit else 'manifest defaults'}")
            return dict(hit[1]) if hit else self.defaults()
        self._settings[uid] = (time.time(), values)
        return dict(values)

    def defaults(self) -> dict:
        """This card's manifest defaults.

        The same shape the host merges under a tile's saved values, so a card
        asked about a tile that isn't on the board - or asked with no tile at
        all, which is how a card's own startup code sees it - gets usable
        settings rather than an empty dict and a pile of `or` fallbacks.
        """
        out = {}
        for name, spec in (self.manifest.get("settings") or {}).items():
            if isinstance(spec, dict):
                out[name] = spec.get(
                    "default", False if spec.get("type") == "bool" else "")
            else:
                out[name] = spec
        return out

    def save_settings(self, values: dict, uid: str | None = None) -> dict:
        uid = uid or self.tile_uid()
        out = self.host.save_tile_settings(uid, values)
        self._settings.pop(uid, None)
        return out

    def _settings_changed(self, _topic: str, payload: dict) -> None:
        if payload.get("card") in (None, self.id):
            self._settings.pop(payload.get("uid", ""), None)

    # --- bus ------------------------------------------------------------
    def publish(self, topic: str, payload: dict | None = None) -> None:
        """Publish inside this card's own namespace: <card_id>.*

        The host refuses anything else - the reserved host namespaces, another
        card's namespace, and (if card.json declares `publishes`) a topic not
        on that list. A refusal prints the host's reason and returns; a card
        reacting to a bus event should not die because a topic name was
        wrong, but the reason does end up in hamdash.log.
        """
        try:
            self.host.post("/api/publish", {"topic": topic,
                                            "payload": payload or {}})
        except requests.RequestException as exc:
            print(f"publish {topic} refused or failed: {exc}")

    def on(self, pattern: str):
        def deco(fn):
            self._subs.append((pattern, fn))
            return fn
        return deco

    def _listen(self) -> None:
        """Read the bus forever.

        The host ends each stream after ~a minute on purpose (an open stream
        holds a server thread), so a clean end is normal and reconnects at
        once and silently. Only real errors back off, and a 503 means the host
        is out of stream slots, which needs a longer wait.
        """
        topics = ",".join(p for p, _ in self._subs)
        url = f"{self.host_url}/api/events?topics={topics}"
        backoff = 0.0
        while True:
            if backoff:
                threading.Event().wait(backoff)
            try:
                with requests.get(url, stream=True, timeout=(5, None),
                                  headers={"Accept": "text/event-stream"}) as r:
                    if r.status_code == 503:
                        backoff = min(30.0, max(5.0, backoff * 2))
                        print(f"bus is full; retrying in {backoff:.0f}s")
                        continue
                    r.raise_for_status()
                    backoff = 0.0
                    for line in r.iter_lines(decode_unicode=True):
                        if not line or not line.startswith("data: "):
                            continue
                        evt = json.loads(line[6:])
                        for pattern, fn in self._subs:
                            if _match(pattern, evt["topic"]):
                                try:
                                    fn(evt["topic"], evt["payload"])
                                except Exception as exc:
                                    print(f"handler for {evt['topic']}: {exc}")
            except requests.RequestException as exc:
                backoff = min(30.0, (backoff * 2) or 3.0)
                print(f"bus connection lost ({exc}); retrying in {backoff:.0f}s")

    # --- run ------------------------------------------------------------
    def run(self) -> None:
        self._warn_unreachable_subscriptions()
        if self._subs:
            threading.Thread(target=self._listen, daemon=True).start()
        print(f"{self.id} listening on 127.0.0.1:{self.port}")
        self.app.run(host="127.0.0.1", port=self.port, threaded=True,
                     use_reloader=False, debug=False)


    def _warn_unreachable_subscriptions(self) -> None:
        """Say so when a handler can never fire.

        `subscribes` in card.json is enforced server-side, so an @card.on for
        a topic outside it is dead code that looks like working code. The
        failure is otherwise silent: the card runs, the events never arrive,
        and there is nothing in the log to explain it.
        """
        declared = _strings(self.manifest.get("subscribes"))
        if not declared:
            return
        for pattern, fn in self._subs:
            if pattern in bus.ALWAYS or bus.owns(self.id, pattern):
                continue        # delivered regardless of the declaration
            if pattern != "*" and not bus.covered(pattern, declared):
                print(f"{self.id}: @on({pattern!r}) can never fire - "
                      f"subscribes in card.json is "
                      f"{', '.join(declared)}. Add {pattern!r} to it.")


def _strings(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return [v.strip() for v in (value or []) if isinstance(v, str) and v.strip()]


def _match(pattern: str, topic: str) -> bool:
    if pattern in ("*", topic):
        return True
    return pattern.endswith(".*") and topic.startswith(pattern[:-1])


def _manifest(d: Path) -> dict:
    p = d / "card.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def _free() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
