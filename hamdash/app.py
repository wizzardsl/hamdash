"""The host: static shell, REST API, SSE bus, card reverse proxy."""
from __future__ import annotations

import json
import sys
import time
from functools import wraps
from pathlib import Path

import requests
from flask import (Flask, Response, abort, jsonify, request,
                   send_from_directory, stream_with_context)

from . import registry
from .bus import BUS, MAX_SUBSCRIBERS
from .config import BUNDLED_STATIC, Config
from .proc import Supervisor, free_port  # noqa: F401  (re-exported for run.py)
from .services import grid as gridlib
from .services.callsign import Lookup
from .services.logbook import Logbook
from .services.radio import MODES, Rig

HOP_BY_HOP = {"content-encoding", "content-length", "transfer-encoding",
              "connection", "keep-alive", "upgrade", "proxy-authenticate",
              "proxy-authorization", "te", "trailers"}


def create_app(cfg: Config, host: str = "127.0.0.1", port: int = 8765,
               native: bool = False):
    static = BUNDLED_STATIC
    app = Flask(__name__, static_folder=None)
    host_url = f"http://{host}:{port}"

    cards = registry.discover(cfg.plugin_dirs)
    sup = Supervisor(host_url)
    rig = Rig(cfg.data["radio"]["rigctld_host"], cfg.data["radio"]["rigctld_port"],
              mock=cfg.data["radio"].get("mock", True))
    rig.start_polling(hz=2)
    log = Logbook(cfg.db_path)
    look = _make_lookup(cfg)

    # ---------------- permissions --------------------------------------
    # A card declares what it needs in card.json ("permissions": ["radio"]).
    # Server-side card calls carry that card's token, so they are checked for
    # real. Calls from a card's own *web* page carry no token - they run in the
    # dashboard origin - so those are matched on Referer, which is a speed
    # bump and not a sandbox. See the README: real isolation needs card pages
    # on their own origin.
    def _calling_card():
        card = sup.card_for_token(request.headers.get("X-Hamdash-Token", ""))
        if card:
            return card, True
        ref = request.headers.get("Referer", "")
        marker = "/cards/"
        if marker in ref:
            cid = ref.split(marker, 1)[1].split("/", 1)[0].split("?", 1)[0]
            return cards.get(cid), False
        return None, False

    def guard(permission):
        """Refuse a shared service the calling card didn't ask for."""
        def wrap(fn):
            @wraps(fn)
            def inner(*a, **kw):
                card, verified = _calling_card()
                if card and not card.may(permission):
                    return jsonify(
                        error=f"{card.id} has no '{permission}' permission; "
                              f"add it to permissions in card.json",
                        permission=permission, card=card.id,
                        verified=verified), 403
                return fn(*a, **kw)
            return inner
        return wrap

    # ---------------- shell -------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(static, "index.html")

    @app.get("/api/shell")
    def shell():
        """Which shell the page is in. The UI must not guess: inside a native
        webview, window.open and Picture-in-Picture are not just degraded,
        they hand the URL to the OS (Windows then offers to find an app for
        the 'about:' link), so the pop-out backend is chosen from this."""
        return jsonify(native=bool(native), platform=sys.platform)

    @app.get("/popout")
    def popout_shell():
        """Chrome for a popped-out card: same card, second window."""
        return send_from_directory(static, "popout.html")

    @app.get("/static/<path:sub>")
    def assets(sub):
        return send_from_directory(static, sub)

    # ---------------- bus ---------------------------------------------
    @app.get("/api/events")
    def events():
        # Refuse politely rather than starving the thread pool. EventSource
        # retries on its own, and the SDK's reader backs off.
        if BUS.full():
            return jsonify(error="too many event streams open",
                           subscribers=BUS.subscribers), 503
        topics = request.args.get("topics", "*")
        card, _ = _calling_card()
        gen = stream_with_context(
            BUS.stream(topics, card.subscribe_filter() if card else None))
        # No Connection header: PEP 3333 forbids hop-by-hop headers from a
        # WSGI app and waitress asserts on it. The server keeps it alive anyway.
        return Response(gen, mimetype="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/publish")
    def publish():
        """Publish to the bus, inside the caller's namespace.

        The host owns bus.RESERVED; a card owns <card_id>.* and nothing more.
        Identified callers do not get to choose `source` either - it used to
        be taken from the body, so a card could label its events "host".
        """
        body = request.get_json(silent=True) or {}
        topic = body.get("topic")
        if not topic:
            return jsonify(error="topic is required"), 400
        card, verified = _calling_card()
        if card:
            why = card.may_publish(topic)
            if why:
                return jsonify(error=why, topic=topic, card=card.id,
                               verified=verified), 403
            src = card.id
        else:
            # The board and the pop-out shell: host pages, not cards.
            src = body.get("source") or request.headers.get("X-Hamdash-Card", "ui")
        return jsonify(BUS.publish(topic, body.get("payload") or {}, source=src))

    @app.get("/api/events/recent")
    def recent_events():
        card, _ = _calling_card()
        return jsonify(BUS.recent(request.args.get("topics", "*"),
                                  int(request.args.get("limit", 50)),
                                  allow=card.subscribe_filter() if card else None))

    # ---------------- cards -------------------------------------------
    @app.get("/api/cards")
    def list_cards():
        nonlocal cards
        if request.args.get("rescan"):
            cards = registry.discover(cfg.plugin_dirs)
        out = []
        for c in cards.values():
            d = c.public()
            w = sup.get(c.id)
            d["runtime"] = w.info() if w else {"status": "stopped"}
            out.append(d)
        return jsonify(out)

    @app.post("/api/cards/<cid>/start")
    def start_card(cid):
        c = cards.get(cid) or abort(404)
        if c.kind != "python":
            return jsonify(status="n/a")
        return jsonify(sup.ensure(c).info())

    @app.post("/api/cards/<cid>/restart")
    def restart_card(cid):
        cards.get(cid) or abort(404)
        sup.restart(cid)
        return jsonify(sup.get(cid).info())

    @app.post("/api/cards/<cid>/stop")
    def stop_card(cid):
        w = sup.get(cid) or abort(404)
        w.stop()
        return jsonify(w.info())

    @app.route("/cards/<cid>/", defaults={"sub": ""},
               methods=["GET", "POST", "PUT", "DELETE"])
    @app.route("/cards/<cid>/<path:sub>",
               methods=["GET", "POST", "PUT", "DELETE"])
    def card_mount(cid, sub):
        c = cards.get(cid) or abort(404)
        if c.error:
            return _card_error(c), 500
        if c.kind == "html":
            return send_from_directory(c.path, sub or c.view)
        if c.kind == "url":
            return Response(f'<meta http-equiv="refresh" content="0;url={c.url}">',
                            mimetype="text/html")
        return _proxy(sup.ensure(c), sub)

    def _proxy(worker, sub):
        # The UI mounts a tile and immediately requests the card page, which
        # is usually faster than a fresh python process can bind its port.
        # Wait for it rather than painting a 502 on every first load.
        if worker.status == "starting":
            # Short wait only: this is holding a server thread. If the card is
            # slower than that, hand back a page that retries itself, so the
            # thread goes back to the pool instead of sleeping in it.
            deadline = time.time() + 2.5
            while time.time() < deadline and worker.status == "starting":
                time.sleep(0.1)
            if worker.status == "starting":
                return Response(_starting(worker), mimetype="text/html")
        if worker.status not in ("running", "wedged"):
            return _worker_down(worker), 503
        url = f"{worker.base}/{sub}"
        try:
            # The tile uid rides along in both the query string and a header,
            # so a card can tell its instances apart: two web_embed tiles are
            # two different pages, two beacon_map tiles two different targets.
            headers = {k: v for k, v in request.headers
                       if k.lower() not in ("host", "content-length")}
            uid = request.args.get("uid", "")
            if uid:
                headers["X-Hamdash-Tile"] = uid
            r = requests.request(
                request.method, url,
                params=request.args,
                data=request.get_data(),
                headers=headers,
                timeout=30, stream=True, allow_redirects=False)
        except requests.RequestException as exc:
            return (f"<pre>card {worker.card.id} not answering on "
                    f"{worker.base}\n{exc}</pre>"), 502
        headers = [(k, v) for k, v in r.raw.headers.items()
                   if k.lower() not in HOP_BY_HOP]
        return Response(r.iter_content(8192), status=r.status_code,
                        headers=headers)

    # ---------------- layout & config ----------------------------------
    @app.get("/api/layout")
    def get_layout():
        return jsonify({"tiles": cfg.load_layout(),
                        "cols": cfg.data["grid_cols"],
                        "row_px": cfg.data["row_px"]})

    @app.put("/api/layout")
    def put_layout():
        body = request.get_json(silent=True) or {}
        cfg.save_layout(body.get("tiles", []))
        # A card with no tiles left has nothing to serve: stop its process
        # instead of leaking one per removed card until restart.
        stopped = sup.reap(cfg.active_card_ids())
        if stopped:
            BUS.publish("card.reaped", {"cards": stopped}, source="host")
        return jsonify(ok=True, stopped=stopped)

    @app.get("/api/tiles")
    def tiles_list():
        """Tiles, optionally just the ones belonging to one card."""
        want = request.args.get("card")
        out = [t for t in cfg.load_layout() if not want or t.get("card") == want]
        for t in out:
            c = cards.get(t.get("card"))
            t["settings"] = {**(c.defaults() if c else {}),
                             **(t.get("settings") or {})}
        return jsonify(out)

    @app.get("/api/tiles/<uid>/settings")
    def tile_settings_get(uid):
        tile = cfg.tile(uid)
        if not tile:
            return jsonify(error=f"no tile {uid} on the board"), 404
        card = cards.get(tile.get("card"))
        caller, _ = _calling_card()
        if caller and caller.id != tile.get("card"):
            return jsonify(error=f"{caller.id} cannot read another card's tile"), 403
        return jsonify({"uid": uid, "card": tile.get("card"),
                        "schema": card.schema() if card else [],
                        "values": {**(card.defaults() if card else {}),
                                   **(tile.get("settings") or {})}})

    @app.put("/api/tiles/<uid>/settings")
    def tile_settings_put(uid):
        body = request.get_json(silent=True) or {}
        tile = cfg.tile(uid)
        if not tile:
            return jsonify(error=f"no tile {uid} on the board"), 404
        caller, _ = _calling_card()
        if caller and caller.id != tile.get("card"):
            return jsonify(error=f"{caller.id} cannot write another card's tile"), 403
        try:
            values = cfg.set_tile_settings(uid, body.get("values", body))
        except KeyError as exc:
            return jsonify(error=str(exc)), 404
        # Cards cache settings; this is how they find out to stop.
        BUS.publish("card.settings_changed",
                    {"uid": uid, "card": tile.get("card"), "values": values},
                    source="host")
        return jsonify({"uid": uid, "values": values})

    @app.get("/api/config")
    def get_config():
        safe = json.loads(json.dumps(cfg.data))
        safe["callsign_lookup"]["password"] = "***" if safe["callsign_lookup"].get("password") else ""
        return jsonify(safe)

    @app.put("/api/config")
    def put_config():
        nonlocal look
        body = request.get_json(silent=True) or {}
        for key in ("station", "theme", "radio", "grid_cols", "row_px",
                    "callsign_lookup", "units"):
            if key in body:
                if isinstance(cfg.data.get(key), dict):
                    cfg.data[key].update(body[key])
                else:
                    cfg.data[key] = body[key]
        if "callsign_lookup" in body:
            # Rebuilt rather than left until restart: the point of changing the
            # provider is usually to look something up straight afterwards, and
            # silently using the old one is the kind of trap that costs an hour.
            look = _make_lookup(cfg)
        cfg.save()
        BUS.publish("config.changed", cfg.data, source="host")
        return jsonify(ok=True)

    @app.get("/api/themes")
    def themes():
        out = []
        for d in cfg.theme_dirs:
            for f in sorted(d.glob("*.json")) if d.exists() else []:
                try:
                    t = json.loads(f.read_text())
                    t["id"] = t.get("id", f.stem)
                    out.append(t)
                except json.JSONDecodeError as exc:
                    print(f"{f}: bad theme JSON ({exc})")
        return jsonify(out)

    # ---------------- shared services ---------------------------------
    @app.get("/api/radio/state")
    @guard("radio")
    def radio_state():
        return jsonify(rig.state())

    @app.post("/api/radio/freq")
    @guard("radio")
    def radio_freq():
        hz = (request.get_json(silent=True) or {}).get("hz")
        try:
            return jsonify(rig.set_freq(int(hz)))
        except (TypeError, ValueError):
            return jsonify(error="hz must be an integer"), 400
        except Exception as exc:
            return jsonify(error=str(exc)), 502

    @app.post("/api/radio/mode")
    @guard("radio")
    def radio_mode():
        b = request.get_json(silent=True) or {}
        try:
            return jsonify(rig.set_mode(b.get("mode", "USB"),
                                        int(b.get("width", 0))))
        except Exception as exc:
            return jsonify(error=str(exc)), 502

    @app.post("/api/radio/ptt")
    @guard("radio")
    def radio_ptt():
        on = bool((request.get_json(silent=True) or {}).get("on"))
        try:
            return jsonify(rig.set_ptt(on))
        except Exception as exc:
            return jsonify(error=str(exc)), 502

    @app.get("/api/radio/modes")
    @guard("radio")
    def radio_modes():
        return jsonify(MODES)

    @app.get("/api/lookup/<call>")
    @guard("lookup")
    def lookup(call):
        res = look.lookup(call)
        my_grid = cfg.data["station"].get("grid", "")
        if res.get("locator") and gridlib.valid(my_grid) \
                and gridlib.valid(res["locator"]):
            res["path"] = gridlib.distance_bearing(my_grid, res["locator"])
        res["worked_before"] = log.worked_before(call)
        BUS.publish("lookup.result", res, source="host")
        return jsonify(res)

    @app.post("/api/grid/locator")
    @guard("lookup")
    def grid_locator():
        """Coordinates to a Maidenhead locator.

        POST, not GET with a query string: this carries the operator's actual
        position, and a query string is the one place it would end up written
        down - in a log, in history, in a referer.
        """
        body = request.get_json(silent=True) or {}
        try:
            lat, lon = float(body["lat"]), float(body["lon"])
        except (KeyError, TypeError, ValueError):
            return jsonify(error="send lat and lon as numbers"), 400
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return jsonify(error=f"{lat},{lon} is not on Earth"), 400
        return jsonify(locator=gridlib.to_locator(lat, lon),
                       precision=6)

    @app.get("/api/grid/distance")
    @guard("lookup")
    def grid_distance():
        a = request.args.get("from") or cfg.data["station"].get("grid", "")
        b = request.args.get("to", "")
        if not a:
            return jsonify(error="no 'from' grid given and your station grid "
                                 "is not set (Settings -> station grid)"), 400
        try:
            return jsonify(gridlib.distance_bearing(a, b))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.get("/api/log")
    @guard("log")
    def log_list():
        q = request.args.get("q")
        return jsonify(log.search(q) if q else log.recent(
            int(request.args.get("limit", 25))))

    @app.post("/api/log")
    @guard("log")
    def log_add():
        try:
            return jsonify(log.add(request.get_json(silent=True) or {}))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.delete("/api/log/<int:qso_id>")
    @guard("log")
    def log_del(qso_id):
        log.delete(qso_id)
        return jsonify(ok=True)

    @app.get("/api/log/stats")
    @guard("log")
    def log_stats():
        return jsonify(log.stats())

    @app.get("/api/log/adif")
    @guard("log")
    def log_adif():
        body = log.export_adif(cfg.data["station"].get("callsign", ""))
        return Response(body, mimetype="text/plain", headers={
            "Content-Disposition": 'attachment; filename="hamdash.adi"'})

    @app.post("/api/log/adif")
    @guard("log")
    def log_adif_import():
        n = log.import_adif(request.get_data(as_text=True))
        return jsonify(imported=n)

    @app.get("/api/bus/stats")
    def bus_stats():
        return jsonify(subscribers=BUS.subscribers, limit=MAX_SUBSCRIBERS,
                       full=BUS.full())

    @app.get("/api/health")
    def health():
        return jsonify(ok=True, cards=len(cards),
                       running=sum(1 for w in sup.workers.values()
                                   if w.status == "running"),
                       wedged=[w.card.id for w in sup.workers.values()
                               if w.status == "wedged"],
                       radio=rig.state().get("ok"))

    def shutdown():
        sup.stop_all()
        rig.stop()

    return app, shutdown


def _make_lookup(cfg) -> Lookup:
    c = cfg.data["callsign_lookup"]
    return Lookup(c.get("provider", "callook"), c.get("user", ""),
                  c.get("password", ""), cache_path=cfg.db_path,
                  cache_days=c.get("cache_days", 30))


def _card_error(card):
    return (f"<pre style='font:13px ui-monospace;padding:16px'>"
            f"{card.id} can't load: {card.error}\n\nmanifest: {card.path}/card.json"
            f"</pre>")


def _starting(worker):
    return (f"<!doctype html><meta charset=utf-8>"
            f"<meta http-equiv=refresh content=1>"
            f"<link rel=stylesheet href=/static/css/app.css>"
            f"<p style='font:13px system-ui;padding:16px;color:var(--muted)'>"
            f"Starting {worker.card.id}…</p>")


def _worker_down(worker):
    tail = "\n".join(worker.log[-20:]) or "(no output)"
    return (f"<pre style='font:13px ui-monospace;padding:16px;white-space:pre-wrap'>"
            f"{worker.card.id} is {worker.status}\n\n{tail}</pre>")
