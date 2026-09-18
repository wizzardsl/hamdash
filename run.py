#!/usr/bin/env python3
"""hamdash launcher.

    python run.py              native desktop window (needs pywebview)
    python run.py --browser    serve only, open it yourself
    python run.py --port 8765  pin the port

The HTTP server is an implementation detail: bound to loopback, started in a
background thread, and shut down with the window. No terminal, no URL to
remember, nothing for the user to administer.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import signal
import socket
import sys
import threading
import time
import traceback
import urllib.request

from hamdash import __version__
from hamdash.app import create_app
from hamdash.config import Config
from hamdash import windows as native_windows


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for(url: str, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url + "/api/health", timeout=0.5)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    return False


class _Truncating(logging.Formatter):
    """Cap one record at a readable length.

    A runaway repr (pywebview walking a .NET object graph, a card dumping a
    huge payload) can otherwise emit megabytes per line, which fills the log,
    floods the console and makes the real error impossible to find.
    """

    LIMIT = 2000

    def format(self, record):
        out = super().format(record)
        if len(out) > self.LIMIT:
            out = f"{out[:self.LIMIT]}… [truncated, {len(out)} chars]"
        return out


def setup_logging(cfg) -> None:
    """Log to a file as well as the console.

    On Windows the app is usually launched without a console, so a crash
    leaves nothing behind to read. This is where to look, and what to send
    when something goes wrong: <config dir>/hamdash.log
    """
    # Windows consoles are cp1252 by default, so a stray non-ASCII byte in a
    # card's output turns into "--- Logging error ---" instead of a message.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    path = cfg.home / "hamdash.log"
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=1_000_000, backupCount=3, encoding="utf-8",
        errors="replace")
    handler.setFormatter(_Truncating(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(_Truncating("%(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(console)

    def hook(exc_type, exc, tb):
        logging.getLogger("hamdash").error(
            "unhandled exception\n%s", "".join(
                traceback.format_exception(exc_type, exc, tb)))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook

    def thread_hook(args):
        logging.getLogger("hamdash").error(
            "unhandled exception in thread %s\n%s", args.thread_name,
            "".join(traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback)))

    threading.excepthook = thread_hook

    log = logging.getLogger("hamdash")
    log.info("hamdash %s starting: python %s on %s, config in %s",
             __version__, sys.version.split()[0], sys.platform, cfg.home)
    try:
        import webview
        try:
            from importlib.metadata import version
            ver = version("pywebview")
        except Exception:  # noqa: BLE001
            ver = getattr(webview, "__version__", "unknown")
        log.info("pywebview %s, gui backend %s", ver,
                 getattr(webview, "guilib", None) or "not started yet")
    except Exception as exc:  # noqa: BLE001
        log.info("pywebview not importable (%s)", exc)


def main() -> int:
    ap = argparse.ArgumentParser(description="ham radio dashboard")
    ap.add_argument("--browser", action="store_true",
                    help="serve only, don't open a native window")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0, help="0 picks a free port")
    ap.add_argument("--home", default=None, help="config directory override")
    ap.add_argument("--threads", type=int, default=64,
                    help="server threads; each open event stream uses one")
    args = ap.parse_args()

    port = args.port or (8765 if args.browser else free_port())
    cfg = Config(args.home)
    setup_logging(cfg)
    app, shutdown = create_app(cfg, host=args.host, port=port,
                               native=not args.browser)
    url = f"http://{args.host}:{port}"

    from waitress import serve as wsgi_serve
    # Event streams hold a thread each for their (bounded) life: the board,
    # every card page, and every card process keeps one. channel_timeout sits
    # just above the stream lifetime so an abandoned socket is reclaimed
    # promptly instead of squatting a thread for minutes.
    kwargs = dict(host=args.host, port=port, threads=max(16, args.threads),
                  channel_timeout=70, clear_untrusted_proxy_headers=True)

    if args.browser:
        print(f"hamdash on {url}  (config in {cfg.home})")
        try:
            wsgi_serve(app, **kwargs)
        finally:
            shutdown()
        return 0

    try:
        import webview
    except ImportError:
        print("pywebview is not installed; falling back to --browser mode.\n"
              "  pip install pywebview   for the desktop window", file=sys.stderr)
        print(f"hamdash on {url}")
        try:
            wsgi_serve(app, **kwargs)
        finally:
            shutdown()
        return 0

    threading.Thread(target=wsgi_serve, kwargs={"app": app, **kwargs},
                     daemon=True, name="hamdash-http").start()
    if not wait_for(url):
        print("server did not come up in time; check the output above",
              file=sys.stderr)
        shutdown()
        return 1

    native_windows.configure(url)
    popouts = native_windows.PopoutApi()

    def stop_everything():
        native_windows.close_all()
        shutdown()

    signal.signal(signal.SIGINT, lambda *_: (stop_everything(), sys.exit(0)))
    window = webview.create_window("hamdash", url, width=1440, height=900,
                                   min_size=(960, 640), js_api=popouts)
    native_windows.attach_main(window)
    window.events.closed += stop_everything
    webview.start()
    stop_everything()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
