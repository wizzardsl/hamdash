#!/usr/bin/env python3
"""hamdash launcher.

    python run.py              native desktop window (needs pywebview)
    python run.py --browser    serve only, open it yourself
    python run.py --port 8765  pin the port
    python run.py --run-card <entry>   run one card in this process

--run-card exists for the frozen build, where sys.executable is this binary
rather than an interpreter, so the app has to be its own card runner. See
proc.card_command().

The HTTP server is an implementation detail: bound to loopback, started in a
background thread, and shut down with the window. No terminal, no URL to
remember, nothing for the user to administer.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import errno
import json
import os
import signal
import socket
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

from hamdash import __version__
from hamdash.app import create_app, reachable
from hamdash.config import Config
from hamdash import windows as native_windows


def ensure_streams() -> None:
    """Give print() somewhere to go in a windowed build.

    A PyInstaller --windowed app has no console, and sys.stdout/stderr are
    None. Every print() in the host then raises AttributeError on NoneType -
    including the one in proc._drain that relays a card's output, which would
    kill the drain thread and lose exactly the diagnostics the log exists for.
    A real stream (a pipe from the supervisor, or a console) is left alone.
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def run_card(entry: str) -> int:
    """Be the interpreter for one card, then exit with it.

    A card is a plain script that expects to be __main__ with its own
    directory as cwd (the supervisor sets that). Nothing else from the host
    may start here: this process is the card, and it binds the card's port.
    """
    import runpy

    path = Path(entry)
    if not path.exists():
        print(f"--run-card: {path} does not exist (cwd {Path.cwd()})",
              file=sys.stderr)
        return 2
    # `python card.py` puts the script's own directory on sys.path; runpy
    # does not. Without this a card that splits itself over more than one
    # file imports fine from source and fails only in a frozen build, which
    # is exactly the difference that makes a broken spec look healthy.
    here = str(path.resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    sys.argv = [str(path)]
    runpy.run_path(str(path), run_name="__main__")
    return 0


def why_not_bindable(host: str, port: int) -> str:
    """"" if the board can listen there, else the reason in plain words.

    Checked before anything is built, because waitress fails deep: the
    operator gets a traceback ending in `PermissionError: [WinError
    10013] An attempt was made to access a socket in a way forbidden by
    its access permissions`, which does not name the port and, on
    Windows, does not say the thing that is actually true - that
    something already has it. Whoever passes --port for a fixed address
    is exactly who meets this.

    **SO_REUSEADDR on POSIX, and only there.** waitress sets it on the
    socket it really listens on, and without it here the probe was the
    stricter of the two: on Linux the connections a stopped instance
    leaves in TIME_WAIT refuse a plain bind for about a minute, so a
    restart said "something is already using it" with nothing listening
    at all. With it, a live listener still refuses, which is the case
    that matters. Not on Windows: there SO_REUSEADDR lets a bind succeed
    on top of somebody's live listener, which would turn this check into
    one that never fires - and Windows does not hold TIME_WAIT against a
    bind in the first place.
    """
    s = socket.socket()
    try:
        if os.name != "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return ""
    except OSError as exc:
        if getattr(exc, "winerror", None) in (10013, 10048) or \
                exc.errno in (errno.EADDRINUSE, errno.EACCES):
            return "something is already using it"
        return str(exc)
    finally:
        s.close()

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


RUNNING = "running.json"


def note_running(cfg, host: str, port: int, url: str) -> None:
    """Leave a note saying where this instance is listening.

    So that `--stop` has something to talk to. The port is often not a
    fixed one - a native window takes whatever is free - and the bind
    address may not be loopback, so guessing is no good.
    """
    try:
        (cfg.home / RUNNING).write_text(json.dumps({
            "pid": os.getpid(), "host": host, "port": port, "url": url,
            "started": time.time(),
        }), encoding="utf-8")
    except OSError:              # a read-only home is not worth failing on
        pass


def clear_running(cfg) -> None:
    try:
        (cfg.home / RUNNING).unlink(missing_ok=True)
    except OSError:
        pass


def stop_running(cfg) -> int:
    """`python run.py --stop`: ask the instance recorded here to close.

    The point is that a kill cannot be caught. On Windows a headless
    instance has no window to close and taskkill needs /F, which skips
    the signal handlers - so the cards are not stopped and `rig.stop()`
    never runs, which is what takes a transmitter off the air. This asks
    properly instead.
    """
    import requests
    note = cfg.home / RUNNING
    if not note.exists():
        print(f"no running hamdash recorded in {cfg.home}", file=sys.stderr)
        return 1
    try:
        at = json.loads(note.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"{note} is unreadable; deleting it", file=sys.stderr)
        clear_running(cfg)
        return 1
    url = at.get("url") or f"http://127.0.0.1:{at.get('port')}"
    try:
        r = requests.post(f"{url}/api/shutdown", timeout=10)
    except requests.RequestException as exc:
        print(f"nothing answering at {url} ({exc}); clearing the note",
              file=sys.stderr)
        clear_running(cfg)
        return 1
    if r.status_code == 403:
        print(r.json().get("error", "refused"), file=sys.stderr)
        return 1
    if not r.ok:
        print(f"{url} said {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return 1
    print(f"asked hamdash on {url} to stop")
    # Wait for it to actually go, so a script can restart it straight after.
    for _ in range(100):
        time.sleep(0.1)
        try:
            requests.get(f"{url}/api/health", timeout=1)
        except requests.RequestException:
            clear_running(cfg)
            print("stopped")
            return 0
    print("it is still answering; it may be stuck", file=sys.stderr)
    return 1

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
        # `threading.ExceptHookArgs` carries `thread`, not `thread_name`.
        # It was the latter, so this hook raised AttributeError every time
        # it fired - and what reached the log was that AttributeError
        # instead of the exception it exists to record. Every unhandled
        # exception in a thread has been lost since it was written, which
        # is the worst possible thing for the one piece of machinery whose
        # whole job is to make silent failures visible. `thread` is None
        # when the thread object has already gone, which happens during
        # interpreter shutdown - which is exactly when this fired.
        thread = getattr(args, "thread", None)
        name = thread.name if thread is not None else "unknown"
        try:
            logging.getLogger("hamdash").error(
                "unhandled exception in thread %s\n%s", name,
                "".join(traceback.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback)))
        except Exception:                               # noqa: BLE001
            # Logging can be half torn down at shutdown. A diagnostic that
            # raises is worse than one that gives up.
            pass

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
    # Before argparse and before any host setup: when frozen, this binary is
    # also the card runner, and a card process must not build a Config, take
    # the log file or start a server. proc.card_command() is the other half.
    ensure_streams()
    if len(sys.argv) >= 3 and sys.argv[1] == "--run-card":
        return run_card(sys.argv[2])

    ap = argparse.ArgumentParser(description="ham radio dashboard")
    ap.add_argument("--browser", action="store_true",
                    help="serve only, don't open a native window")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0, help="0 picks a free port")
    ap.add_argument("--home", default=None, help="config directory override")
    ap.add_argument("--threads", type=int, default=64,
                    help="server threads; each open event stream uses one")
    ap.add_argument("--stop", action="store_true",
                    help="ask a running hamdash on this machine to stop")
    args = ap.parse_args()

    if args.stop:
        return stop_running(Config(args.home))

    port = args.port or (8765 if args.browser else free_port())

    why = why_not_bindable(args.host, port)
    if why:
        print(f"cannot listen on {args.host}:{port} - {why}",
              file=sys.stderr)
        print("  pick another with --port, or stop whatever has it",
              file=sys.stderr)
        return 2
    cfg = Config(args.home)
    setup_logging(cfg)
    # /api/shutdown calls this. close_once is defined below; the hook is
    # read when the route fires, which is long after. Ending the process
    # belongs here rather than in the app: by this point the cards are
    # stopped and the rig is closed - which is what takes a transmitter
    # off the air - so what is left to unwind is a blocking server with
    # no clean way to be stopped from one of its own threads.
    def quit_now():
        close_once()
        logging.shutdown()
        os._exit(0)

    app, shutdown = create_app(cfg, host=args.host, port=port,
                               native=not args.browser, on_quit=quit_now)
    # The page the window opens, or that is printed for the
    # operator - the bind address may be a wildcard, which is
    # not something a browser can go to.
    url = f"http://{reachable(args.host)}:{port}"

    # Closing has to happen once, and it has to happen on the ways out that
    # actually occur. The `finally` around the server covers a clean return;
    # it does not run when the process is interrupted, which on Windows is
    # most of the time - and the rig manager's shutdown is what takes a
    # transmitter off the air. A hard kill still cannot be caught by
    # anything, which is why the radio's own timeout is the real backstop.
    note_running(cfg, args.host, port, url)

    closing = threading.Lock()
    closed = []

    def close_once():
        with closing:
            if closed:
                return
            closed.append(True)
        clear_running(cfg)
        try:
            native_windows.close_all()
        except Exception:                               # noqa: BLE001
            pass
        shutdown()

    def _on_signal(signum, _frame):
        close_once()
        sys.exit(0)

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass            # not the main thread, or not deliverable here

    from waitress import serve as wsgi_serve
    # Event streams hold a thread each for their (bounded) life: the board,
    # every card page, and every card process keeps one. channel_timeout sits
    # just above the stream lifetime so an abandoned socket is reclaimed
    # promptly instead of squatting a thread for minutes.
    kwargs = dict(host=args.host, port=port, threads=max(16, args.threads),
                  channel_timeout=70, clear_untrusted_proxy_headers=True)

    if args.browser:
        print(f"hamdash on {url}  (config in {cfg.home})")
        # Headless has no window to close, and a kill cannot be caught -
        # which matters because closing is what unkeys the radio.
        print("stop it with Ctrl-C here, or `python run.py --stop` "
              "from another window on this machine")
        try:
            wsgi_serve(app, **kwargs)
        finally:
            close_once()
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
            close_once()
        return 0

    threading.Thread(target=wsgi_serve, kwargs={"app": app, **kwargs},
                     daemon=True, name="hamdash-http").start()
    if not wait_for(url):
        print("server did not come up in time; check the output above",
              file=sys.stderr)
        close_once()
        return 1

    native_windows.configure(url)
    popouts = native_windows.PopoutApi()

    window = webview.create_window("hamdash", url, width=1440, height=900,
                                   min_size=(960, 640), js_api=popouts)
    native_windows.attach_main(window)
    window.events.closed += close_once
    webview.start()
    close_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
