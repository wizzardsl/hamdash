"""Which band a frequency is in. One table, because three disagreed.

`radio.py`, `logbook.py` and the PSK card each carried their own copy, and
they had drifted: 5.3 MHz was "60m" to the logbook and "out of band" to the
rig, 17m had two different pairs of edges, and only one of them had heard of
2200m, 630m or 4m. A QSO's band is written into the log and shown on the map,
so two answers to the same question is two answers in front of the operator.

Edges are the IARU allocations, widened only where logging wants it: 60m is
channelised in most countries and the channels differ, so the span covers all
of them rather than calling a legal QSO out of band.
"""
from __future__ import annotations

# (low MHz, high MHz, name), low to high.
BANDS: list[tuple[float, float, str]] = [
    (0.1357, 0.1378, "2200m"),
    (0.472, 0.479, "630m"),
    (1.8, 2.0, "160m"),
    (3.5, 4.0, "80m"),
    (5.25, 5.45, "60m"),
    (7.0, 7.3, "40m"),
    (10.1, 10.15, "30m"),
    (14.0, 14.35, "20m"),
    (18.068, 18.168, "17m"),
    (21.0, 21.45, "15m"),
    (24.89, 24.99, "12m"),
    (28.0, 29.7, "10m"),
    (50.0, 54.0, "6m"),
    (70.0, 70.5, "4m"),
    (144.0, 148.0, "2m"),
    (222.0, 225.0, "1.25m"),
    (420.0, 450.0, "70cm"),
    (902.0, 928.0, "33cm"),
    (1240.0, 1300.0, "23cm"),
    (2300.0, 2450.0, "13cm"),
]

NAMES = [name for _, _, name in BANDS]


def for_mhz(mhz: float | None) -> str:
    """The band a frequency in MHz falls in, or "" if it is in none."""
    try:
        f = float(mhz)
    except (TypeError, ValueError):
        return ""
    return next((n for lo, hi, n in BANDS if lo <= f <= hi), "")


def for_hz(hz: int | None) -> str:
    """The same, for a frequency in Hz."""
    try:
        return for_mhz(float(hz) / 1e6)
    except (TypeError, ValueError):
        return ""


def as_json() -> list[list]:
    """The table, for anything that cannot import this - the browser."""
    return [[lo, hi, name] for lo, hi, name in BANDS]
