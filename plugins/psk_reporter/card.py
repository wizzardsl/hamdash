"""PSK Reporter card.

Reception reports from pskreporter.info: who heard whom, where, on what, and
how well. The card process owns the fetching and the store; the page asks the
card, never pskreporter directly.

**The store is the whole design.** Philip Gladstone asks that data be retrieved
no more often than once every five minutes, says plainly that he reserves the
right to rate limit or block anyone who loads his system, and asks for "an
adaptive use of the retrieval mechanism". A map with filters invites exactly
the opposite: change the band, change the mode, drag the time window, and each
of those wants to be a request.

So:

* **one upstream request per watched target**, at most every `MIN_INTERVAL`
  seconds, enforced here rather than trusted to the page;
* **`lastseqno`** on every pull after the first, so each one carries only what
  has arrived since - their own mechanism for exactly this, and the reason the
  polling costs so little;
* reports **accumulate** in `store.py` rather than being re-fetched, so the
  window grows past what one reply can hold and a restart costs nothing;
* **every filter runs against the store or in the page** - band, mode, signal,
  country, direction and the time window - so none of them is a request.

Correctness does not depend on `lastseqno`: rows are keyed on what makes a
report unique, so an overlapping pull updates rather than duplicates, and a
`lastseqno` that is ignored costs bandwidth and nothing else.

The query API cannot return the whole world - every query is anchored on a
callsign or, with `modify=grid`, a grid square. So "everything near me" means
a grid square.

Spec: pskreporter.info/pskdev.html
"""
from __future__ import annotations

import threading
import time
import xml.etree.ElementTree as ET

import requests

from hamdash.sdk import Card
from store import RETAIN, Store, band_for

card = Card(__file__)

QUERY = "https://retrieve.pskreporter.info/query"
# Their ask is "no more often than once every five minutes". This is the floor
# for one target; the page may poll as often as it likes and is served from
# the store.
MIN_INTERVAL = 300.0
TIMEOUT = 45.0
# What one reply may carry. The store is how the window gets deeper than this.
MAX_ROWS = 2000
# How far back the first pull for a target reaches. Their limit is 24 hours;
# asking for all of it on a busy square would just hit `rptlimit` and throw
# most of it away, and every pull after this one is incremental anyway.
FIRST_PULL = 3600 * 6

_v = "0.2.0"
USER_AGENT = f"hamdash-pskreporter/{_v} (+https://github.com/wizzardsl/hamdash)"


class RateLimited(RuntimeError):
    """They asked us to stop, and for how long."""

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class Fetcher:
    """One target, polled incrementally, with the five minute floor here."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self._lock = threading.Lock()

    def get(self, watch: str, as_grid: bool, window: float,
            contact: str = "", force: bool = False) -> dict:
        target = watch.upper()
        now = time.time()
        with self._lock:
            st = self.store.state(target)
            last = float(st.get("at") or 0)
            wait = max(MIN_INTERVAL, float(st.get("wait") or 0))
            # `force` is the Refresh button. It cannot buy its way past the
            # floor - it only means "do not wait for the poll".
            if not last or (now - last) >= wait:
                try:
                    self._pull(target, as_grid, st, contact)
                    self.store.mark(target, at=now, wait=0, error="", failed=0,
                                    as_grid=1 if as_grid else 0)
                    self.store.prune()
                except Exception as exc:            # noqa: BLE001 - reported
                    # A failure waits its turn as surely as a success does.
                    # Without this the page's own poll retries every minute,
                    # and against a rate limit retrying is what caused it.
                    back = max(MIN_INTERVAL,
                               getattr(exc, "retry_after", 0) or 0)
                    self.store.mark(target, at=now, wait=back, error=str(exc),
                                    failed=1, as_grid=1 if as_grid else 0)
                st = self.store.state(target)
            return self._answer(target, window, st)

    def _answer(self, target: str, window: float, st: dict) -> dict:
        age = time.time() - float(st.get("at") or 0)
        wait = max(MIN_INTERVAL, float(st.get("wait") or 0))
        rows = self.store.since(target, window)
        oldest = self.store.oldest(target)
        return {
            # A failure is only a failure while there is nothing to show.
            # Once the store holds reports, an outage is a note beside them.
            "ok": bool(rows) or not st.get("failed"),
            "reports": rows,
            "watching": target,
            "age_s": round(age) if st.get("at") else 0,
            "next_s": max(0, round(wait - age)) if st.get("at") else 0,
            "error": st.get("error") or "",
            "held": self.store.count(target),
            # How far back the store actually reaches, so the page can say
            # what a window is worth rather than implying one it cannot fill.
            "covers_s": round(time.time() - oldest) if oldest else 0,
            "retain_s": RETAIN,
        }

    def _pull(self, target: str, as_grid: bool, st: dict, contact: str) -> None:
        seq = (st.get("last_seq") or "").strip()
        params = {
            "callsign": target,
            "rronly": 1,             # reception reports only, not the monitors
            "noactive": 1,
            "rptlimit": MAX_ROWS,
        }
        if as_grid:
            params["modify"] = "grid"
        if seq:
            # Only what has arrived since we last looked. This is the whole
            # point: after the first pull these replies are small.
            params["lastseqno"] = seq
        else:
            params["flowStartSeconds"] = -int(FIRST_PULL)
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

        rows = [x for x in (_report(el) for el in root.iter("receptionReport"))
                if x]
        self.store.add(target, rows)
        # Their reply carries the high-water mark, and keeping it is what
        # makes the next pull incremental. A reply without one leaves the
        # marker alone rather than resetting us to fetching everything again.
        found = root.find("lastSequenceNumber")
        got = found.get("value") if found is not None else ""
        if got:
            self.store.mark(target, last_seq=got)


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
        "band": band_for(hz / 1e6),
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


STORE = Store(card.store / "reports.sqlite3")
FETCHER = Fetcher(STORE)


@card.get("/reports")
def reports():
    """One window of the store, refreshing it if it is due.

    The window is a query against the local file, so the page may ask for any
    of them as often as it likes.
    """
    from flask import request
    conf = card.settings(card.tile_uid())
    watch = (request.args.get("watch") or conf.get("watch") or "").strip()
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
    as_grid = (asked == "grid") if asked in ("grid", "call") \
        else _looks_like_grid(watch)
    try:
        window = float(request.args.get("window") or RETAIN)
    except ValueError:
        window = float(RETAIN)
    return FETCHER.get(watch, as_grid, max(60.0, min(window, RETAIN)),
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
