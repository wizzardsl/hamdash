"""Space weather, for map overlays.

Only the aurora lives here. The greyline is pure solar geometry and is worked
out in the browser: asking a server where the sun is would be a network round
trip for something a few lines of trigonometry answer exactly.

The aurora comes from NOAA SWPC's OVATION model, which is public and needs no
account. It goes through the host rather than being fetched by the card for
three reasons:

  * NOAA publishes a new run about every five minutes, and every open tile
    polling them directly would be rude and pointless. One cache serves all.
  * the raw grid is 360x181 cells of mostly zero - about 1.5 MB - and sending
    that to a browser several times a minute is wasteful when the interesting
    part is a few thousand cells near the poles.
  * a card fetching a third-party URL itself depends on that server's CORS
    headers, which are not ours to rely on.
"""
from __future__ import annotations

import threading
import time

import requests

OVATION = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
TTL = 300.0             # NOAA runs the model about every five minutes
TIMEOUT = 25.0
USER_AGENT = "hamdash (+https://github.com/wizzardsl/hamdash)"


class Aurora:
    """OVATION aurora probability, cached and thinned."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._at = 0.0
        self._data: dict | None = None
        self._error = ""

    def get(self, threshold: int = 5, force: bool = False) -> dict:
        """Cells at or above `threshold` percent, as [lon, lat, percent].

        Thresholding is what makes this small: the aurora is confined to high
        latitudes, so almost every cell in the grid is zero and carries no
        information.
        """
        with self._lock:
            fresh = self._data and (time.time() - self._at) < TTL and not force
            if not fresh:
                self._refresh()
            if not self._data:
                return {"ok": False, "error": self._error or "no aurora data",
                        "cells": []}
            cells = [c for c in self._data["cells"] if c[2] >= threshold]
            return {"ok": True, "cells": cells, "threshold": threshold,
                    "observed": self._data["observed"],
                    "forecast": self._data["forecast"],
                    "age_s": round(time.time() - self._at),
                    "total_cells": len(self._data["cells"])}

    def _refresh(self) -> None:
        try:
            r = requests.get(OVATION, timeout=TIMEOUT,
                             headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            raw = r.json()
        except (requests.RequestException, ValueError) as exc:
            self._error = f"could not reach NOAA SWPC: {exc}"
            return
        coords = raw.get("coordinates") or []
        if not coords:
            self._error = "NOAA returned no aurora grid"
            return
        cells = []
        for row in coords:
            try:
                lon, lat, pct = float(row[0]), float(row[1]), int(row[2])
            except (TypeError, ValueError, IndexError):
                continue
            if pct <= 0:
                continue
            # NOAA gives longitude 0..360; the rest of the world uses -180..180.
            if lon > 180:
                lon -= 360
            cells.append([round(lon, 1), round(lat, 1), pct])
        self._data = {"cells": cells,
                      "observed": raw.get("Observation Time", ""),
                      "forecast": raw.get("Forecast Time", "")}
        self._at = time.time()
        self._error = ""
