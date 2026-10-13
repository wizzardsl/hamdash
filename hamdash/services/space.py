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

import base64
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


def _raster(lons: set, lats: set, seen: dict) -> dict | None:
    """The grid as one byte a cell, west to east and south to north.

    Sent instead of a list of triples so the browser can draw the aurora as
    an *image* and let the GPU interpolate it, rather than as thousands of
    dots. A dot of fixed pixel size cannot tile the globe at more than one
    zoom, which is what made the old overlay look stippled.

    The step is measured rather than assumed - NOAA has published this at
    more than one resolution - and anything that is not a complete regular
    grid returns None, so the caller falls back to the cells.
    """
    if len(lons) < 2 or len(lats) < 2:
        return None
    xs, ys = sorted(lons), sorted(lats)
    step_x = min(b - a for a, b in zip(xs, xs[1:]))
    step_y = min(b - a for a, b in zip(ys, ys[1:]))
    if step_x <= 0 or step_y <= 0:
        return None
    w, h = len(xs), len(ys)
    if w * h != len(seen):          # ragged, or duplicated: not a grid
        return None
    buf = bytearray(w * h)
    for (lon, lat), pct in seen.items():
        ix = int(round((lon - xs[0]) / step_x))
        iy = int(round((lat - ys[0]) / step_y))
        if 0 <= ix < w and 0 <= iy < h:
            buf[iy * w + ix] = max(0, min(255, pct))
    # Longitude is NOAA's 0..360. Roll it to start at -180 so the image
    # lines up with the map without the browser having to think about it.
    if xs[0] >= 0 and xs[-1] > 180:
        half = int(round(180 / step_x))
        rolled = bytearray(w * h)
        for iy in range(h):
            row = buf[iy * w:(iy + 1) * w]
            rolled[iy * w:(iy + 1) * w] = row[half:] + row[:half]
        buf = rolled
        lon0 = xs[0] - 180
    else:
        lon0 = xs[0]
    return {"w": w, "h": h, "lon0": lon0, "lat0": ys[0],
            "step_lon": step_x, "step_lat": step_y,
            "data": base64.b64encode(bytes(buf)).decode("ascii")}

class Aurora:
    """OVATION aurora probability, cached and thinned."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._at = 0.0
        self._data: dict | None = None
        self._error = ""

    def get(self, threshold: int = 5, force: bool = False,
            grid: bool = False) -> dict:
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
            out = {"ok": True, "cells": cells, "threshold": threshold,
                   "observed": self._data["observed"],
                   "forecast": self._data["forecast"],
                   "age_s": round(time.time() - self._at),
                   "total_cells": len(self._data["cells"])}
            # `grid=True` means "the raster, or the cells only if you
            # cannot". Sending both would be 85 kB of image on top of 115
            # kB of triples describing the same field, so the cells go
            # when the raster is there - and stay when it is not, which is
            # what the caller falls back to.
            if grid and self._data.get("grid"):
                out["grid"] = self._data["grid"]
                out["cells"] = []
            return out

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
        # The grid is read as well as thinned. Measured against a live run:
        # 360 x 181 at exactly 1 degree, 65160 rows with no gaps. One byte a
        # cell is 85 kB of base64 for the *whole* field - smaller than the
        # 115 kB of sparse triples that thresholding at 8% produces, and it
        # keeps the faint edge that a threshold cuts off square.
        lons, lats = set(), set()
        seen = {}
        for row in coords:
            try:
                lon, lat, pct = float(row[0]), float(row[1]), int(row[2])
            except (TypeError, ValueError, IndexError):
                continue
            lons.add(lon)
            lats.add(lat)
            seen[(lon, lat)] = pct
            if pct <= 0:
                continue
            # NOAA gives longitude 0..360; the rest of the world uses -180..180.
            if lon > 180:
                lon -= 360
            cells.append([round(lon, 1), round(lat, 1), pct])
        self._data = {"cells": cells,
                      "grid": _raster(lons, lats, seen),
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

    def get(self, threshold: float = 1.0, force: bool = False,
            grid: bool = False) -> dict:
        with self._lock:
            if not (self._data and time.time() - self._at < TTL and not force):
                self._refresh()
            if not self._data:
                return {"ok": False, "error": self._error or "no D-RAP data",
                        "cells": []}
            cells = [c for c in self._data["cells"] if c[2] >= threshold]
            out = {"ok": True, "cells": cells, "threshold": threshold,
                   "valid_at": self._data["valid_at"],
                   "quiet": not cells,
                   "age_s": round(time.time() - self._at)}
            # Same bargain as the aurora: the raster instead of the cells,
            # not as well as. `quiet` is worked out from the cells, so it
            # is still right when they have been dropped.
            if grid and self._data.get("grid"):
                out["grid"] = self._data["grid"]
                out["cells"] = []
            return out

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
        lon_set, lat_set, seen = set(), set(), {}
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
                lon_set.add(lon)
                lat_set.add(lat)
                # Whole MHz in the raster: it is a picture, and a tenth of
                # a megahertz is finer than a pixel. `cells` keeps the
                # decimal for anything that wants to read a number.
                seen[(lon, lat)] = int(round(v))
                if v > 0:
                    cells.append([round(lon, 1), round(lat, 1), round(v, 1)])
        if not lons:
            self._error = "NOAA returned no D-RAP grid"
            return
        self._data = {"cells": cells, "valid_at": valid_at,
                      "grid": _raster(lon_set, lat_set, seen)}
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
