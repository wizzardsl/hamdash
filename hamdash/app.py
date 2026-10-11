"""The host: static shell, REST API, SSE bus, card reverse proxy."""
from __future__ import annotations

import json
import sys
import threading
import time
from html import escape
from functools import wraps
from pathlib import Path

import requests
from flask import (Flask, Response, abort, jsonify, request,
                   send_from_directory, stream_with_context)

from . import registry
from .bus import BUS, MAX_SUBSCRIBERS
from .config import (BUNDLED_STATIC, CREDENTIALS, SECRETS, Config,
                     tidy_credential)
from .proc import Supervisor, free_port  # noqa: F401  (re-exported for run.py)
from .services import dxcc
from .services import grid as gridlib
from .services.callsign import Lookup
from .services.logbook import Logbook
from .services.qsl import Lotw, QrzLogbook, Runner, SyncError, WrlLogbook
from .services.radio import MODES
from .services import audio as audiosvc


from .services import bands as bandsvc
from .services import rigs as rigsvc
from .services.space import Aurora, Drap, Ionosondes

# One requests.Session per server thread, for the card proxy.
_local = threading.local()

# A proxied reply at or over this streams; under it is sent as bytes.
# 64 kB covers every JSON reply and every ordinary card page.
STREAM_CHUNK = 65536

HOP_BY_HOP = {"content-encoding", "content-length", "transfer-encoding",
              "connection", "keep-alive", "upgrade", "proxy-authenticate",
              "proxy-authorization", "te", "trailers"}


def create_app(cfg: Config, host: str = "127.0.0.1", port: int = 8765,
               native: bool = False):
    static = BUNDLED_STATIC
    app = Flask(__name__, static_folder=None)
    host_url = f"http://{host}:{port}"

    cards = registry.discover(cfg.plugin_dirs)
    sup = Supervisor(host_url, home=str(cfg.home))
    rigsvc.ensure_rigs(cfg)
    rig = rigsvc.RigManager(cfg)
    rig.start_polling(hz=2)
    log = Logbook(cfg.db_path)
    look = _make_lookup(cfg)
    syncer = Runner(lambda t, p: BUS.publish(t, p, source='host'))
    aurora = Aurora()
    drap = Drap()
    ionosondes = Ionosondes()

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

    def _session() -> requests.Session:
        """One pooled connection to the cards, per server thread.

        `requests.request()` builds a whole Session - adapters, connection
        pool, the lot - and throws it away again, which measured 15.6 ms
        per call on Windows against 1.1 ms for a session that is kept.
        That is the Windows timer tick showing up a third time in this
        tree, and it was being paid on *every request to every card*: the
        tile's page, its settings, its polling, all of it.

        Thread-local rather than one shared Session, because requests does
        not promise a Session is safe to use from several threads and
        waitress hands every request to a different one. The pool is
        bounded by waitress' own thread count.
        """
        s = getattr(_local, "session", None)
        if s is None:
            s = _local.session = requests.Session()
        return s

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
            return _worker_down(worker, cfg.home), 503
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
            r = _session().request(
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
        # Hand back bytes when the whole reply is small, and only stream
        # what is actually big. A generator response costs a 15.6 ms
        # Windows timer tick inside waitress - measured at 15.12 ms against
        # 1.07 ms for the identical body returned as bytes - and almost
        # every card reply is a few kB of JSON that was paying it for
        # nothing. Bigger replies (a card page with images, a long audio
        # catch-up) still stream, because holding those in memory to save a
        # tick is the wrong trade.
        it = r.iter_content(STREAM_CHUNK)
        head = next(it, b"")
        if len(head) < STREAM_CHUNK:
            return Response(head, status=r.status_code, headers=headers)

        def rest():
            yield head
            yield from it
        return Response(rest(), status=r.status_code, headers=headers)

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
        # Cards, not boxes. A card asking "what tiles do I have" means its
        # own instances, and one of them may be a tab in a stack - which is
        # still a tile as far as the card is concerned.
        want = request.args.get("card")
        out = [dict(e) for e in cfg.entries()
               if not want or e.get("card") == want]
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
        values = {**(card.defaults() if card else {}),
                  **(tile.get("settings") or {})}
        secret_set = {}
        if card:
            # The owning card needs its real values - path_map builds a tile
            # URL from its weather key. Everyone else, the settings form
            # included, gets to know a secret exists and not what it is. A
            # form that is shown a secret sends it back on the next save,
            # which is how the lookup password used to become "***".
            mine = caller is not None and caller.id == card.id
            for f in card.schema():
                if f["type"] != "secret":
                    continue
                secret_set[f["name"]] = bool(str(values.get(f["name"]) or "").strip())
                if not mine:
                    values[f["name"]] = ""
        return jsonify({"uid": uid, "card": tile.get("card"),
                        "schema": card.schema() if card else [],
                        "secrets_set": secret_set,
                        "values": values})

    @app.put("/api/tiles/<uid>/settings")
    def tile_settings_put(uid):
        body = request.get_json(silent=True) or {}
        tile = cfg.tile(uid)
        if not tile:
            return jsonify(error=f"no tile {uid} on the board"), 404
        caller, _ = _calling_card()
        if caller and caller.id != tile.get("card"):
            return jsonify(error=f"{caller.id} cannot write another card's tile"), 403
        incoming = dict(body.get("values", body) or {})
        # An empty secret means "I was not editing this", never "erase it".
        # The settings form is not shown a secret, so it cannot send one back,
        # and an untouched box must not wipe what is stored.
        card_for_tile = cards.get(tile.get("card"))
        if card_for_tile:
            held = tile.get("settings") or {}
            for f in card_for_tile.schema():
                name = f["name"]
                if f["type"] != "secret":
                    continue
                if not str(incoming.get(name) or "").strip() and held.get(name):
                    incoming[name] = held[name]
        try:
            values = cfg.set_tile_settings(uid, incoming)
        except KeyError as exc:
            return jsonify(error=str(exc)), 404
        # Cards cache settings; this is how they find out to stop.
        BUS.publish("card.settings_changed",
                    {"uid": uid, "card": tile.get("card"), "values": values},
                    source="host")
        return jsonify({"uid": uid, "values": values})

    def _safe_config() -> dict:
        """Config without the secrets.

        They used to come back as the literal "***", which the settings form
        loaded into the field and saved straight back - so opening settings
        and pressing Save replaced the password with three asterisks. Secrets
        now leave as empty, and `secrets_set` says which ones exist so the
        form can show that without knowing them.

        Used for the bus as well as the endpoint. `config.changed` is in
        `bus.ALWAYS`, so it reaches every card page there is; publishing
        `cfg.data` raw put the operator's QRZ password in all of them.
        """
        safe = json.loads(json.dumps(cfg.data))
        which = {}
        for section, key in SECRETS:
            block = safe.get(section)
            if not isinstance(block, dict):
                continue
            which[f"{section}.{key}"] = bool((block.get(key) or "").strip())
            block[key] = ""
        safe["secrets_set"] = which
        return safe

    @app.get("/api/config")
    def get_config():
        return jsonify(_safe_config())

    @app.put("/api/config")
    def put_config():
        nonlocal look
        body = request.get_json(silent=True) or {}
        # Before anything reads them: a credential never means the whitespace
        # around it, and the service that rejects it only says "incorrect".
        for section, key in CREDENTIALS:
            block = body.get(section)
            if isinstance(block, dict) and key in block:
                block[key] = tidy_credential(block[key])
        kept = {f"{sec}.{k}": (cfg.data.get(sec, {}) or {}).get(k, "")
                for sec, k in SECRETS}
        for key in ("station", "theme", "radio", "grid_cols", "row_px",
                    "callsign_lookup", "logbook_sync", "units"):
            if key in body:
                if isinstance(cfg.data.get(key), dict):
                    cfg.data[key].update(body[key])
                else:
                    cfg.data[key] = body[key]
        # A secret is only changed when a new one is actually supplied. Absent
        # or empty means "I was not editing this"; an explicit null means
        # "clear it". Without that rule every save rewrites every secret with
        # whatever the form happened to be showing.
        for section, key in SECRETS:
            incoming = body.get(section)
            if not isinstance(incoming, dict) or key not in incoming:
                continue
            value = incoming[key]
            if value is None:
                cfg.data.setdefault(section, {})[key] = ""
            elif str(value).strip():
                cfg.data.setdefault(section, {})[key] = value
            else:
                cfg.data.setdefault(section, {})[key] = kept.get(f"{section}.{key}", "")

        if "callsign_lookup" in body:
            # Rebuilt rather than left until restart: the point of changing the
            # provider is usually to look something up straight afterwards, and
            # silently using the old one is the kind of trap that costs an hour.
            look = _make_lookup(cfg)
        cfg.save()
        BUS.publish("config.changed", _safe_config(), source="host")
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

    @app.get("/api/radio/meters")
    @guard("radio")
    def radio_meters():
        """The S-meter, or the power and SWR while transmitting.

        Its own endpoint rather than part of the state: a needle changes on
        every read and `radio.state` is published on change, so putting
        them together would wake every card on the board twice a second.
        Whoever draws a meter polls for it.
        """
        return jsonify(rig.meters())

    @app.post("/api/radio/vfo")
    @guard("radio")
    def radio_vfo():
        b = request.get_json(silent=True) or {}
        try:
            if b.get("swap"):
                return jsonify(rig.swap_vfo())
            return jsonify(rig.set_vfo(b.get("which", "A")))
        except Exception as exc:
            return jsonify(error=str(exc)), 502

    @app.post("/api/radio/split")
    @guard("radio")
    def radio_split():
        on = bool((request.get_json(silent=True) or {}).get("on"))
        try:
            return jsonify(rig.set_split(on))
        except Exception as exc:
            return jsonify(error=str(exc)), 502

    # ---------------- the radios themselves -----------------------------
    # Separate from /api/radio/*, which is "talk to the live one". These are
    # "which radios exist and which is live", and are the board's own: a card
    # should not be able to point the operator's rig at a different port.
    @app.get("/api/rigs")
    def rigs_list():
        c = cfg.data.get("radio", {})
        exe = rigsvc.find_rigctld(c.get("rigctld_path", ""))
        return jsonify({
            "rigs": rig.rigs(), "active": c.get("active", ""),
            "max": rigsvc.MAX_RIGS, "backends": list(rigsvc.BACKENDS),
            "bauds": rigsvc.BAUDS, "ports": rigsvc.serial_ports(),
            "rigctld": exe, "rigctld_path": c.get("rigctld_path", ""),
            "state": rig.state(),
            # Cached, so opening the sheet does not wait on PortAudio; the
            # Rescan button asks again.
            "audio": audiosvc.devices(),
        })

    @app.put("/api/rigs")
    def rigs_save():
        body = request.get_json(silent=True) or {}
        incoming = body.get("rigs")
        if not isinstance(incoming, list):
            return jsonify(error="rigs must be a list"), 400
        if len(incoming) > rigsvc.MAX_RIGS:
            return jsonify(error=f"at most {rigsvc.MAX_RIGS} radios"), 400
        c = cfg.data.setdefault("radio", {})
        c["rigs"] = [rigsvc.clean_rig(r) for r in incoming]
        if "rigctld_path" in body:
            c["rigctld_path"] = str(body.get("rigctld_path") or "").strip()
        want = body.get("active")
        cfg.save()
        # Re-open whichever is live: its settings may be what just changed.
        state = rig.select(want if want is not None else c.get("active", ""))
        BUS.publish("config.changed", _safe_config(), source="host")
        return jsonify({"ok": True, "rigs": c["rigs"],
                        "active": c.get("active", ""), "state": state})

    @app.get("/api/audio/devices")
    def audio_devices():
        """The PC's sound devices, for assigning one to a radio.

        Enumeration happens off this thread with a timeout (see
        `services/audio.py`): PortAudio on Windows can take seconds or hang
        outright, and a waitress thread is not something to spend on a
        driver that may never answer.
        """
        fresh = request.args.get("refresh") in ("1", "true", "yes")
        return jsonify(audiosvc.devices(refresh=fresh))

    @app.post("/api/rigs/select")
    def rigs_select():
        want = (request.get_json(silent=True) or {}).get("id", "")
        return jsonify(rig.select(str(want)))

    @app.post("/api/rigs/test")
    def rigs_test():
        """Open one, ask it one question, close it again."""
        body = request.get_json(silent=True) or {}
        return jsonify(rig.test(rigsvc.clean_rig(body)))

    @app.get("/api/rigs/models")
    def rigs_models():
        """Every rig Hamlib supports, asked of Hamlib."""
        return jsonify(rigsvc.hamlib_models(
            cfg.data.get("radio", {}).get("rigctld_path", "")))

    @app.post("/api/rigs/omnirig/dialog")
    def rigs_omnirig_dialog():
        """Open OmniRig's own settings window.

        The rig type, port and baud rate live there, and two programs cannot
        share a serial port - so this opens theirs rather than offering a
        second set of fields that would disagree with it.
        """
        slot = int((request.get_json(silent=True) or {}).get("slot") or 1)
        try:
            back = rigsvc.OmniRigBackend(slot=slot)
            back.configure()
            return jsonify({"ok": True, **back.info()})
        except rigsvc.RigError as exc:
            return jsonify(ok=False, error=str(exc)), 502
        except Exception as exc:                    # noqa: BLE001 - reported
            return jsonify(ok=False, error=str(exc)), 502

    @app.get("/api/bands")
    def bands_list():
        """The band table, so the browser does not keep its own.

        Three copies had already drifted - 5.3 MHz was 60m to the logbook and
        out of band to the rig - so a fourth in JavaScript would just be a
        fourth answer.
        """
        return jsonify({"bands": bandsvc.as_json(), "names": bandsvc.NAMES})

    @app.get("/api/radio/modes")
    @guard("radio")
    def radio_modes():
        return jsonify(MODES)

    @app.get("/api/lookup/<call>")
    @guard("lookup")
    def lookup(call):
        # An explicit refresh is the operator asking, by hand, right now: it
        # is the one moment worth spending a login that was refused before.
        refresh = request.args.get("refresh") in ("1", "true", "yes")
        if refresh:
            look.retry_auth()
        res = dict(look.lookup(call, refresh=refresh))
        # ?grid= overrides the locator for this answer only. A logged QSO
        # carries the grid that was actually worked, which is not always where
        # the operator lives now - a portable activation, or someone who has
        # moved. The cached lookup is left alone.
        want = (request.args.get("grid") or "").strip().upper()
        if want and gridlib.valid(want) and want.upper() != (res.get("locator") or "").upper():
            res["locator"] = want
            res["locator_from"] = "log"
            res["approximate"] = False
        my_grid = cfg.data["station"].get("grid", "")
        if res.get("locator") and gridlib.valid(my_grid) \
                and gridlib.valid(res["locator"]):
            res["path"] = gridlib.distance_bearing(my_grid, res["locator"])
        # The flag comes from the prefix, not from the provider's country
        # name: providers spell countries differently and some give none.
        res["iso"] = dxcc.iso_for(res.get("callsign") or call)
        res["worked_before"] = log.worked_before(call)
        BUS.publish("lookup.result", res, source="host")
        return jsonify(res)

    @app.post("/api/lookup/test")
    @guard("lookup")
    def lookup_test():
        """Try the configured provider once, on purpose, and say what it said."""
        look.retry_auth()
        c = cfg.data["callsign_lookup"]
        call = (cfg.data["station"].get("callsign") or "W1AW").upper()
        try:
            res = look.lookup(call, refresh=True)
        except Exception as exc:                # noqa: BLE001 - reported, not raised
            return jsonify({"ok": False, "error": str(exc)}), 502
        if res.get("note"):
            return jsonify({"ok": False, "error": res["note"]}), 502
        return jsonify({"ok": True, "provider": c.get("provider", ""),
                        "source": res.get("source", ""), "callsign": call,
                        "name": res.get("name", ""),
                        "locator": res.get("locator", "")})

    @app.get("/api/lookup/cache")
    @guard("lookup")
    def lookup_cache_size():
        return jsonify({"entries": look.cache_size(),
                        "days": cfg.data["callsign_lookup"].get("cache_days", 30)})

    @app.delete("/api/lookup/cache")
    @guard("lookup")
    def lookup_cache_clear():
        """Forget every remembered lookup, for an answer that has gone stale."""
        return jsonify({"ok": True, "cleared": look.clear_cache()})

    @app.get("/api/space/aurora")
    @guard("space")
    def space_aurora():
        """NOAA's aurora forecast, cached here so every tile does not poll it.

        Public data, no account. Thinned to the cells that carry information -
        the raw grid is mostly zero.
        """
        try:
            threshold = max(1, min(100, int(request.args.get("threshold", 5))))
        except ValueError:
            threshold = 5
        return jsonify(aurora.get(threshold))

    @app.get("/api/space/drap")
    @guard("space")
    def space_drap():
        """D-region absorption: the highest frequency being absorbed.

        Zero over the whole planet is the normal, good case, so the answer
        carries `quiet` rather than leaving an empty list looking broken.
        """
        try:
            threshold = max(0.1, float(request.args.get("threshold", 1)))
        except ValueError:
            threshold = 1.0
        return jsonify(drap.get(threshold))

    @app.get("/api/space/muf")
    @guard("space")
    def space_muf():
        """Measured MUF and foF2 from the world's ionosondes.

        Points, not a field: the smooth MUF maps everyone knows are an
        interpolation of exactly these stations.
        """
        return jsonify(ionosondes.get())

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

    def _flagged(rows):
        """Attach what can be worked out rather than stored: the country a
        callsign's prefix implies, and how far away it was.

        Derived at serve time on purpose. The prefix table improves, and the
        station's own grid can change - a log written last year should get
        today's answer to both, not the answer that was true when it was
        written.
        """
        mine = cfg.data["station"].get("grid", "")
        home = gridlib.valid(mine)
        for r in rows:
            r["iso"] = dxcc.iso_for(r.get("call", ""))
            r["km"] = None
            loc = (r.get("gridsquare") or "").strip()
            if home and loc and gridlib.valid(loc):
                try:
                    r["km"] = gridlib.distance_bearing(mine, loc)["km"]
                except ValueError:
                    pass
        return rows

    @app.get("/api/log")
    @guard("log")
    def log_list():
        q = request.args.get("q")
        return jsonify(_flagged(log.search(q) if q else log.recent(
            int(request.args.get("limit", 25)))))

    @app.get("/api/log/page")
    @guard("log")
    def log_page():
        def num(name, fallback):
            try:
                return int(request.args.get(name, fallback))
            except (TypeError, ValueError):
                return fallback
        # 0 means "no limit": the table offers an All choice, and a log that
        # fits on one page should not be paged.
        limit = num("limit", 30)
        got = log.page(request.args.get("q", ""),
                       limit if limit > 0 else 10 ** 7, num("offset", 0))
        got["rows"] = _flagged(got["rows"])
        return jsonify(got)

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
        # Counts, not one number: importing the same file twice should say
        # "nothing new" rather than looking like it did the work again.
        res = log.import_adif(request.get_data(as_text=True))
        return jsonify(imported=res["added"], **res)

    # ---------------- logbook sync -------------------------------------
    def _lotw() -> Lotw:
        c = cfg.data.get("logbook_sync", {})
        return Lotw(c.get("lotw_user", ""), c.get("lotw_password", ""),
                    c.get("tqsl_path", ""), c.get("station_location", ""))

    def _qrz() -> QrzLogbook:
        return QrzLogbook(cfg.data.get("logbook_sync", {}).get("qrz_api_key", ""))

    def _iso(yyyymmdd: str) -> str:
        """The UI's YYYYMMDD as the ISO instant WRL asks for.

        LoTW and QRZ take the ADIF form; World Radio League keeps one instant
        rather than a date and a time, so the conversion lives here rather
        than making the form ask twice for the same thing.
        """
        d = "".join(ch for ch in str(yyyymmdd or "") if ch.isdigit())
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}T00:00:00Z" if len(d) >= 8 else ""

    def _wrl() -> WrlLogbook:
        c = cfg.data.get("logbook_sync", {})
        return WrlLogbook(c.get("wrl_api_key", ""), c.get("wrl_logbook_id", ""))

    @app.get("/api/log/sync")
    @guard("log")
    def sync_state():
        c = cfg.data.get("logbook_sync", {})
        lotw = _lotw()
        newest = log.max_id()
        pending = lambda k: max(0, len(log.after_id(int(c.get(k) or 0))))  # noqa: E731
        return jsonify({
            "state": syncer.state,
            "newest_id": newest,
            "lotw": {
                "configured": bool(c.get("lotw_user") and c.get("lotw_password")),
                # Push is a separate capability, not a separate credential:
                # LoTW only takes signed uploads and TQSL is what signs them.
                "can_push": bool(lotw.tqsl()),
                "tqsl": lotw.tqsl() or "",
                "pushed_id": int(c.get("lotw_pushed_id") or 0),
                "pending": pending("lotw_pushed_id"),
            },
            "qrz": {"configured": bool(c.get("qrz_api_key")),
                    "pushed_id": int(c.get("qrz_pushed_id") or 0),
                    "pending": pending("qrz_pushed_id")},
            "wrl": {"configured": bool(c.get("wrl_api_key")),
                    "pushed_id": int(c.get("wrl_pushed_id") or 0),
                    "pending": pending("wrl_pushed_id")},
        })

    @app.post("/api/log/sync/<service>/<direction>")
    @guard("log")
    def sync_run(service, direction):
        body = request.get_json(silent=True) or {}
        since = str(body.get("since") or "").strip()
        # Uploads default to counting rather than sending. The far end has no
        # undo, and "push my log" to a logbook with ten years in it is not
        # something to find out you meant differently afterwards.
        dry_run = bool(body.get("dry_run", True))
        # An automatic upload sends only what has been logged since the last
        # one. The marker is per service and lives in config, so a restart
        # does not re-send the whole log.
        new_only = bool(body.get("new_only"))
        mine = cfg.data["station"].get("callsign", "")

        def to_send(service):
            if new_only:
                mark = int(cfg.data.get("logbook_sync", {})
                           .get(f"{service}_pushed_id") or 0)
                return log.after_id(mark)
            return log.since(since)

        def mark_sent(service):
            cfg.data.setdefault("logbook_sync", {})[f"{service}_pushed_id"] =                 log.max_id()
            cfg.save()

        def pull_lotw():
            text = _lotw().pull(since=since, owncall=mine)
            res = log.import_adif(text)
            res["message"] = (f"{res['added']} new, {res['updated']} updated, "
                              f"{res['skipped']} already had")
            return res

        def pull_qrz():
            res = log.import_adif(_qrz().pull())
            res["message"] = (f"{res['added']} new, {res['updated']} updated, "
                              f"{res['skipped']} already had")
            return res

        def push_lotw():
            rows = to_send("lotw")
            if dry_run:
                return {"would_send": len(rows), "dry_run": True,
                        "message": f"{len(rows)} QSOs would go to TQSL"
                                   f"{' from ' + since if since else ''}. "
                                   f"Untick dry run to send them."}
            if not rows:
                return {"sent": 0, "message": "nothing to send"}
            adif = "\n".join(log.export_adif_record(r, my_callsign=mine)
                             for r in rows)
            out = _lotw().push("<EOH>\n" + adif + "\n")
            out["message"] = f"TQSL accepted {len(rows)} QSOs"
            return out

        def push_qrz():
            rows = to_send("qrz")
            if dry_run:
                return {"would_send": len(rows), "dry_run": True,
                        "message": f"{len(rows)} QSOs would be sent to QRZ"
                                   f"{' from ' + since if since else ''}. "
                                   f"Untick dry run to send them."}
            book, sent, failed = _qrz(), 0, []
            for rec in rows:
                one = log.export_adif_record(rec, my_callsign=mine)
                try:
                    book.push_record(one)
                    sent += 1
                except SyncError as exc:
                    # QRZ rejects a QSO it already has. That is the normal
                    # case on a re-run, not a failure worth stopping for.
                    if "duplicate" in str(exc).lower():
                        continue
                    failed.append(f"{rec.get('call')}: {exc}")
                    if len(failed) > 20:
                        break
            if new_only and not failed:
                mark_sent("qrz")
            return {"sent": sent, "failed": failed,
                    "message": f"{sent} sent"
                               + (f", {len(failed)} refused" if failed else "")}

        def pull_wrl():
            res = log.import_adif(_wrl().pull(since=_iso(since)))
            res["message"] = (f"{res['added']} new, {res['updated']} updated, "
                              f"{res['skipped']} already had")
            return res

        def push_wrl():
            rows = to_send("wrl")
            if dry_run:
                return {"would_send": len(rows), "dry_run": True,
                        "message": f"{len(rows)} QSOs would be sent to World "
                                   f"Radio League"
                                   f"{' from ' + since if since else ''}. "
                                   f"Untick dry run to send them."}
            # One contact per request, 60 a minute: their API has no bulk
            # endpoint and says so - a whole log belongs in their own ADIF
            # import. Pacing here keeps an ordinary push inside the budget
            # instead of earning a 429 halfway through.
            book, sent, dupes, failed = _wrl(), 0, 0, []
            for i, rec in enumerate(rows):
                if i:
                    time.sleep(book.WRITE_PAUSE)
                try:
                    out = book.push_record(rec, my_callsign=mine)
                    sent += 1
                    dupes += 1 if out.get("duplicate") else 0
                except SyncError as exc:
                    failed.append(f"{rec.get('call')}: {exc}")
                    # A rate limit will not clear by trying harder, and the
                    # rest of the log is still here for the next run.
                    if "rate limiting" in str(exc) or len(failed) > 20:
                        break
            if new_only and not failed:
                mark_sent("wrl")
            return {"sent": sent, "failed": failed,
                    "message": f"{sent} sent"
                               + (f", {dupes} already there" if dupes else "")
                               + (f", {len(failed)} refused" if failed else "")}

        def test_wrl():
            return _wrl().test()

        def test_lotw():
            return _lotw().test()

        def test_qrz():
            return _qrz().test()

        jobs = {("lotw", "pull"): pull_lotw, ("qrz", "pull"): pull_qrz,
                ("wrl", "pull"): pull_wrl,
                ("lotw", "push"): push_lotw, ("qrz", "push"): push_qrz,
                ("wrl", "push"): push_wrl,
                ("lotw", "test"): test_lotw, ("qrz", "test"): test_qrz,
                ("wrl", "test"): test_wrl}
        fn = jobs.get((service, direction))
        if fn is None:
            return jsonify(error=f"no {direction} for {service}"), 404
        try:
            return jsonify(syncer.start(f"{service} {direction}", fn))
        except SyncError as exc:
            return jsonify(error=str(exc)), 409

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
                  cache_days=c.get("cache_days", 30),
                  account=c.get("account", ""))


# The tile is where somebody writing a card is looking when it does not
# work, so it is where the reason belongs - not only in the log, which they
# have to be told exists before it helps.
#
# **Escape it.** This is a card own output rendered by the *host*, on the
# host origin. Unescaped, a traceback "in <module>" is read as a tag and
# silently vanishes - the single most useful word on the line - and anything
# a card prints that contains a script tag would run.
def _page(body: str, refresh: int = 0) -> str:
    bits = ["<!doctype html><meta charset=utf-8>",
            "<link rel=stylesheet href=/static/css/app.css>"]
    if refresh:
        bits.append(f"<meta http-equiv=refresh content={refresh}>")
    bits.append("<style>body{font:13px system-ui,sans-serif;padding:14px;"
                "margin:0}pre{font:12px/1.45 ui-monospace,monospace;"
                "white-space:pre-wrap;word-break:break-word;margin:8px 0;"
                "padding:8px;background:var(--panel-2);border-radius:6px;"
                "max-height:60vh;overflow:auto}"
                ".why{color:var(--alert);font-weight:600;margin:0}"
                ".hint{color:var(--muted);margin:6px 0 0}</style>")
    bits.append(body)
    return "".join(bits)


def _card_error(card):
    return _page(
        f'<p class="why">{escape(card.id)} cannot load</p>'
        f"<pre>{escape(card.error)}</pre>"
        f'<p class="hint">Its manifest is {escape(str(card.path))}'
        f"/card.json. Fix it, then Cards &rarr; Rescan.</p>")


def _starting(worker):
    return _page(
        f'<p class="hint">Starting {escape(worker.card.id)}&hellip;</p>',
        refresh=1)


def _worker_down(worker, home=""):
    """A card that will not run, said where its author is looking.

    It refreshes while the supervisor is still going to try again, so a card
    that gets fixed comes back on its own; once it has given up there is
    nothing to wait for and the page stops reloading itself.
    """
    tail = ("\n".join(worker.log[-20:]) or "(no output)")
    coming_back = not worker.status.startswith("failed")
    log = f"{home}/hamdash.log" if home else "hamdash.log"
    return _page(
        f'<p class="why">{escape(worker.card.id)} is '
        f"{escape(worker.status)}</p>"
        f"<pre>{escape(tail)}</pre>"
        f'<p class="hint">'
        + ("Fix it and it will come back by itself. " if coming_back else
           "It has stopped trying. Fix it, then restart hamdash. ")
        + f"Everything it prints also goes to {escape(log)}.</p>",
        refresh=5 if coming_back else 0)
