"""Supervisor for python cards.

Each python card runs as its own OS process on its own loopback port, so a
card that segfaults, hangs or leaks takes only itself down. The host
reverse-proxies /cards/<id>/* to that port and restarts the process (with
backoff) if it dies.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .registry import Card

START_TIMEOUT = 12.0
MAX_RESTARTS = 5
HEALTH_EVERY = 10.0        # seconds between /_health polls
HEALTH_FAILS = 3           # consecutive failures before a card is 'wedged'


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@dataclass
class Worker:
    card: Card
    host_url: str
    port: int = 0
    token: str = ""
    proc: subprocess.Popen | None = None
    restarts: int = 0
    log: list[str] = field(default_factory=list)
    status: str = "stopped"

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        self.port = free_port()
        self.token = secrets.token_urlsafe(24)
        env = dict(os.environ)
        env.update({
            "HAMDASH_CARD_ID": self.card.id,
            "HAMDASH_PORT": str(self.port),
            "HAMDASH_HOST": self.host_url,
            "HAMDASH_TOKEN": self.token,
            "PYTHONUNBUFFERED": "1",
            # let cards import the sdk without installing anything
            "PYTHONPATH": os.pathsep.join(
                [str(Path(__file__).resolve().parent.parent),
                 env.get("PYTHONPATH", "")]).strip(os.pathsep),
        })
        self.status = "starting"
        extra = {}
        if os.name == "nt":
            # Without this every card process flashes up its own console
            # window on Windows, which looks exactly like the app misbehaving.
            extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            [sys.executable, self.card.entry],
            cwd=str(self.card.path), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            # Windows defaults to cp1252 here, so one accented character in a
            # card's output would otherwise kill the log reader thread.
            encoding="utf-8", errors="replace",
            **extra,
        )
        threading.Thread(target=self._drain, daemon=True).start()
        threading.Thread(target=self._wait_ready, daemon=True).start()

    def _drain(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.rstrip()
            self.log.append(line)
            del self.log[:-300]
            print(f"[{self.card.id}] {line}")
        code = self.proc.wait()
        if self.status != "stopping":
            self.status = f"exited ({code})"
            self._maybe_restart()

    def _wait_ready(self) -> None:
        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                return
            try:
                with socket.create_connection(("127.0.0.1", self.port), 0.3):
                    self.status = "running"
                    threading.Thread(target=self._health_loop, daemon=True).start()
                    return
            except OSError:
                time.sleep(0.2)
        self.status = "no response"

    def _health_loop(self) -> None:
        """A card can bind its port and then wedge - blocked on a serial read,
        deadlocked, stuck in a retry loop. An open socket says nothing about
        that, so ask the card itself and report 'wedged' separately from
        'crashed'. A wedged card is never auto-restarted: restarting something
        that is hung but holding a device handle usually makes things worse."""
        import urllib.error
        import urllib.request

        fails = 0
        generation = self.proc
        while self.proc is generation and self.proc.poll() is None:
            time.sleep(HEALTH_EVERY)
            if self.status in ("stopping", "stopped"):
                return
            try:
                with urllib.request.urlopen(f"{self.base}/_health", timeout=3) as r:
                    json.loads(r.read() or b"{}")
                fails = 0
                if self.status == "wedged":
                    self.status = "running"
                    self.log.append("health check recovered")
            except Exception as exc:  # noqa: BLE001
                fails += 1
                if fails >= HEALTH_FAILS and self.status == "running":
                    self.status = "wedged"
                    self.log.append(f"no health response after {fails} tries ({exc})")

    @property
    def healthy(self) -> bool:
        return self.status == "running"

    def _maybe_restart(self) -> None:
        if self.restarts >= MAX_RESTARTS:
            self.status = "failed (restart limit)"
            self.log.append("restart limit reached; leaving stopped")
            return
        self.restarts += 1
        delay = min(2 ** self.restarts, 30)
        self.log.append(f"restarting in {delay}s (attempt {self.restarts})")
        time.sleep(delay)
        self.start()

    def stop(self) -> None:
        self.status = "stopping"
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.status = "stopped"

    def info(self) -> dict:
        return {"id": self.card.id, "status": self.status, "port": self.port,
                "restarts": self.restarts, "log": self.log[-25:]}


class Supervisor:
    def __init__(self, host_url: str) -> None:
        self.host_url = host_url
        self.workers: dict[str, Worker] = {}

    def ensure(self, card: Card) -> Worker:
        w = self.workers.get(card.id)
        if w is None:
            w = Worker(card=card, host_url=self.host_url)
            self.workers[card.id] = w
        if w.status in ("stopped", "") or (w.proc and w.proc.poll() is not None
                                          and not w.status.startswith("res")):
            w.start()
        return w

    def get(self, card_id: str) -> Worker | None:
        return self.workers.get(card_id)

    def restart(self, card_id: str) -> None:
        w = self.workers.get(card_id)
        if w:
            w.restarts = 0
            w.stop()
            w.start()

    def reap(self, active_card_ids) -> list[str]:
        """Stop cards that no longer have a tile. Without this, processes
        accumulate for the life of the session every time you remove a card."""
        gone = []
        for cid, w in list(self.workers.items()):
            if cid not in active_card_ids:
                w.stop()
                self.workers.pop(cid, None)
                gone.append(cid)
        return gone

    def card_for_token(self, token: str):
        """Which card is holding this token - the basis of permission checks."""
        if not token:
            return None
        return next((w.card for w in self.workers.values() if w.token == token), None)

    def stop_all(self) -> None:
        for w in list(self.workers.values()):
            w.stop()
