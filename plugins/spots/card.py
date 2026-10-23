"""Who is on the air right now: POTA, SOTA, the cluster, and what contest.

Four services, each somebody's hobby server, so the politeness lives here
rather than in the page (the shape `psk_reporter` settled on):

* **One fetch per source per floor, whatever the page does.** Every tile and
  every keystroke is served from what was last fetched. A Refresh button
  cannot buy its way past the floor; it only means "do not wait for the next
  poll".
* **A failure waits its turn as surely as a success does.** Otherwise the
  page's own poll retries every few seconds into whatever just failed.
* **The cache is on disk.** In memory it lasts exactly as long as the
  process, and "no more than one request a minute" is not honoured by
  restarting the app.
* **Filtering happens in the browser**, over what was already fetched. A band
  or a search box must never be a reason to call anybody's server.

Nets come from NetLogger's list of nets in progress, at most once a
minute, and only while somebody has the Nets tab open.

Contests are fetched rarely on purpose: they are a year's calendar, they move
about as often, and the source's keyed API allows twenty calls a day while
its bulk export - the one its own downloads page links to - allows this.
"""
from __future__ import annotations

import json
import os
import threading
import time

from flask import request

from hamdash.sdk import Card
# Imports nothing, like bands, so a card process may use it directly.
from hamdash.services import dxcc

import sources
import telnet

card = Card(__file__)

# The telnet feeds, one per (kind, host, port, callsign), shared by every
# tile that wants the same thing - these hold a socket open on somebody
# else's server, so two tiles must not mean two logins.
_streams: dict = {}
_streams_lock = threading.Lock()
# Nothing tells a card that a tile was deleted, so a feed nobody has asked
# for is dropped - and this one is a login on a public network, not just a
# cache entry.
STREAM_IDLE = 90.0
_stream_wanted: dict = {}

# POTA's own page refreshes once a minute and the others are no busier, so
# this is the pace of a human watching, not of a program.
FLOOR = 60.0
CONTEST_FLOOR = 6 * 3600.0
# NetLogger's nets in progress: a net runs for an hour or so and changes
# when somebody starts or closes one, so once a minute is plenty - and it
# is the pace RagChew settled on against the same server.
NETS_FLOOR = 60.0
# How long a spot is worth showing at all. The services keep their own
# windows; this is the card refusing to draw an hour-old spot as news.
MAX_AGE = 3600.0


class Feed:
    """One source: its own floor, its own cache, its own last failure."""

    def __init__(self, name: str, fetch, floor: float) -> None:
        self.name = name
        self.fetch = fetch
        self.floor = floor
        self.rows: list = []
        self.at = 0.0               # when the rows were fetched
        self.wait = floor           # how long before trying again
        self.error = ""
        self._lock = threading.Lock()
        self._load()

    # -- disk ------------------------------------------------------------
    @property
    def path(self):
        return card.store / f"{self.name}.json"

    def _load(self) -> None:
        try:
            got = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:                               # noqa: BLE001
            return
        if isinstance(got, dict):
            self.rows = got.get("rows") or []
            self.at = float(got.get("at") or 0)
            self.wait = float(got.get("wait") or self.floor)
            self.error = str(got.get("error") or "")

    def _save(self) -> None:
        try:
            card.store.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"rows": self.rows, "at": self.at,
                                       "wait": self.wait,
                                       "error": self.error}),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception as exc:                        # noqa: BLE001
            print(f"{self.name}: could not write the cache ({exc})")

    # -- fetching ---------------------------------------------------------
    def get(self, force: bool = False) -> None:
        """Refresh if it is time. Never raises: a source that is down is a
        note beside the rows it last gave us, not an empty card."""
        now = time.time()
        with self._lock:
            due = not self.at or (now - self.at) >= max(self.floor, self.wait)
            if not (due or (force and (now - self.at) >= self.floor)):
                return
            try:
                self.rows = self.fetch()
                self.at, self.wait, self.error = now, self.floor, ""
            except Exception as exc:                    # noqa: BLE001
                # Back off, and keep serving what we had. The age is
                # reported, so stale is visible rather than pretended.
                retry = getattr(getattr(exc, "response", None), "headers", {})
                after = 0.0
                try:
                    after = float(retry.get("Retry-After") or 0)
                except (TypeError, ValueError):
                    after = 0.0
                self.at = self.at or now
                self.wait = max(self.floor, after, min(self.wait * 2, 900))
                self.error = str(exc)[:200]
            self._save()

    def status(self) -> dict:
        age = time.time() - self.at if self.at else 0
        return {"name": self.name, "age_s": round(age),
                "next_s": max(0, round(max(self.floor, self.wait) - age)),
                "error": self.error, "held": len(self.rows),
                # A source is only failing while it has nothing to show.
                "ok": bool(self.rows) or not self.error}


_feeds = {k: Feed(k, sources.FETCH[k], FLOOR) for k in sources.KINDS}
_contests = Feed("contests", sources.contests, CONTEST_FLOOR)
_nets = Feed("nets", sources.nets, NETS_FLOOR)


def _wanted(conf: dict) -> list[str]:
    """Which polled sources this tile is watching.

    Everything off used to mean everything on, which was a reasonable
    reading of an empty choice until there were live feeds to watch
    instead: a tile set to RBN alone got all three polled sources back as
    well. It only falls back when *nothing at all* is selected.
    """
    on = [k for k in sources.KINDS if conf.get(f"show_{k}", True)]
    if on or conf.get("show_rbn") or conf.get("show_cluster"):
        return on
    return list(sources.KINDS)


def _refresh(kinds, force=False) -> None:
    """Fetch what is due, each on its own thread.

    One slow service must not make the others late, and the request is
    waiting on all of them (invariant 4 - this is a host thread through the
    proxy), so they go at once rather than one after another.
    """
    threads = [threading.Thread(target=_feeds[k].get, args=(force,),
                                daemon=True) for k in kinds]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=sources.TIMEOUT + 2)


# ---------------------------------------------------------------- routes
def my_call() -> str:
    try:
        return str(card.host.station().get("callsign") or "").strip().upper()
    except Exception:                                   # noqa: BLE001
        return ""


def stream_for(conf: dict):
    """The telnet feeds this tile wants, started if they are not running."""
    want = []
    call = my_call()
    if conf.get("show_rbn"):
        port = (telnet.RBN_FT_PORT if str(conf.get("rbn_feed")) == "ft8"
                else telnet.RBN_CW_PORT)
        want.append(("rbn", telnet.RBN_HOST, port, call, ()))
    if conf.get("show_cluster"):
        # Every cluster has its own commands - VE7CC prints a page of them
        # on connect - so they are the operator's to give, one per line or
        # separated by semicolons, and they go to every node listed.
        raw = str(conf.get("cluster_commands") or "").replace(";", "\n")
        cmds = [c.strip() for c in raw.splitlines() if c.strip()]
        # One or several nodes, in the one box an existing tile already
        # has its single host in (`telnet.cluster_hosts`).
        for host, port in telnet.cluster_hosts(conf.get("cluster_host"),
                                               conf.get("cluster_port") or 23):
            want.append(("cluster", host, port, call, cmds))

    out = []
    now = time.time()
    with _streams_lock:
        for kind, host, port, who, cmds in want:
            key = (kind, host, port, who)
            _stream_wanted[key] = now
            st = _streams.get(key)
            if st is None:
                st = _streams[key] = telnet.SpotStream(
                    kind, host, port, who, cmds)
            out.append(st)
    return out


def _reap_streams() -> None:
    """Close a feed nobody has asked for. It is a login on a public
    network, so leaving it open because a tile was closed is rude as well
    as wasteful."""
    while True:
        time.sleep(15.0)
        now = time.time()
        with _streams_lock:
            for key in list(_streams):
                if now - _stream_wanted.get(key, 0) > STREAM_IDLE:
                    print(f"closing {key[0]} at {key[1]}:{key[2]}: "
                          f"no tile has asked for it")
                    _streams.pop(key).stop()
                    _stream_wanted.pop(key, None)


@card.get("/spots")
def spots():
    conf = card.settings()
    kinds = _wanted(conf)
    _refresh(kinds, force=request.args.get("force") in ("1", "true"))
    now = time.time()
    try:
        max_age = float(conf.get("max_age_min") or 30) * 60
    except (TypeError, ValueError):
        max_age = 1800.0
    max_age = min(max(max_age, 60.0), MAX_AGE)

    rows = []
    for kind in kinds:
        for r in _feeds[kind].rows:
            if now - float(r.get("at") or 0) <= max_age:
                rows.append(r)

    # The telnet feeds. `watch` is the headline: on RBN, the spots *of* one
    # callsign are every skimmer that can hear that station right now.
    live = []
    watch = str(conf.get("watch") or "").strip().upper()
    if watch == "ME":
        watch = my_call()
    for st in stream_for(conf):
        rows.extend(st.recent(max_age, watch))
        live.append(st.status_line())

    # `arrived` breaks ties between spots stamped to the same minute, which
    # on a live feed is most of them. It is a counter shared by every feed in
    # this process, so it orders them against each other as well.
    rows.sort(key=lambda r: (r.get("at") or 0, r.get("arrived") or 0),
              reverse=True)
    # Two cluster nodes deliver mostly the same spots - it is one network.
    rows = telnet.merge_cluster(rows)[:400]

    # The flag comes from the *callsign*, never from whatever a source calls
    # the country - they spell them differently and some send none at all.
    # Attached here rather than stored with the spot, the same as the log:
    # a row served from yesterday's disk cache gets today's prefix table.
    for r in rows:
        where = dxcc.for_call(r.get("call", ""))
        r["iso"] = where["iso"]
        r["country"] = where["country"]

    return {"spots": rows, "now": now,
            "sources": [_feeds[k].status() for k in sources.KINDS] + live,
            "watching": watch, "my_call": my_call(),
            "max_age_s": max_age}


@card.get("/contests")
def contests():
    _contests.get(force=request.args.get("force") in ("1", "true"))
    now = time.time()
    out = sources.live(_contests.rows, now)
    return {**out, "now": now, "source": _contests.status()}


@card.get("/nets")
def nets():
    """Nets in progress. Asked only while the Nets tab is on show; the
    floor holds whatever the page does."""
    _nets.get(force=request.args.get("force") in ("1", "true"))
    return {"nets": _nets.rows, "now": time.time(), "source": _nets.status()}


@card.post("/refresh")
def refresh():
    """What the Refresh button does. It cannot jump the floor."""
    conf = card.settings()
    _refresh(_wanted(conf), force=True)
    _contests.get(force=True)
    return {"ok": True,
            "sources": [_feeds[k].status() for k in sources.KINDS]}


if __name__ == "__main__":
    threading.Thread(target=_reap_streams, daemon=True,
                     name="spots-reaper").start()
    card.run()
