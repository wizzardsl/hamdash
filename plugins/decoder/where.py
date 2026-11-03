"""Where a decoded station is: country, state or province, and how far.

Each FT8/FT4 row gets a flag, a short place and a distance from the
operator's grid. The pieces, cheapest first:

* **Country** from the callsign prefix (`services/dxcc.py`), instant.
* **Canadian province** from the prefix too (`dxcc.province_for`), instant.
* **US state** cannot be read off a call - a W7 may live anywhere - so it
  comes from the host's callsign lookup. That is the free callook/hamdb by
  default, cached by the host for a month, and asked **slowly**: one call
  every `LOOKUP_GAP` seconds, US calls only, each at most once per
  process. A busy band is forty stations a slot; asking for all of them at
  once is how a free service's rate limit gets hit, the lesson the
  logbook's lookup gating learned (CLAUDE.md, "Callsign lookup").
* **Grid** for the distance: the one the station sent in this message,
  else the last one heard from that call (most stations send it in their
  CQ and first reply, so it is usually known by the time they send a
  report), else the lookup's, else the country's middle - which is marked
  approximate and drawn with a "~", because the middle of a country is not
  where anybody lives.

Nothing here touches the network itself; the lookup goes through the host,
which owns the provider, the credentials and the cache.
"""
from __future__ import annotations

import queue
import threading
import time

from hamdash.services import dxcc
from hamdash.services import grid as gridlib

LOOKUP_GAP = 2.0          # seconds between lookups
STATION_TTL = 60.0        # how long the operator's own grid is kept
MAX_PENDING = 200         # a band that busy can wait for its states
US = "us"


class Places:
    """Answers "where is this call" for the decoder's rows.

    `lookup(call)` and `station()` are the host's; tests pass their own.
    `start=False` keeps the lookup thread out of a test that does not want
    one, which then drives `_lookup_one` itself.
    """

    def __init__(self, lookup=None, station=None, clock=time.time,
                 start: bool = True) -> None:
        self._lookup = lookup
        self._station = station
        self._clock = clock
        self._lock = threading.Lock()
        self.heard: dict[str, str] = {}         # call -> last grid sent
        self.looked: dict[str, dict] = {}       # call -> lookup answer
        self._asked: set[str] = set()
        self._queue: queue.Queue = queue.Queue(maxsize=MAX_PENDING)
        self._mine = ("", 0.0)
        if start and lookup is not None:
            threading.Thread(target=self._run, daemon=True,
                             name="decoder-lookups").start()

    # -- what the rows say -----------------------------------------------------
    def heard_grid(self, call: str, grid: str) -> None:
        """A station sent its grid on the air: the best answer there is."""
        call = (call or "").upper()
        if _usable(call) and grid and gridlib.valid(grid):
            with self._lock:
                self.heard[call] = grid.upper()

    def place(self, call: str, sent_grid: str = "",
              may_look_up: bool = True) -> dict | None:
        """Everything the row shows about where `call` is, or None for a
        call that is not one (a hash "<...>", a CQ with no caller).

        `may_look_up=False` for the mock's stations: they are invented
        (K1ABC and W9XYZ are the textbook calls), and asking a real
        lookup service about them on every start is a waste of somebody
        else's server."""
        call = (call or "").upper()
        if not _usable(call):
            return None
        if sent_grid:
            self.heard_grid(call, sent_grid)
        country = dxcc.for_call(call)
        code, name = dxcc.province_for(call)
        with self._lock:
            looked = self.looked.get(call)
            heard = self.heard.get(call, "")
        pending = False
        if country["iso"] == US and looked is None and may_look_up:
            pending = self._ask(call)
        if looked and not code:
            code = name = (looked.get("state") or "").upper()
        loc, approx = "", False
        if heard:
            loc = heard
        elif looked and looked.get("locator") and not looked.get("approximate"):
            loc = looked["locator"]
        elif dxcc.province_grid(code):
            loc, approx = dxcc.province_grid(code), True
        elif country["locator"]:
            loc, approx = country["locator"], True
        out = {"call": call, "country": country["country"],
               "iso": country["iso"], "state": code, "state_name": name,
               "grid": loc, "approx": approx, "pending": pending,
               "dist_km": None, "bearing": None}
        mine = self.station_grid()
        if loc and mine:
            try:
                d = gridlib.distance_bearing(mine, loc)
                out["dist_km"] = round(d["km"], 1)
                out["bearing"] = round(d["bearing"])
            except (ValueError, KeyError, TypeError):
                pass
        return out

    # -- the operator's own grid -------------------------------------------------
    def station_grid(self) -> str:
        grid, at = self._mine
        if self._station is None or self._clock() - at < STATION_TTL:
            return grid
        try:
            got = str((self._station() or {}).get("grid") or "").strip()
        except Exception:                               # noqa: BLE001
            got = grid                                  # keep what we had
        grid = got if gridlib.valid(got) else ""
        self._mine = (grid, self._clock())
        return grid

    # -- the slow lookups -----------------------------------------------------------
    def _ask(self, call: str) -> bool:
        with self._lock:
            if call in self._asked:
                return call not in self.looked
            self._asked.add(call)
        try:
            self._queue.put_nowait(call)
        except queue.Full:
            with self._lock:
                self._asked.discard(call)               # ask again later
        return True

    def _run(self) -> None:
        while True:
            call = self._queue.get()
            self._lookup_one(call)
            time.sleep(LOOKUP_GAP)

    def _lookup_one(self, call: str) -> None:
        try:
            got = self._lookup(call) or {}
        except Exception as exc:                        # noqa: BLE001
            # A refused or failed lookup is a fact about the provider, not
            # about the station: remembered as "no state", so the row stops
            # saying it is waiting, and not asked again this run.
            got = {"error": str(exc)}
        with self._lock:
            self.looked[call] = {
                "state": str(got.get("state") or "").strip(),
                "locator": str(got.get("locator") or "").strip(),
                "approximate": bool(got.get("approximate"))}


def _usable(call: str) -> bool:
    return bool(call) and not call.startswith("<") and call != "CQ" \
        and any(ch.isdigit() for ch in call)
