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
from urllib.parse import quote

log = logging.getLogger("hamdash.windows")

# --- state lives here, deliberately out of reach of api introspection -----
_LOCK = threading.Lock()
_MAIN = None                      # the board window
_WINDOWS: dict[str, object] = {}  # uid -> pywebview Window
_GEOMETRY: dict[str, dict] = {}   # uid -> last known size/position
_HOST_URL = "http://127.0.0.1:8765"


def configure(host_url: str) -> None:
    global _HOST_URL
    _HOST_URL = host_url.rstrip("/")


def attach_main(window) -> None:
    global _MAIN
    _MAIN = window


def close_all() -> None:
    with _LOCK:
        wins = list(_WINDOWS.items())
        _WINDOWS.clear()
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
        _GEOMETRY[uid] = {"w": win.width, "h": win.height, "x": win.x, "y": win.y}
    except Exception:  # noqa: BLE001 - not every backend reports geometry
        pass


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
        geo = _GEOMETRY.get(uid, {})
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
