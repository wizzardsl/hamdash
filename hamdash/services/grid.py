"""Maidenhead locators, great-circle distance and bearing.

Pure stdlib. 4-, 6- and 8-character locators supported.
"""
from __future__ import annotations

import math
import re
import string

R_KM = 6371.0088
LOC_RE = re.compile(r"^[A-R]{2}[0-9]{2}([A-X]{2}([0-9]{2})?)?$", re.I)


def valid(locator: str) -> bool:
    return bool(LOC_RE.match(locator.strip()))


def to_latlon(locator: str) -> tuple[float, float]:
    """Centre of the square. Raises ValueError on a bad locator."""
    loc = locator.strip().upper()
    if not valid(loc):
        raise ValueError(f"{locator!r} is not a Maidenhead locator")
    lon = (string.ascii_uppercase.index(loc[0]) * 20) - 180
    lat = (string.ascii_uppercase.index(loc[1]) * 10) - 90
    lon += int(loc[2]) * 2
    lat += int(loc[3]) * 1
    if len(loc) >= 6:
        lon += string.ascii_uppercase.index(loc[4]) * (2 / 24)
        lat += string.ascii_uppercase.index(loc[5]) * (1 / 24)
        if len(loc) >= 8:
            lon += int(loc[6]) * (2 / 240)
            lat += int(loc[7]) * (1 / 240)
            lon += 1 / 240
            lat += 1 / 480
        else:
            lon += 1 / 24
            lat += 1 / 48
    else:
        lon += 1.0
        lat += 0.5
    return lat, lon


def to_locator(lat: float, lon: float, precision: int = 6) -> str:
    lon = min(max(lon, -180), 179.999)
    lat = min(max(lat, -90), 89.999)
    x = lon + 180
    y = lat + 90
    out = string.ascii_uppercase[int(x // 20)] + string.ascii_uppercase[int(y // 10)]
    x %= 20
    y %= 10
    out += f"{int(x // 2)}{int(y // 1)}"
    if precision >= 6:
        x %= 2
        y %= 1
        out += (string.ascii_lowercase[int(x / (2 / 24))]
                + string.ascii_lowercase[int(y / (1 / 24))])
    return out


def distance_bearing(from_loc: str, to_loc: str) -> dict:
    lat1, lon1 = to_latlon(from_loc)
    lat2, lon2 = to_latlon(to_loc)
    return {
        "from": {"locator": from_loc.upper(), "lat": lat1, "lon": lon1},
        "to": {"locator": to_loc.upper(), "lat": lat2, "lon": lon2},
        "km": round(haversine(lat1, lon1, lat2, lon2), 1),
        "miles": round(haversine(lat1, lon1, lat2, lon2) * 0.621371, 1),
        "bearing": round(bearing(lat1, lon1, lat2, lon2), 1),
        "back_bearing": round(bearing(lat2, lon2, lat1, lon1), 1),
    }


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_KM * math.asin(math.sqrt(a))


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360
