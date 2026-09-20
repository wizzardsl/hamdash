"""PSK Reporter card.

Reception reports from pskreporter.info: who heard whom, where, on what, and
how well. The card process owns the fetching and the cache; the page asks the
card, never pskreporter directly.

**The cache is the whole design.** Philip Gladstone asks that data be retrieved
no more often than once every five minutes, and says plainly that he reserves
the right to rate limit or block anyone who loads his system. A map with
filters invites exactly the behaviour that would earn that: change the band,
change the mode, drag the time window, and each of those is a redraw. So:

* one upstream request per query at most every `MIN_INTERVAL` seconds, kept
  here rather than trusted to the page;
* filtering happens on what is already cached, so changing band, mode, sent
  or received, country or time costs nothing at all;
* the only thing that changes the upstream query is the callsign or grid being
  watched and how far back to look, and even that serves the cached answer
  until it is properly stale;
* a request that arrives while the cache is warm gets the cached answer with
  its age attached, so the page can say how old it is rather than pretending.

The query API cannot return the whole world - every query is anchored on a
callsign or, with `modify=grid`, a grid square. So "everything near me" means
a grid square, which is what `watch` defaults to.

Spec: pskreporter.info/pskdev.html
"""
from __future__ import annotations

import threading
import time
import xml.etree.ElementTree as ET

import requests

from hamdash.sdk import Card

card = Card(__file__)

QUERY = "https://retrieve.pskreporter.info/query"
# Their ask is "no more often than once every five minutes". This is the floor
# for one query; the page may poll as often as it likes and will be served
# from here.
MIN_INTERVAL = 300.0
# How long an answer is still worth showing while a refresh is due. Reports
# age out of usefulness quickly - this is propagation, not history.
STALE_AFTER = 1800.0
TIMEOUT = 45.0
MAX_ROWS = 2000

_v = "0.1.0"
USER_AGENT = f"hamdash-pskreporter/{_v} (+https://github.com/wizzardsl/hamdash)"


CACHE_FILE = "cache.json"


class RateLimited(RuntimeError):
    """They asked us to stop, and for how long."""

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class Fetcher:
    """One upstream query, cached, with the five minute floor enforced here.

    The cache is written to disk as well as held in memory. In memory alone
    it lasts exactly as long as the card process, so restarting hamdash - or
    the card being restarted under it - would send a fresh query every time.
    Restart three times inside five minutes and that is three queries, which
    is precisely what the rule asks you not to do; closing a window is not
    what anybody means by "no more than once every five minutes".
    """

    def __init__(self, store=None) -> None:
        self._lock = threading.Lock()
        self._by_key: dict[tuple, dict] = {}
        self._store = store
        self._load()

    # ---- across restarts -------------------------------------------------
    def _load(self) -> None:
        if not self._store:
            return
        saved = self._store.load_json(CACHE_FILE, None)
        if not isinstance(saved, list):
            return
        now = time.time()
        for row in saved:
            try:
                key = tuple(row["key"])
                entry = row["entry"]
                # A clock that has gone backwards would otherwise make an
                # entry look fresh for ever.
                keep = max(STALE_AFTER, entry.get("wait", 0))
                if not (0 <= now - entry["at"] < keep):
                    continue
                self._by_key[key] = entry
            except (KeyError, TypeError, ValueError):
                continue

    def _save(self) -> None:
        if not self._store:
            return
        self._store.save_json(CACHE_FILE, [
            {"key": list(k), "entry": v} for k, v in self._by_key.items()])

    def get(self, watch: str, as_grid: bool, hours: float, mode: str,
            contact: str = "", force: bool = False) -> dict:
        key = (watch.upper(), as_grid, round(hours, 2), mode.upper())
        now = time.time()
        with self._lock:
            got = self._by_key.get(key)
            age = now - got["at"] if got else None
            # Never sooner than MIN_INTERVAL, whatever anyone asks for. A
            # forced refresh from the page cannot buy its way past this; it
            # only skips the wait when the floor has already passed.
            if got and age < max(MIN_INTERVAL, got.get("wait", 0)):
                return self._answer(got, age, fresh=False)
            try:
                fresh = self._fetch(watch, as_grid, hours, mode, contact)
            except Exception as exc:                # noqa: BLE001 - reported
                # A failure waits its turn as surely as a success does.
                # Without this there is nothing holding the clock, so the
                # page's own poll retries every minute - and against a rate
                # limit, retrying is the thing that caused it. Remember the
                # attempt, and honour a Retry-After if one was given.
                wait = max(MIN_INTERVAL, getattr(exc, "retry_after", 0) or 0)
                if got:
                    # Keep showing what we have; say why it is not moving.
                    got["error"] = str(exc)
                    got["at"], got["wait"] = now, wait
                    self._save()
                    return self._answer(got, 0.0, fresh=False)
                self._by_key[key] = {"reports": [], "at": now, "wait": wait,
                                     "watching": watch.upper(), "hours": hours,
                                     "truncated": False, "error": str(exc),
                                     "failed": True}
                self._save()
                return {"ok": False, "error": str(exc), "reports": [],
                        "age_s": 0, "next_s": round(wait)}
            self._by_key[key] = fresh
            # One key per query, and there are only so many a person watches.
            if len(self._by_key) > 12:
                oldest = min(self._by_key, key=lambda k: self._by_key[k]["at"])
                self._by_key.pop(oldest, None)
            self._save()
            return self._answer(fresh, 0.0, fresh=True)

    @staticmethod
    def _answer(entry: dict, age: float, fresh: bool) -> dict:
        wait = max(MIN_INTERVAL, entry.get("wait", 0))
        return {"ok": not entry.get("failed"), "reports": entry["reports"],
                "age_s": round(age), "fresh": fresh,
                "next_s": max(0, round(wait - age)),
                "watching": entry["watching"], "hours": entry["hours"],
                "error": entry.get("error", ""),
                "truncated": entry.get("truncated", False)}

    def _fetch(self, watch: str, as_grid: bool, hours: float, mode: str,
               contact: str) -> dict:
        params = {
            "callsign": watch.upper(),
            # A negative number of seconds, and their limit is 24 hours.
            "flowStartSeconds": -int(min(hours, 24) * 3600),
            "rronly": 1,             # reception reports only, not the monitor list
            "noactive": 1,
            "rptlimit": MAX_ROWS,
        }
        if as_grid:
            params["modify"] = "grid"
        if mode:
            params["mode"] = mode.upper()
        if contact.strip():
            # Opt-in, empty by default. They ask for it so they can reach a
            # heavy user rather than simply blocking one.
            params["appcontact"] = contact.strip()
        r = requests.get(QUERY, params=params, timeout=TIMEOUT,
                         headers={"User-Agent": USER_AGENT,
                                  # They may come to require this; it is
                                  # cheaper for both ends either way.
                                  "Accept-Encoding": "gzip, deflate"})
        if r.status_code == 429:
            # Their rate limit, which is a request to stop rather than a
            # transient error to retry through.
            try:
                after = int(r.headers.get("Retry-After", "") or 0)
            except ValueError:
                after = 0
            raise RateLimited(
                "pskreporter is rate limiting this address"
                + (f" and asks for {after}s" if after else "")
                + ". Nothing is lost - it will try again by itself.",
                after or MIN_INTERVAL * 4)
        r.raise_for_status()
        text = r.text
        if "<receptionReports" not in text:
            raise RuntimeError("pskreporter did not return reception reports. "
                               "It usually means the query was refused; "
                               f"it said: {' '.join(text.split())[:200]}")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise RuntimeError(f"pskreporter sent XML that would not "
                               f"parse ({exc})") from exc
        rows = [_report(el) for el in root.iter("receptionReport")]
        rows = [r for r in rows if r]
        return {"reports": rows, "at": time.time(),
                "watching": watch.upper(), "hours": hours,
                "truncated": len(rows) >= MAX_ROWS, "error": ""}


def _report(el) -> dict | None:
    g = el.attrib
    try:
        hz = int(g.get("frequency") or 0)
        when = int(g.get("flowStartSeconds") or 0)
    except ValueError:
        return None
    if not g.get("senderCallsign") or not g.get("receiverCallsign"):
        return None
    return {
        "tx": g.get("senderCallsign", "").upper(),
        "rx": g.get("receiverCallsign", "").upper(),
        "tx_grid": g.get("senderLocator", ""),
        "rx_grid": g.get("receiverLocator", ""),
        "hz": hz,
        "mhz": round(hz / 1e6, 6),
        "band": _band(hz / 1e6),
        "mode": g.get("mode", ""),
        "snr": _int(g.get("sNR")),
        "at": when,
        # Which side of the report the watched station was on. The API says
        # so, which saves guessing from the callsign - and a grid query has no
        # callsign to guess from.
        "is_tx": g.get("isSender") == "1",
        "is_rx": g.get("isReceiver") == "1",
        "tx_dxcc": g.get("senderDXCC", ""),
        "rx_dxcc": g.get("receiverDXCC", ""),
        "tx_region": g.get("senderRegion", ""),
        "rx_region": g.get("receiverRegion", ""),
    }


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


BANDS = [(0.13, 0.14, "2200m"), (0.47, 0.48, "630m"), (1.8, 2.0, "160m"),
         (3.5, 4.0, "80m"), (5.25, 5.45, "60m"), (7.0, 7.3, "40m"),
         (10.1, 10.15, "30m"), (14.0, 14.35, "20m"), (18.06, 18.17, "17m"),
         (21.0, 21.45, "15m"), (24.89, 24.99, "12m"), (28.0, 29.7, "10m"),
         (50.0, 54.0, "6m"), (70.0, 71.0, "4m"), (144.0, 148.0, "2m"),
         (222.0, 225.0, "1.25m"), (420.0, 450.0, "70cm"),
         (902.0, 928.0, "33cm"), (1240.0, 1300.0, "23cm")]


def _band(mhz: float) -> str:
    return next((n for lo, hi, n in BANDS if lo <= mhz <= hi), "")


FETCHER = Fetcher(store=card)


@card.get("/reports")
def reports():
    """Everything cached for this query. The page filters what it gets.

    Filtering client side is not laziness: it is what keeps a person changing
    band and mode from turning into upstream requests.
    """
    from flask import request
    conf = card.settings(card.tile_uid())
    watch = (request.args.get("watch")
             or conf.get("watch")
             or "").strip()
    if not watch:
        station = {}
        try:
            station = card.host.station()
        except Exception:
            pass
        watch = (station.get("grid") or "")[:4]
    if not watch:
        return {"ok": False, "reports": [],
                "error": "Nothing to watch yet. Set your grid in Settings, or "
                         "type a callsign above."}
    # The page says which question it is asking; falling back to the shape of
    # the text only when it does not, which is what a card run on its own or
    # an older page will do.
    asked = (request.args.get("as") or "").strip().lower()
    as_grid = (asked == "grid") if asked in ("grid", "call")         else _looks_like_grid(watch)
    try:
        hours = float(request.args.get("hours") or conf.get("hours") or 1)
    except ValueError:
        hours = 1.0
    return FETCHER.get(watch, as_grid, max(0.05, min(hours, 24)),
                       (conf.get("mode_filter") or "").strip(),
                       (conf.get("appcontact") or "").strip(),
                       force=request.args.get("force") == "1")


def _looks_like_grid(s: str) -> bool:
    """A grid square, not a callsign. Four or six characters, no digits first.

    Getting this wrong just asks the wrong question, so it is deliberately
    strict: anything that is not clearly a locator is treated as a callsign.
    """
    s = s.strip().upper()
    if len(s) not in (4, 6):
        return False
    return (s[0].isalpha() and s[1].isalpha()
            and s[2].isdigit() and s[3].isdigit()
            and (len(s) == 4 or (s[4].isalpha() and s[5].isalpha())))


@card.get("/station")
def station():
    try:
        return card.host.station()
    except Exception as exc:                        # noqa: BLE001 - reported
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    card.run()
