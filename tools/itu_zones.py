"""Build hamdash/static/vendor/ituzones.js from HB9HIL's ITU zone GeoJSON.

    python tools/itu_zones.py            # fetch the pinned commit and write
    python tools/itu_zones.py FILE       # use a local copy of the GeoJSON

The source is https://github.com/HB9HIL/hamradio-zones-geojson (MIT,
copyright 2024 HB9HIL), the data Wavelog draws its zone map from. It is
pinned to a commit so a rebuild is the same file unless somebody chooses
to move it. 90 zones, one polygon each, with a label position.

What is done to it, and why:

* **Simplified and rounded to 0.1 degrees**, the same as `world.js` and
  `borders.js`. 73,000 points is 1.5 MB for lines a few pixels wide; the
  zone edges are mostly meridians and parallels, which simplify to almost
  nothing.
* **Kept unwrapped.** Zones across the date line come with longitudes like
  -200 and 191, which is exactly what the maps want: they draw the world
  more than once and unwrap lines rather than split them (see "Longitudes
  are unwrapped" in CLAUDE.md).
* **Outlines, not polygons, and no edges along +-180.** The two polar caps
  (74 and 75) are rings that run the whole way round and close with a
  360-degree jump and two vertical edges on the date line. Drawn, those are
  seams at +-180 near the poles, so a segment lying on +-180, or jumping
  more than half the world, is dropped and the ring is cut there.
"""
from __future__ import annotations

import json
import math
import sys
import urllib.request
from pathlib import Path

COMMIT = "091b813f35c899af194d028d3841fc10b16fbd45"
URL = ("https://raw.githubusercontent.com/HB9HIL/hamradio-zones-geojson/"
       f"{COMMIT}/ituzones.geojson")
OUT = Path(__file__).resolve().parent.parent / "hamdash" / "static" / "vendor" / "ituzones.js"
TOLERANCE = 0.1         # degrees, Douglas-Peucker
ROUND = 1               # decimal places


def _dp(points, tol):
    """Douglas-Peucker, iteratively so a long ring cannot hit the recursion
    limit."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = points[a], points[b]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy)
        worst, at = 0.0, None
        for i in range(a + 1, b):
            x, y = points[i]
            d = (abs(dy * x - dx * y + x2 * y1 - y2 * x1) / norm if norm
                 else math.hypot(x - x1, y - y1))
            if d > worst:
                worst, at = d, i
        if at is not None and worst > tol:
            keep[at] = True
            stack += [(a, at), (at, b)]
    return [p for p, k in zip(points, keep) if k]


def _seam(p, q) -> bool:
    """A segment on the date line, or one that jumps half the world."""
    on_edge = abs(abs(p[0]) - 180) < 1e-6 and abs(abs(q[0]) - 180) < 1e-6 \
        and abs(p[0] - q[0]) < 1e-6
    return on_edge or abs(p[0] - q[0]) > 180


PARALLEL_STEP = 5.0     # degrees between points on a whole parallel


def _whole_parallel(p, q) -> bool:
    """+180 to -180 (or back) at one latitude: the source's way of writing a
    line right round the world, which is a real zone edge (the polar caps
    are bounded by 80 and 85 degrees). Not a seam."""
    return (abs(abs(p[0]) - 180) < 1e-6 and abs(abs(q[0]) - 180) < 1e-6
            and abs(p[0] - q[0]) > 180 and abs(p[1] - q[1]) < 1e-6)


def lines(ring):
    """A closed ring as one or more open lines, cut wherever it has a seam.

    A whole parallel becomes its own line from -180 to 180 with a point
    every PARALLEL_STEP degrees - straight between two points is straight
    in projected space, which on the azimuthal map is a chord through the
    pole rather than a circle round it.
    """
    out, cur = [], [ring[0]]
    for p, q in zip(ring, ring[1:]):
        if _whole_parallel(p, q):
            if len(cur) > 1:
                out.append(cur)
            n = int(360 / PARALLEL_STEP)
            out.append([(-180 + i * PARALLEL_STEP, p[1]) for i in range(n + 1)])
            cur = [q]
        elif _seam(p, q):
            if len(cur) > 1:
                out.append(cur)
            cur = [q]
        else:
            cur.append(q)
    if len(cur) > 1:
        out.append(cur)
    return out


def build(geo: dict) -> list[dict]:
    zones = []
    for f in geo["features"]:
        props = f["properties"]
        n = int(props["itu_zone_number"])
        lat, lon = props["itu_zone_name_loc"]
        parts = []
        for ring in f["geometry"]["coordinates"]:
            for line in lines([(float(x), float(y)) for x, y in ring]):
                # A whole parallel is straight in degrees, so simplifying
                # would take it back to its two ends.
                whole = len(line) > 2 and len({pt[1] for pt in line}) == 1 \
                    and line[0][0] == -180 and line[-1][0] == 180
                simple = line if whole else _dp(line, TOLERANCE)
                pts = []
                for x, y in simple:
                    pt = [round(x, ROUND), round(y, ROUND)]
                    if not pts or pts[-1] != pt:
                        pts.append(pt)
                if len(pts) > 1:
                    parts.append(pts)
        zones.append({"n": n, "at": [round(lat, ROUND), round(lon, ROUND)],
                      "lines": parts})
    zones.sort(key=lambda z: z["n"])
    return zones


def main(argv) -> int:
    if len(argv) > 1:
        raw = Path(argv[1]).read_bytes()
    else:
        with urllib.request.urlopen(URL, timeout=60) as r:
            raw = r.read()
    zones = build(json.loads(raw))
    points = sum(len(p) for z in zones for p in z["lines"])
    body = json.dumps(zones, separators=(",", ":"))
    OUT.write_text(
        "/* ITU zones: outlines and label positions for the 90 zones.\n\n"
        "   From HB9HIL's hamradio-zones-geojson, MIT licence, copyright 2024\n"
        "   HB9HIL - see ITU-ZONES-LICENSE.txt beside this file. Built by\n"
        f"   tools/itu_zones.py from commit {COMMIT[:12]}: outlines\n"
        "   simplified and rounded to 0.1 degrees, longitudes left unwrapped,\n"
        "   and edges lying on the date line dropped. Do not edit by hand;\n"
        "   change the tool and run it again. */\n"
        f"window.ITU_ZONES = {body};\n", encoding="utf-8")
    print(f"{OUT.name}: {len(zones)} zones, {points} points, "
          f"{OUT.stat().st_size // 1024} kB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
