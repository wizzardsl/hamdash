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
DRAP = "https://services.swpc.noaa.gov/text/drap_global_frequencies.txt"
# prop.kc2g.com publishes the ionosonde measurements behind its MUF map. The
# smooth field on their site is their interpolation of these points; we plot
# the measurements themselves rather than pretending to reproduce it.
IONOSONDES = "https://prop.kc2g.com/api/stations.json"
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


class Drap:
    """NOAA's D-region absorption, as the highest frequency it affects.

    Published as a text table, not JSON: a header, then one row per latitude
    from 89 down to -89, each holding 90 longitudes from -178 in steps of 4.
    The value is the highest frequency in MHz absorbed at that point, so zero
    means an undisturbed ionosphere - which is most of the time, and the layer
    being empty is the good news rather than a failure.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._at = 0.0
        self._data: dict | None = None
        self._error = ""

    def get(self, threshold: float = 1.0, force: bool = False) -> dict:
        with self._lock:
            if not (self._data and time.time() - self._at < TTL and not force):
                self._refresh()
            if not self._data:
                return {"ok": False, "error": self._error or "no D-RAP data",
                        "cells": []}
            cells = [c for c in self._data["cells"] if c[2] >= threshold]
            return {"ok": True, "cells": cells, "threshold": threshold,
                    "valid_at": self._data["valid_at"],
                    "quiet": not cells,
                    "age_s": round(time.time() - self._at)}

    def _refresh(self) -> None:
        try:
            r = requests.get(DRAP, timeout=TIMEOUT,
                             headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            text = r.text
        except requests.RequestException as exc:
            self._error = f"could not reach NOAA SWPC: {exc}"
            return
        valid_at = ""
        lons: list[float] = []
        cells = []
        for line in text.splitlines():
            if line.startswith("#"):
                if "Product Valid At" in line:
                    valid_at = line.split(":", 1)[1].strip()
                continue
            if not line.strip() or set(line.strip()) <= {"-"}:
                continue
            if "|" not in line:
                # The one header row: the longitude of every column.
                try:
                    lons = [float(x) for x in line.split()]
                except ValueError:
                    lons = []
                continue
            head, _, rest = line.partition("|")
            try:
                lat = float(head.strip())
                vals = [float(x) for x in rest.split()]
            except ValueError:
                continue
            for lon, v in zip(lons, vals):
                if v > 0:
                    cells.append([round(lon, 1), round(lat, 1), round(v, 1)])
        if not lons:
            self._error = "NOAA returned no D-RAP grid"
            return
        self._data = {"cells": cells, "valid_at": valid_at}
        self._at = time.time()
        self._error = ""


class Ionosondes:
    """Measured MUF and foF2 from the world's ionosondes, via prop.kc2g.com.

    Points, not a field. The familiar smooth MUF map is an interpolation of
    exactly these stations; drawing the measurements says what is known and
    where, and does not invent confidence between them.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._at = 0.0
        self._data: list | None = None
        self._error = ""

    def get(self, force: bool = False) -> dict:
        with self._lock:
            if not (self._data and time.time() - self._at < TTL and not force):
                self._refresh()
            if self._data is None:
                return {"ok": False, "error": self._error or "no data",
                        "stations": []}
            return {"ok": True, "stations": self._data,
                    "age_s": round(time.time() - self._at),
                    "credit": "prop.kc2g.com / GIRO"}

    def _refresh(self) -> None:
        try:
            r = requests.get(IONOSONDES, timeout=TIMEOUT,
                             headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            raw = r.json()
        except (requests.RequestException, ValueError) as exc:
            self._error = f"could not reach prop.kc2g.com: {exc}"
            return
        out = []
        for row in raw if isinstance(raw, list) else []:
            st = row.get("station") or {}
            try:
                lat = float(st.get("latitude"))
                lon = float(st.get("longitude"))
            except (TypeError, ValueError):
                continue
            if lon > 180:            # they publish 0..360
                lon -= 360
            muf = row.get("mufd")
            fof2 = row.get("fof2")
            if muf is None and fof2 is None:
                continue
            out.append({
                "name": st.get("name", ""), "code": st.get("code", ""),
                "lat": round(lat, 2), "lon": round(lon, 2),
                "mufd": round(float(muf), 1) if muf is not None else None,
                "fof2": round(float(fof2), 1) if fof2 is not None else None,
                "time": row.get("time", ""),
            })
        self._data = out
        self._at = time.time()
        self._error = ""
