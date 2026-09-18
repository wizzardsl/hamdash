#!/usr/bin/env python3
"""Start the frozen app and prove it can run a card.

    pyinstaller --noconfirm --clean hamdash.spec
    python tools/smoke_frozen.py

Exists because the interesting failures only happen in a build. From source,
`sys.executable` is an interpreter and everything works; frozen, it is the
hamdash binary, so a card is started by re-execing the app itself
(`proc.card_command` -> `run.py --run-card`). A spec can build cleanly and
still produce an app that serves the board and cannot start a single card.

Checks, in order:
  1. the binary starts and answers /api/health
  2. the board shell and its CSS come out of the bundle
  3. the bundled cards are discovered from the bundle
  4. a python card actually starts, which is the re-exec path
  5. that card answers through the host's proxy
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "hamdash"
BINARY = DIST / ("hamdash.exe" if os.name == "nt" else "hamdash")

BOOT_TIMEOUT = 60.0     # a cold first launch on a CI runner is not fast
CARD_TIMEOUT = 60.0


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return 0, str(exc)


def wait_for(url: str, limit: float, want: str = "") -> str:
    """Poll until the URL answers 200, or give up and say what it last said."""
    deadline = time.time() + limit
    last = ""
    while time.time() < deadline:
        status, body = get(url)
        if status == 200 and (not want or want in body):
            return body
        last = f"{status} {body[:300]}"
        time.sleep(1.0)
    raise SystemExit(f"FAIL  {url} never came good.\n      last: {last}")


def main() -> int:
    if not BINARY.exists():
        raise SystemExit(
            f"FAIL  {BINARY} does not exist.\n"
            f"      Build it first: pyinstaller --noconfirm --clean hamdash.spec")

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    home = Path(tempfile.mkdtemp(prefix="hamdash-smoke-"))
    env = dict(os.environ, HAMDASH_HOME=str(home))

    print(f"starting {BINARY.name} on {port}, config in {home}")
    proc = subprocess.Popen(
        [str(BINARY), "--browser", "--port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")
    try:
        if proc.poll() is not None:
            raise SystemExit(f"FAIL  it exited immediately ({proc.returncode})\n"
                             f"{proc.stdout.read() if proc.stdout else ''}")

        wait_for(f"{base}/api/health", BOOT_TIMEOUT)
        print("ok    /api/health")

        for path, want in (("/", "<html"), ("/static/css/app.css", "")):
            status, body = get(f"{base}{path}")
            if status != 200 or (want and want not in body.lower()):
                raise SystemExit(f"FAIL  {path} -> {status}; the bundle is "
                                 f"missing hamdash/static")
        print("ok    board shell and css served from the bundle")

        status, body = get(f"{base}/api/cards")
        ids = [c["id"] for c in json.loads(body)] if status == 200 else []
        for want in ("hello_card", "logbook"):
            if want not in ids:
                raise SystemExit(f"FAIL  {want} not discovered; the bundle is "
                                 f"missing plugins/. Found: {ids}")
        print(f"ok    cards discovered from the bundle: {', '.join(sorted(ids))}")

        # The one that only fails in a build.
        body = wait_for(f"{base}/cards/hello_card/api/state?uid=smoke",
                        CARD_TIMEOUT, want="greeting")
        print(f"ok    python card started and answered: {body.strip()[:120]}")

        status, health = get(f"{base}/api/health")
        if '"running":0' in health.replace(" ", ""):
            raise SystemExit(f"FAIL  card answered but health says none "
                             f"running: {health}")
        print(f"ok    {health.strip()}")

        print("\nPASS  the frozen build runs cards")
        return 0
    finally:
        proc.terminate()
        try:
            out = proc.communicate(timeout=15)[0]
        except subprocess.TimeoutExpired:
            proc.kill()
            out = proc.communicate()[0]
        if out and os.environ.get("SMOKE_VERBOSE"):
            print("--- app output ---")
            print(out)
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
