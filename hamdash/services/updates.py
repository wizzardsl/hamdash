"""Is there a newer hamdash?

Asks GitHub's public "latest release" record once a day and says whether
it is newer than the one running. That is the whole of it: nothing is
downloaded, nothing is installed, and the answer is a line in
Settings -> Help with a link. Someone who installed hamdash from a zip
otherwise has no way to know an update exists short of going to look.

What it sends: one HTTPS GET to api.github.com, with hamdash's
User-Agent. No callsign, no grid, nothing from the config. `updates.check`
turns it off, and off means no request at all.

It runs on its own thread, never a request thread (invariant 4): the
endpoint reports the last answer and never waits on GitHub. A failure is
kept as the answer's `error` and tried again an hour later rather than a
day, since the usual cause is the network being down at startup.
"""
from __future__ import annotations

import re
import threading
import time

from .. import __version__

LATEST = "https://api.github.com/repos/wizzardsl/hamdash/releases/latest"
RELEASES = "https://github.com/wizzardsl/hamdash/releases/latest"
USER_AGENT = "hamdash (+https://github.com/wizzardsl/hamdash)"
EVERY = 24 * 3600          # a release a week at most; a day is plenty
RETRY = 3600               # after a failure
FIRST = 30                 # after startup, so the board paints first
TIMEOUT = 10


def version_tuple(text: str) -> tuple[int, ...] | None:
    """'v1.3.1' or '1.3.1' -> (1, 3, 1); anything else -> None.

    A tag that is not plain numbers (a pre-release, a typo) is not
    compared at all, rather than compared wrongly: GitHub's "latest"
    excludes pre-releases already, so this only matters for a mistake."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", (text or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def newer(latest: str, current: str = __version__) -> bool:
    a, b = version_tuple(latest), version_tuple(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


class Updates:
    """`enabled()` is read each round, so turning it off in Settings stops
    the next request without a restart. `fetch` is the HTTP call, replaced
    in tests so nothing here touches the network."""

    def __init__(self, enabled, fetch=None, clock=time.time) -> None:
        self._enabled = enabled
        self._fetch = fetch or _fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._state = {"current": __version__, "latest": "", "newer": False,
                       "url": RELEASES, "checked": 0.0, "error": ""}

    def status(self) -> dict:
        with self._lock:
            out = dict(self._state)
        out["enabled"] = bool(self._enabled())
        return out

    def check(self) -> dict:
        """One round, now. Returns the new state; never raises."""
        if not self._enabled():
            return self.status()
        try:
            tag, url = self._fetch()
            update = {"latest": tag.lstrip("v"), "newer": newer(tag),
                      "url": url or RELEASES, "error": ""}
        except Exception as exc:                        # noqa: BLE001
            update = {"error": str(exc) or type(exc).__name__}
        with self._lock:
            self._state.update(update, checked=self._clock())
        return self.status()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True,
                         name="update-check").start()

    def again(self) -> None:
        """Turned back on in Settings: look now rather than tomorrow."""
        self._wake.set()

    def _run(self) -> None:
        self._wake.wait(FIRST)
        while True:
            self._wake.clear()
            try:
                state = self.check()
                pause = RETRY if state.get("error") else EVERY
            except Exception:                           # noqa: BLE001
                pause = RETRY                           # nothing ends this thread
            self._wake.wait(pause)


def _fetch() -> tuple[str, str]:
    import requests
    r = requests.get(LATEST, timeout=TIMEOUT, headers={
        "User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise RuntimeError("GitHub rate limit; will try again later")
    r.raise_for_status()
    body = r.json()
    tag = str(body.get("tag_name") or "")
    if not version_tuple(tag):
        raise RuntimeError(f"unexpected release tag {tag!r}")
    return tag, str(body.get("html_url") or RELEASES)
