"""Build hamdash/static/vendor/ituregions.js: the boundaries of the three ITU
Regions, from the Radio Regulations' own definition.

    pip install shapely                  # for this script only, not for hamdash
    python tools/regions.py              # fetch the pinned country data, write
    python tools/regions.py FILE         # from a local copy of that GeoJSON

Nobody publishes the Regions as data, so they are built from the rule. The
rule is Radio Regulations Article 5 (Nos. 5.3-5.9), as reproduced in the
US 47 CFR 2.104(b), which is public domain:

* **Region 1** is the area between line B (west) and line A (east), less any
  of Iran between them, plus the whole of the territory of Armenia,
  Azerbaijan, the Russian Federation, Georgia, Kazakhstan, Mongolia,
  Uzbekistan, Kyrgyzstan, Tajikistan, Turkmenistan, Turkey and Ukraine, and
  the area north of the Russian Federation between lines A and C.
* **Region 2** is the area between line C (west) and line B (east).
* **Region 3** is the area between line A (west) and line C (east), less
  those twelve countries and the area north of Russia, plus all of Iran.
* **Line A**: the North Pole along 40 E to 40 N; great circle to 60 E on the
  Tropic of Cancer; along 60 E to the South Pole.
* **Line B**: the North Pole along 10 W to 72 N; great circle to 50 W, 40 N;
  great circle to 20 W, 10 S; along 20 W to the South Pole.
* **Line C**: the North Pole by great circle (which from a pole is a
  meridian) to 65 30' N on the international boundary in Bering Strait;
  great circle to 165 E, 50 N; great circle to 170 W, 10 N; along 10 N to
  120 W; along 120 W to the South Pole.

Country outlines are Natural Earth's 1:110m admin-0 countries, public domain,
the same scale as the borders already on the map, pinned to a commit.

Three things are approximations, and the file says so:

* **Territory includes water; the outlines do not.** Taken literally, the
  Caspian, the White Sea and the eastern end of the Black Sea - enclosed by
  Region 1 countries but east of line A - would be pockets of Region 3. So
  water enclosed by the named countries and Iran, up to about 2 x CLOSE
  degrees across, is given to their side; land is never filled, so a
  concave corner of China cannot be swallowed. Further out to sea the
  boundary follows the coast, where the real one is the territorial sea.
* **"The area to the north of the Russian Federation"** is taken as the part
  of the A-to-C band north of ARCTIC degrees - the Arctic Ocean - rather
  than guessed at further south.
* **The lines are drawn at 1-degree steps** along each arc, then simplified
  like the zones.
"""
from __future__ import annotations

import json
import math
import sys
import urllib.request
from pathlib import Path

NE_COMMIT = "ca96624a56bd078437bca8184e78163e5039ad19"
NE_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
          f"{NE_COMMIT}/geojson/ne_110m_admin_0_countries.geojson")
OUT = Path(__file__).resolve().parent.parent / "hamdash" / "static" / "vendor" / "ituregions.js"

REGION_1_TERRITORY = ("ARM", "AZE", "RUS", "GEO", "KAZ", "MNG", "UZB", "KGZ",
                      "TJK", "TKM", "TUR", "UKR")
IRAN = "IRN"

TROPIC = 23.44          # the Tropic of Cancer, degrees north
BERING_LON = -168.9769  # the US-Russia boundary at 65 30' N, 168 58' 37" W
STEP = 1.0              # degrees between points along an arc
CLOSE = 2.5             # degrees: enclosed water up to ~5 degrees across
ARCTIC = 66.5           # "north of the Russian Federation": the Arctic Ocean
TOLERANCE = 0.1         # degrees, simplification - as for the zones
MIN_LENGTH = 1.0        # degrees: shorter pieces are slivers, not edges

# Where the words go: inside each region, away from the boundaries.
LABELS = [
    {"text": "Region 1", "at": [8, 18]},
    {"text": "Region 1", "at": [60, 95]},
    {"text": "Region 2", "at": [20, -95]},
    {"text": "Region 2", "at": [-15, -55]},
    {"text": "Region 3", "at": [-10, 125]},
    {"text": "Region 3", "at": [25, 80]},
]


# ---- geometry on the sphere -----------------------------------------------
def _xyz(lon, lat):
    lo, la = math.radians(lon), math.radians(lat)
    return (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))


def great_circle(a, b, step=STEP):
    """Points along the great circle from a to b, as (lon, lat), with the
    longitudes kept continuous from a's - so an arc across the date line
    goes to -195 rather than jumping to 165."""
    p, q = _xyz(*a), _xyz(*b)
    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(p, q))))
    ang = math.acos(dot)
    n = max(1, math.ceil(math.degrees(ang) / step))
    out, prev = [], a[0]
    for k in range(n + 1):
        t = k / n
        if ang < 1e-12:
            v = p
        else:
            s1 = math.sin((1 - t) * ang) / math.sin(ang)
            s2 = math.sin(t * ang) / math.sin(ang)
            v = tuple(s1 * x + s2 * y for x, y in zip(p, q))
        lat = math.degrees(math.asin(max(-1.0, min(1.0, v[2]))))
        lon = math.degrees(math.atan2(v[1], v[0]))
        lon += 360 * round((prev - lon) / 360)       # stay continuous
        prev = lon
        out.append((lon, lat))
    out[0], out[-1] = tuple(a), tuple(b)               # exact ends
    return out


def meridian(lon, lat0, lat1, step=STEP):
    n = max(1, math.ceil(abs(lat1 - lat0) / step))
    return [(lon, lat0 + (lat1 - lat0) * k / n) for k in range(n + 1)]


def parallel(lat, lon0, lon1, step=STEP):
    n = max(1, math.ceil(abs(lon1 - lon0) / step))
    return [(lon0 + (lon1 - lon0) * k / n, lat) for k in range(n + 1)]


def _join(*runs):
    out = []
    for run in runs:
        out += run if not out else run[1:]
    return out


def line_a():
    return _join(meridian(40, 90, 40),
                 great_circle((40, 40), (60, TROPIC)),
                 meridian(60, TROPIC, -90))


def line_b():
    return _join(meridian(-10, 90, 72),
                 great_circle((-10, 72), (-50, 40)),
                 great_circle((-50, 40), (-20, -10)),
                 meridian(-20, -10, -90))


def line_c():
    """In the western frame: 165 E is -195 here, so the line is continuous
    across the date line."""
    return _join(meridian(BERING_LON, 90, 65.5),
                 great_circle((BERING_LON, 65.5), (-195, 50)),
                 great_circle((-195, 50), (-170, 10)),
                 parallel(10, -170, -120),
                 meridian(-120, 10, -90))


# ---- the regions ----------------------------------------------------------
def regions(countries: dict):
    """The three regions as shapely geometries in one longitude frame running
    from about -200 to 240: Region 2 west of 0, Region 3 east of line A
    with line C carried round to 165..240. `countries` maps an ADM0_A3 code
    to a shapely geometry."""
    from shapely.geometry import Polygon, box
    from shapely.ops import unary_union
    from shapely import affinity

    a, b, c = line_a(), line_b(), line_c()
    c_east = [(x + 360, y) for x, y in c]
    r1_lines = Polygon(b + a[::-1]).buffer(0)
    r2_lines = Polygon(c + b[::-1]).buffer(0)
    r3_lines = Polygon(a + c_east[::-1]).buffer(0)

    def frame(geom):
        """A country in the eastern frame: Russia's Chukotka sits at -180 to
        -169 in the source, and belongs next to the rest of Russia at 180+."""
        parts = getattr(geom, "geoms", [geom])
        moved = [affinity.translate(p, xoff=360) if p.bounds[2] < -150 else p
                 for p in parts]
        return unary_union(moved)

    land = unary_union([frame(g) for g in countries.values()])
    territory = unary_union([frame(countries[k]) for k in REGION_1_TERRITORY])
    iran = frame(countries[IRAN])
    # Water these countries enclose, and only water.
    closed = unary_union([territory, iran]).buffer(CLOSE).buffer(-CLOSE)
    enclosed_water = closed.difference(land)
    arctic = r3_lines.intersection(box(0, ARCTIC, 400, 90))

    region1_extra = unary_union([territory, enclosed_water.difference(iran), arctic])
    r1 = unary_union([r1_lines.difference(iran), region1_extra.intersection(r3_lines)])
    r3 = unary_union([r3_lines.difference(region1_extra), iran])
    return r1, r2_lines, r3


def boundaries(r1, r2, r3) -> list[list[list[float]]]:
    """Every edge between two regions, as lines, without the polar edges the
    polygons close along."""
    from shapely.ops import unary_union, linemerge
    from shapely.geometry import LineString
    from shapely import affinity

    edges = unary_union([r.boundary for r in (r1, r2, r3)])
    keep = []
    for line in getattr(edges, "geoms", [edges]):
        pts = list(line.coords)
        run = [pts[0]]
        for p, q in zip(pts, pts[1:]):
            polar = abs(p[1]) > 89.99 and abs(q[1]) > 89.99
            if polar:
                if len(run) > 1:
                    keep.append(LineString(run))
                run = [q]
            else:
                run.append(q)
        if len(run) > 1:
            keep.append(LineString(run))
    merged = list(getattr(linemerge(keep), "geoms", [linemerge(keep)]))
    # Line C is an edge of Region 2 at -120 and of Region 3 at +240: the same
    # line in two frames, which the world copies would draw twice over.
    western = unary_union([ln for ln in merged if ln.bounds[2] <= 180]).buffer(0.05)
    merged = [ln for ln in merged
              if ln.bounds[2] <= 180 or not western.contains(affinity.translate(ln, xoff=-360))]
    # Slivers where two outlines nearly meet (the Iran-Turkey-Iraq corner)
    # are the scale of the data, not a boundary anybody could see.
    merged = [ln for ln in merged if ln.length >= MIN_LENGTH]
    out = []
    for line in merged:
        simple = line.simplify(TOLERANCE, preserve_topology=False)
        pts = []
        for x, y in simple.coords:
            pt = [round(x, 1), round(y, 1)]
            if not pts or pts[-1] != pt:
                pts.append(pt)
        if len(pts) > 1:
            out.append(pts)
    return out


def region_of(r1, r2, r3, lon, lat) -> int | None:
    """Which region a place is in, by the built shapes - for the tests."""
    from shapely.geometry import Point
    for dx in (0, 360, -360):
        pt = Point(lon + dx, lat)
        for n, r in ((1, r1), (2, r2), (3, r3)):
            if r.contains(pt):
                return n
    return None


def load(raw: bytes) -> dict:
    from shapely.geometry import shape
    geo = json.loads(raw)
    return {f["properties"]["ADM0_A3"]: shape(f["geometry"]).buffer(0)
            for f in geo["features"]}


def main(argv) -> int:
    if len(argv) > 1:
        raw = Path(argv[1]).read_bytes()
    else:
        with urllib.request.urlopen(NE_URL, timeout=60) as r:
            raw = r.read()
    r1, r2, r3 = regions(load(raw))
    lines = boundaries(r1, r2, r3)
    points = sum(len(ln) for ln in lines)
    body = json.dumps({"lines": lines, "labels": LABELS}, separators=(",", ":"))
    OUT.write_text(
        "/* ITU Regions 1, 2 and 3: the boundaries between them.\n\n"
        "   Built by tools/regions.py from the Radio Regulations' definition\n"
        "   (Article 5, Nos. 5.3-5.9, as in 47 CFR 2.104(b)) and Natural\n"
        "   Earth's 1:110m countries (public domain) at commit\n"
        f"   {NE_COMMIT[:12]}. At sea near the named countries the boundary\n"
        "   follows the coast, where the real one is the territorial sea; see\n"
        "   the tool. Do not edit by hand; change the tool and run it again. */\n"
        f"window.ITU_REGIONS = {body};\n", encoding="utf-8")
    print(f"{OUT.name}: {len(lines)} lines, {points} points, "
          f"{OUT.stat().st_size // 1024} kB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
