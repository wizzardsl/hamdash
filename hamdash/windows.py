"""Native pop-out windows, exposed to the UI as window.pywebview.api.*.

A popped-out card is not a copy of the tile: it is the same card process,
reached through the same /cards/<id>/ proxy, just painted in a second OS
window. Nothing about the card changes, which is the whole point of running
cards as independent programs.

IMPORTANT, learned the hard way: pywebview builds the JavaScript proxy for a
js_api object by INTROSPECTING it, and on the Windows (winforms/.NET) backend
that walk follows public attributes. Hang a pywebview Window on the api object
and the walk goes

    api.main -> Window.native -> Form.AccessibilityObject.Bounds
             -> Rectangle.Empty -> Rectangle.Empty -> ... forever

which is "maximum recursion depth exceeded", megabytes of log spam, and a
frozen UI. So this class holds NO state: every window handle and geometry
record lives in the module-level privates below, and the class exposes nothing
but the methods JavaScript is meant to call. Do not add attributes to it.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger("hamdash.windows")

# --- state lives here, deliberately out of reach of api introspection -----
_LOCK = threading.Lock()
_MAIN = None                      # the board window
_WINDOWS: dict[str, object] = {}  # uid -> pywebview Window
_GEOMETRY: dict[str, dict] = {}   # uid -> last known size/position
_HOST_URL = "http://127.0.0.1:8765"
_STORE: Path | None = None        # where _GEOMETRY is kept across restarts
_SAVE_TIMER = None
SAVE_DELAY = 1.0                  # a drag fires "moved" for every pixel
KEEP = 60                         # windows remembered, newest first
VISIBLE = 80                      # px of a window that must be on a screen


def configure(host_url: str, store: Path | None = None) -> None:
    """`store` is `<home>/windows.json`. Where a window sits is a fact
    about this machine's screens, not about the board, so it lives beside
    the config rather than in layout.json - the same board opened on
    another machine must not put windows where that machine has none."""
    global _HOST_URL, _STORE
    _HOST_URL = host_url.rstrip("/")
    if store is not None:
        _STORE = Path(store)
        _GEOMETRY.update(load(_STORE))


def load(path: Path) -> dict:
    """Saved positions, or nothing: a file somebody broke is a reason to
    forget where windows were, never a reason not to start."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for uid, g in (raw.items() if isinstance(raw, dict) else []):
        try:
            out[str(uid)] = {k: int(g[k]) for k in ("w", "h", "x", "y")
                             if g.get(k) is not None}
            out[str(uid)]["at"] = float(g.get("at") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def save(path: Path, geometry: dict) -> None:
    newest = sorted(geometry.items(), key=lambda kv: -kv[1].get("at", 0))[:KEEP]
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(newest), indent=1), encoding="utf-8")
    tmp.replace(path)


def _save_soon() -> None:
    global _SAVE_TIMER
    if _STORE is None:
        return

    def run():
        global _SAVE_TIMER
        with _LOCK:
            _SAVE_TIMER = None
            snapshot = dict(_GEOMETRY)
        try:
            save(_STORE, snapshot)
        except OSError as exc:
            log.info("could not save pop-out positions: %s", exc)

    with _LOCK:
        if _SAVE_TIMER is None:
            _SAVE_TIMER = threading.Timer(SAVE_DELAY, run)
            _SAVE_TIMER.daemon = True
            _SAVE_TIMER.start()


def place(geo: dict, screens: list) -> dict:
    """Where to open a remembered window: its size and position, or its
    size alone when the position is no longer on any screen.

    `screens` is [(x, y, w, h), ...] in the units pywebview uses. A window
    last seen on a monitor that has since been unplugged would otherwise
    open off every screen there is - present, running, and impossible to
    reach. Its size is shrunk to fit the screen it lands on."""
    w, h = geo.get("w"), geo.get("h")
    x, y = geo.get("x"), geo.get("y")
    out = {"w": w, "h": h, "x": None, "y": None}
    if not screens:
        return {**out, "x": x, "y": y}
    home = None
    if x is not None and y is not None:
        for sx, sy, sw, sh in screens:
            # Its title bar has to be reachable: the top-left corner on the
            # screen, and enough of the top edge to grab.
            if sx <= x <= sx + sw - VISIBLE and sy <= y <= sy + sh - VISIBLE // 2:
                home = (sx, sy, sw, sh)
                break
    sx, sy, sw, sh = home or screens[0]
    if w:
        out["w"] = min(int(w), sw)
    if h:
        out["h"] = min(int(h), sh)
    if home:
        out["x"], out["y"] = int(x), int(y)
    return out


def attach_main(window) -> None:
    global _MAIN
    _MAIN = window


def close_all() -> None:
    with _LOCK:
        wins = list(_WINDOWS.items())
        _WINDOWS.clear()
    # Shutting down: the delayed save would never run, and these are the
    # positions that matter - the windows open when hamdash was closed.
    for uid, win in wins:
        _remember(uid, win)
    if _STORE is not None and wins:
        try:
            save(_STORE, dict(_GEOMETRY))
        except OSError as exc:
            log.info("could not save pop-out positions: %s", exc)
    for uid, win in wins:
        try:
            win.destroy()
        except Exception as exc:  # noqa: BLE001
            log.info("could not close pop-out %s: %s", uid, exc)


def _notify(fn: str, *args) -> None:
    if _MAIN is None:
        return
    payload = ", ".join(json.dumps(a) for a in args)
    try:
        _MAIN.evaluate_js(f"window.HDPopout && HDPopout.{fn}({payload})")
    except Exception as exc:  # noqa: BLE001 - board window may already be gone
        log.debug("notify %s failed: %s", fn, exc)


def _remember(uid: str, win) -> None:
    try:
        _GEOMETRY[uid] = {"w": win.width, "h": win.height, "x": win.x,
                          "y": win.y, "at": time.time()}
    except Exception:  # noqa: BLE001 - not every backend reports geometry
        return
    _save_soon()


def _screens() -> list:
    try:
        import webview
        return [(s.x, s.y, s.width, s.height) for s in webview.screens]
    except Exception:  # noqa: BLE001 - no toolkit, or one that cannot say
        return []


def _bind(win, event: str, handler) -> None:
    try:
        getattr(win.events, event).__iadd__(handler)
    except Exception as exc:  # noqa: BLE001
        log.debug("could not bind %s: %s", event, exc)


class PopoutApi:
    """Bridge handed to pywebview as js_api. Methods only - see module note."""

    def popout_backend(self) -> dict:
        return {"backend": "pywebview", "on_top": True, "multiple": True}

    def popout_open(self, opts: dict) -> dict:
        import webview

        opts = opts or {}
        uid = str(opts.get("uid") or opts.get("card") or "popout")
        title = str(opts.get("title") or uid)
        card = str(opts.get("card", ""))
        url = str(opts.get("url") or "")
        geo = place(_GEOMETRY.get(uid, {}), _screens())
        target = (f"{_HOST_URL}/popout?uid={quote(uid)}&card={quote(card)}"
                  f"&title={quote(title)}&native=1"
                  + (f"&url={quote(url)}" if url else ""))

        with _LOCK:
            if uid in _WINDOWS:
                return {"ok": True, "already": True}
        try:
            win = webview.create_window(
                title, target,
                width=int(opts.get("w") or geo.get("w") or 520),
                height=int(opts.get("h") or geo.get("h") or 420),
                x=geo.get("x"), y=geo.get("y"),
                on_top=bool(opts.get("on_top", True)),
                resizable=True, min_size=(280, 200),
                js_api=PopoutApi())
        except Exception as exc:  # noqa: BLE001
            log.error("pop-out window for %s failed: %s", uid, exc)
            return {"ok": False, "error": str(exc)}

        with _LOCK:
            _WINDOWS[uid] = win

        def remember(*_a):
            _remember(uid, win)

        def closed():
            _remember(uid, win)
            with _LOCK:
                _WINDOWS.pop(uid, None)
            # a popped card with no window would be unreachable, so put it back
            _notify("onClosed", uid)

        _bind(win, "resized", remember)
        _bind(win, "moved", remember)
        _bind(win, "closed", closed)
        log.info("popped out %s as %r", card or uid, title)
        return {"ok": True, "uid": uid}

    def popout_close(self, uid: str) -> dict:
        with _LOCK:
            win = _WINDOWS.pop(str(uid), None)
        if win is None:
            return {"ok": False, "error": "no such window"}
        try:
            win.destroy()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def popout_set_on_top(self, uid: str, on: bool) -> dict:
        win = _WINDOWS.get(str(uid))
        if win is None:
            return {"ok": False, "error": "no such window"}
        try:
            win.on_top = bool(on)
            return {"ok": True, "on_top": bool(on)}
        except Exception as exc:  # noqa: BLE001 - platform dependent
            return {"ok": False, "error": f"always-on-top not available here ({exc})"}

    def popout_list(self) -> list:
        return [{"uid": u, **_GEOMETRY.get(u, {})} for u in list(_WINDOWS)]

    def popout_request_pop_in(self, uid: str) -> dict:
        _notify("onPopInRequest", str(uid))
        return self.popout_close(uid)

    def open_external(self, url: str) -> dict:
        """Open a link in the user's real browser.

        Without this, a card's target=_blank link or window.open() inside a
        webview becomes an unhandled new-window request, and Windows offers to
        find a Store app for it. Scheme is allow-listed so a card can't hand
        the shell a file:// or custom protocol URL.
        """
        import webbrowser

        url = str(url or "")
        if url.split(":", 1)[0].lower() not in ("http", "https", "mailto"):
            return {"ok": False, "error": f"refused to open {url[:60]!r}"}
        try:
            webbrowser.open(url)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
