"""PSK Reporter card.

Reception reports from pskreporter.info: who heard whom, where, on what,
and how well. The card process owns the feed and the store; the page asks
the card, never pskreporter directly.

**It listens rather than asks.** It used to poll their query API on a five
minute floor with `lastseqno` to keep each pull small, and that was the
best available at the time and still ran into the ceiling: `rptlimit`
truncates a busy square at 2000 rows, which covered barely ten minutes,
and the request itself could time out. PSK Reporter publish a live MQTT
feed, so there is now nothing to poll, no retrieval limit to hit and
nothing to time out. `mqtt.py` is the subscriber.

The topic tree is what makes it fit:

    pskr/filter/v2/{band}/{mode}/{tx}/{rx}/{tx_grid}/{rx_grid}/{tx_dxcc}/{rx_dxcc}

so "who is hearing me" and "what am I hearing" are two subscriptions, not
a query to be filtered afterwards.

**The store is still the whole design**, for a different reason than
before. Reports accumulate in `store.py` as they arrive, so the window is
as deep as the card has been running plus whatever earlier runs left -
and a restart does not begin at nothing. Every filter still runs against
the store or in the page - band, mode, signal, country, direction, the
time window - so none of them reaches the network, which now means none
of them is anything at all.

One feed per watched *target*, not per tile, and one nobody has asked
about is closed: it is a connection to somebody else's broker.

Spec: pskreporter.info/pskdev.html, and the MQTT feed described there.
"""
from __future__ import annotations

import threading
import time

# Beside card.py, like store.py: this card's own modules.
import mqtt

from hamdash.sdk import Card
from store import RETAIN, Store, band_for

card = Card(__file__)


class Live:
    """One MQTT feed per watched target, and the rows they produce.

    A feed is a TCP connection to somebody else's broker, so there is one
    per *target* rather than one per tile, and one nobody has asked about
    for `IDLE` is closed. "Nothing tells a card that a tile was deleted"
    applies to a socket on a public server at least as much as to a sound
    card.

    Spots are buffered and written in batches. At the rate a busy band
    delivers them - 56 in four seconds, measured - a transaction per spot
    is a write per spot, and SQLite is not the place to find that out.
    """

    IDLE = 180.0            # close a feed no tile has asked about
    FLUSH_ROWS = 64
    FLUSH_SECONDS = 3.0

    def __init__(self, store: Store) -> None:
        self.store = store
        self._lock = threading.Lock()
        self._feeds: dict = {}
        self._wanted: dict = {}
        self._buf: dict = {}
        self._flushed = time.time()
        threading.Thread(target=self._reap, daemon=True,
                         name="psk-reap").start()

    # -- feeds --------------------------------------------------------
    def ensure(self, target: str, as_grid: bool):
        """The feed for a target, started if it is not running."""
        key = target.upper()
        with self._lock:
            self._wanted[key] = time.time()
            feed = self._feeds.get(key)
            if feed is not None and feed.alive():
                return feed
            topics = (mqtt.topics_for(grid=key) if as_grid
                      else mqtt.topics_for(call=key))
            if not topics:
                return None
            feed = mqtt.Feed(topics, lambda spot, t=key: self._spot(t, spot))
            self._feeds[key] = feed
        feed.start()
        return feed

    def status(self, target: str) -> dict:
        feed = self._feeds.get(target.upper())
        return feed.info() if feed else {"status": "idle", "error": "",
                                         "spots": 0, "topics": []}

    # -- rows ---------------------------------------------------------
    def _spot(self, target: str, spot: dict) -> None:
        row = as_row(target, spot)
        if row is None:
            return
        with self._lock:
            self._buf.setdefault(target, []).append(row)
            n = sum(len(v) for v in self._buf.values())
            due = (n >= self.FLUSH_ROWS
                   or time.time() - self._flushed > self.FLUSH_SECONDS)
        if due:
            self.flush()

    def flush(self) -> int:
        with self._lock:
            buf, self._buf = self._buf, {}
            self._flushed = time.time()
        wrote = 0
        for target, rows in buf.items():
            if rows:
                try:
                    wrote += self.store.add(target, rows)
                except Exception:               # noqa: BLE001
                    pass        # a failed write must not end the feed
        return wrote

    def _reap(self) -> None:
        while True:
            time.sleep(10.0)
            self.flush()
            now = time.time()
            with self._lock:
                stale = [k for k, when in self._wanted.items()
                         if now - when > self.IDLE]
                for key in stale:
                    feed = self._feeds.pop(key, None)
                    self._wanted.pop(key, None)
                    if feed is not None:
                        print(f"closing feed for {key}: no tile has asked "
                              f"in {self.IDLE:.0f}s")
                        feed.stop()
            try:
                self.store.prune(RETAIN)
            except Exception:                   # noqa: BLE001
                pass


def as_row(target: str, spot: dict):
    """One MQTT spot as a store row.

    The live feed and the old query answer the same question in different
    words: `sc`/`rc` for the two callsigns, `sl`/`rl` for their squares,
    `f` in hertz, `rp` for the signal report. The time is `t_tx` - when it
    was *transmitted* - because that is what the query's
    `flowStartSeconds` was, and two sources disagreeing about when a spot
    happened would show up as duplicate rows an hour apart.
    """
    tx = str(spot.get("sc") or "").upper()
    rx = str(spot.get("rc") or "").upper()
    if not tx or not rx:
        return None
    try:
        hz = int(spot.get("f") or 0)
    except (TypeError, ValueError):
        return None
    when = _int(spot.get("t_tx")) or _int(spot.get("t")) or 0
    if not hz or not when:
        return None
    up = target.upper()
    return {
        "tx": tx, "rx": rx,
        "tx_grid": str(spot.get("sl") or ""),
        "rx_grid": str(spot.get("rl") or ""),
        "hz": hz,
        "mhz": round(hz / 1e6, 6),
        "band": band_for(hz / 1e6),
        "mode": str(spot.get("md") or ""),
        "snr": _int(spot.get("rp")),
        "at": when,
        # Which side the watched station was on. The feed does not say, so
        # it is read off the callsigns - and for a grid watch neither
        # matches, which is correct: a square is not a station.
        "is_tx": tx == up,
        "is_rx": rx == up,
        "tx_dxcc": str(spot.get("sa") or ""),
        "rx_dxcc": str(spot.get("ra") or ""),
        "tx_region": "",
        "rx_region": "",
    }


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


STORE = Store(card.store / "reports.sqlite3")
LIVE = Live(STORE)


@card.get("/reports")
def reports():
    """What is in the store for this target, and how the feed is doing.

    Live, so there is nothing to refresh and no interval to respect: the
    page may ask as often as it likes and is answered from the local
    file. What it gets is what has arrived since the feed started, plus
    whatever earlier runs left behind - the store is kept on purpose, so
    a restart does not begin at nothing.
    """
    from flask import request
    conf = card.settings(card.tile_uid())
    watch = (request.args.get("watch") or conf.get("watch") or "").strip()
    if not watch:
        station = {}
        try:
            station = card.host.station()
        except Exception:                       # noqa: BLE001
            pass
        watch = (station.get("grid") or "")[:4]
    if not watch:
        return {"ok": False, "reports": [],
                "error": "Nothing to watch yet. Set your grid in Settings, or "
                         "type a callsign above."}
    asked = (request.args.get("as") or "").strip().lower()
    as_grid = (asked == "grid") if asked in ("grid", "call") \
        else _looks_like_grid(watch)
    try:
        window = float(request.args.get("window") or RETAIN)
    except ValueError:
        window = float(RETAIN)
    window = max(60.0, min(window, RETAIN))

    LIVE.ensure(watch, as_grid)
    LIVE.flush()                # so a spot that just arrived is in the answer
    rows = STORE.since(watch, window)
    feed = LIVE.status(watch)
    return {
        "ok": True, "reports": rows, "watch": watch, "as_grid": as_grid,
        "count": len(rows), "held": STORE.count(watch),
        "oldest": STORE.oldest(watch), "window": window,
        "live": feed, "source": "pskreporter.info live feed (MQTT)",
        "error": feed.get("error", ""),
    }

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
