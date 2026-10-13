"""Amateur satellites: the orbital elements, fetched once and kept.

The card does nothing clever with them - the arithmetic is SGP4 and it
happens in the browser, where the clock ticks. What lives here is the
part that must not be done per tile or per minute: talking to Celestrak.

Two-line elements are published a few times a day and go stale slowly -
a set a day old is still good to a few kilometres. So this fetches at
most every `FLOOR` seconds however often it is asked, keeps the answer
on disk so a restart is not a fresh request, and serves a stale set
through an outage rather than nothing. That is the `psk_reporter`
pattern and it is here for the same reason: it is somebody else's server
and a tile on a dashboard is a thing that reloads all day.
"""
from __future__ import annotations

import json
import re
import threading
import time

import requests
from flask import request

from hamdash.sdk import Card

card = Card(__file__)

# Celestrak's own groupings. 'amateur' is 95-odd ham satellites in 16 kB,
# which is the right question to ask: the full catalogue is 10,000 objects
# and nobody is working any of the others.
URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"
GROUPS = ("amateur", "cubesat", "weather", "stations")
FLOOR = 6 * 3600.0          # elements change a few times a day, not hourly
RETRY = 15 * 60.0           # after a failure, before trying again
TIMEOUT = 25.0
UA = "hamdash (+https://github.com/wizzardsl/hamdash)"

# A TLE line 1/2 is fixed-width and starts with the line number and a space.
LINE1 = re.compile(r"^1 \d{5}")
LINE2 = re.compile(r"^2 \d{5}")

_lock = threading.Lock()
_sets: dict[str, dict] = {}


def _parse(text: str) -> list[dict]:
    """Name, line 1, line 2 - in threes, and skipping anything that is not.

    Celestrak occasionally serves an error page with a 200, so a run of
    text that does not look like elements has to come back as no
    satellites rather than as 95 broken ones.
    """
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    out = []
    i = 0
    while i + 2 < len(lines):
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if LINE1.match(l1) and LINE2.match(l2):
            out.append({"name": name.strip(), "tle1": l1, "tle2": l2})
            i += 3
        else:
            i += 1          # out of step, or not elements at all
    return out


def _store_path(group: str):
    return card.store / f"tle-{group}.json"


def _load(group: str) -> dict | None:
    """What we had last time, so a restart is not a fresh request."""
    try:
        return json.loads(_store_path(group).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(group: str, data: dict) -> None:
    try:
        _store_path(group).write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass            # a read-only home is not worth failing a request on


def elements(group: str) -> dict:
    """The elements for a group, fetching only if the floor has passed."""
    group = group if group in GROUPS else "amateur"
    now = time.time()
    with _lock:
        have = _sets.get(group) or _load(group)
        if have:
            _sets[group] = have
            age = now - have.get("at", 0)
            waiting = have.get("error") and age < RETRY
            if age < FLOOR or waiting:
                return dict(have, age_s=round(age),
                            wait_s=round(max(0.0, FLOOR - age)))
        try:
            r = requests.get(URL.format(group=group), timeout=TIMEOUT,
                             headers={"User-Agent": UA})
            r.raise_for_status()
            sats = _parse(r.text)
            if not sats:
                raise ValueError("no elements in the reply")
        except (requests.RequestException, ValueError) as exc:
            # A failure is kept like an answer, with its own clock. Without
            # that, a page polling every minute retries into whatever just
            # refused it.
            if have and have.get("satellites"):
                have = dict(have, error=f"could not refresh: {exc}", at=now)
                _sets[group] = have
                _save(group, have)
                return dict(have, age_s=0, stale=True)
            out = {"satellites": [], "at": now, "error": str(exc)}
            _sets[group] = out
            _save(group, out)
            return dict(out, age_s=0)
        out = {"satellites": sats, "at": now, "error": "",
               "source": "Celestrak"}
        _sets[group] = out
        _save(group, out)
        return dict(out, age_s=0, wait_s=round(FLOOR))


@card.get("/tles")
def tles():
    conf = card.settings()
    group = str(request.args.get("group") or conf.get("group") or "amateur")
    d = elements(group)
    return {"ok": bool(d.get("satellites")), "group": group,
            "satellites": d.get("satellites", []),
            "error": d.get("error", ""), "stale": bool(d.get("stale")),
            "age_s": d.get("age_s", 0), "source": "Celestrak",
            "count": len(d.get("satellites", []))}


card.run()
