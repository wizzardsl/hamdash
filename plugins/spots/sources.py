"""Where spots come from, and what they look like once they are here.

Fetching and normalising only - no caching, no Flask, no threads - so
`tests/test_spots.py` can drive every source with a recorded reply and check
the part that is easy to get wrong and impossible to see: the units.

**Three sources, three different units for a frequency.** Checked against
each live service rather than assumed:

* POTA gives kilohertz **as a string**: `"frequency": "28051.0"`.
* SOTA gives megahertz **as a float**: `"frequency": 7.004`.
* DX Summit gives kilohertz **as a float**: `"frequency": 3538.0`.

Getting one wrong does not throw: it puts a 7 MHz spot on 7 kHz, which
appears as a row with no band against it, or worse lands on a real band a
thousand times off. Everything is normalised to integer hertz here, once,
and `tests/test_spots.py` pins each one with a real record.

Times are normalised the same way, for the same reason: POTA and DX Summit
send a naive timestamp that is UTC, SOTA sends one with a `Z` and
microseconds. A naive string parsed as local time puts every spot hours into
the future and sorts the list backwards.
"""
from __future__ import annotations

import datetime as dt
import re

import requests

from hamdash.services.bands import for_hz as band_for

# Who we are, so the people running these services can see what is calling
# and have something to contact. Every one of them is somebody's hobby
# server; POTA's own page refreshes once a minute and that is the pace here.
try:
    from hamdash import __version__
except Exception:                                       # noqa: BLE001
    __version__ = "0"
UA = f"hamdash/{__version__} (+https://github.com/wizzardsl/hamdash)"

TIMEOUT = 20

POTA_SPOTS = "https://api.pota.app/spot/activator"
SOTA_SPOTS = "https://api2.sota.org.uk/api/spots/{count}/all/all"
DX_SPOTS = "https://www.dxsummit.fi/api/v1/spots"
CONTESTS = "https://www.qsodates.com/api/downloads?format=json"

KINDS = ("pota", "sota", "dx")


def _get(url: str, **params):
    r = requests.get(url, params=params or None, timeout=TIMEOUT,
                     headers={"User-Agent": UA, "Accept": "application/json"})
    r.raise_for_status()
    return r.json()


def _when(value) -> float:
    """Any of their timestamps, as epoch seconds UTC.

    POTA and DX Summit send a naive string that is UTC; SOTA sends one with
    a Z and microseconds. `fromisoformat` would read the naive ones as local
    time, which on this machine puts every spot seven hours into the future
    and sorts the newest to the bottom.
    """
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return 0.0
    try:
        when = dt.datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.timestamp()


def _hz(value, scale: float) -> int:
    """A frequency in hertz, from whatever this service calls one."""
    try:
        return int(round(float(str(value).strip()) * scale))
    except (TypeError, ValueError):
        return 0


def _spot(**kw) -> dict:
    """One row, the same shape whatever it came from.

    One shape because the card shows one list: a park, a summit and a DX
    spot are all "somebody is on this frequency now", and sorting three
    different records together is what the card would otherwise have to do
    in the browser, per filter, per redraw.
    """
    hz = int(kw.get("hz") or 0)
    row = {"kind": "", "call": "", "hz": hz, "band": band_for(hz), "mode": "",
           "mode_from": "", "at": 0.0, "ref": "", "place": "", "where": "",
           "grid": "", "lat": None, "lon": None, "spotter": "", "comment": "",
           "url": ""}
    row.update({k: v for k, v in kw.items() if k in row})
    row["call"] = str(row["call"] or "").upper().strip()
    row["mode"] = _mode(row["mode"])
    return row


def _mode(mode: str) -> str:
    """Their spelling, in ours.

    Nobody agrees: POTA says SSB and FT8, SOTA says DATA for anything
    digital, the cluster says whatever the spotter typed. Left broadly
    alone - a mode filter that silently renames things is worse than one
    that shows what was spotted - but the obvious synonyms are folded so
    "CW" means CW however it arrived.
    """
    name = str(mode or "").upper().strip()
    return {"PHONE": "SSB", "DIGI": "DATA", "DIGITAL": "DATA"}.get(name, name)


# Modes a spotter writes out in a cluster comment, as whole words. Matched
# without regard to case, except AM and FM, which are also English ("am")
# and so count only in capitals. Bare "PSK" is left out: "via PSK" is
# usually PSK Reporter, not PSK31.
_COMMENT_MODES = re.compile(
    r"(?<![\w/])(?:(?i:FT8|FT4|FST4|CW|SSB|USB|LSB|RTTY|PSK31|PSK63|JT65|JT9"
    r"|MSK144|Q65|JS8|SSTV|OLIVIA)|AM|FM)(?![\w/])")
_COMMENT_FOLD = {"USB": "SSB", "LSB": "SSB"}
# A mode right after one of these is a request, not a report: "PSE SSB" is
# a spotter asking a CW station to go to sideband. Found on the live feed -
# H49A on 12 m, shown as SSB on the strength of somebody hoping it was.
_ASKING = {"PSE", "PLS", "PLZ", "PLEASE", "QSY", "TRY"}


def mode_in_comment(comment) -> str:
    """The mode a cluster comment names outright, or "".

    The cluster has no mode field, so a spot from it carried none and
    every one of them read as unknown - though "FT8 -14 dB 1028 Hz" is the
    spotter saying exactly what they heard, no less than POTA's own mode
    field, which is also typed by the spotter. Only a comment naming one
    mode counts: "CW or SSB" is a question, not an answer, and gets none;
    and a mode asked for ("PSE SSB") is not the mode being heard.
    """
    text = str(comment or "")
    found = set()
    for m in _COMMENT_MODES.finditer(text):
        before = text[:m.start()].split()
        if before and before[-1].upper().strip(".,:!") in _ASKING:
            continue
        found.add(_COMMENT_FOLD.get(m.group(0).upper(), m.group(0).upper()))
    return found.pop() if len(found) == 1 else ""


# --------------------------------------------------------------- POTA
def pota(raw=None) -> list[dict]:
    """Parks on the Air. kHz as a string, and a naive UTC timestamp."""
    rows = raw if raw is not None else _get(POTA_SPOTS)
    out = []
    for r in rows or []:
        if r.get("invalid"):
            continue
        ref = str(r.get("reference") or "")
        out.append(_spot(
            kind="pota",
            call=r.get("activator"),
            hz=_hz(r.get("frequency"), 1_000),          # kHz, as a string
            mode=r.get("mode"),
            at=_when(r.get("spotTime")),
            ref=ref,
            place=r.get("name") or r.get("parkName") or "",
            where=r.get("locationDesc") or "",
            grid=r.get("grid6") or r.get("grid4") or "",
            lat=r.get("latitude"), lon=r.get("longitude"),
            spotter=r.get("spotter"),
            comment=r.get("comments"),
            url=f"https://pota.app/#/park/{ref}" if ref else "",
        ))
    return out


# --------------------------------------------------------------- SOTA
def sota(raw=None, count: int = 50) -> list[dict]:
    """Summits on the Air. **Megahertz**, not kilohertz.

    `/api/spots/{n}/all` is hard-deprecated and answers with a notice in
    place of the spots; the live one takes a third path segment. A reply
    whose rows say DEPRECATED is that notice, and it is dropped rather than
    drawn as a station called None on no band.
    """
    rows = raw if raw is not None else _get(SOTA_SPOTS.format(count=count))
    out = []
    for r in rows or []:
        call = r.get("activatorCallsign")
        if not call or str(r.get("summitCode") or "") == "DEPRECATED":
            continue
        ref = str(r.get("summitCode") or "")
        out.append(_spot(
            kind="sota",
            call=call,
            hz=_hz(r.get("frequency"), 1_000_000),      # MHz, as a float
            mode=r.get("mode"),
            at=_when(r.get("timeStamp")),
            ref=ref,
            place=r.get("summitName") or "",
            where=ref.split("/")[0] if "/" in ref else "",
            lat=r.get("latitude"), lon=r.get("longitude"),
            spotter=r.get("callsign"),
            comment=r.get("comments"),
            url=f"https://www.sotadata.org.uk/en/summit/{ref}" if ref else "",
        ))
    return out


# ----------------------------------------------------------- DX cluster
def dx(raw=None, limit: int = 60) -> list[dict]:
    """DX Summit's view of the cluster. kHz as a float."""
    rows = raw if raw is not None else _get(DX_SPOTS, limit=limit)
    out = []
    for r in rows or []:
        call = r.get("dx_call")
        if not call:
            continue
        # The cluster has no mode field. A comment that names one outright
        # supplies it, marked `mode_from: "comment"` so the card can say
        # where it came from and will not hand it to the radio; anything
        # vaguer leaves the mode empty rather than invented.
        mode = mode_in_comment(r.get("info"))
        out.append(_spot(
            kind="dx",
            call=call,
            hz=_hz(r.get("frequency"), 1_000),          # kHz, as a float
            mode=mode,
            mode_from="comment" if mode else "",
            at=_when(r.get("time")),
            place=r.get("dx_country") or "",
            lat=r.get("dx_latitude"), lon=r.get("dx_longitude"),
            spotter=r.get("de_call"),
            comment=r.get("info"),
        ))
    return out


FETCH = {"pota": pota, "sota": sota, "dx": dx}


# -------------------------------------------------------------- contests
def contests(raw=None) -> list[dict]:
    """What is on, and what is coming, from QSO Dates' bulk export.

    Their keyed API allows twenty requests a day, which is not a dashboard;
    the export is the same data, needs no key, and is what their own
    downloads page links to. Contests move once a year, so this is fetched
    rarely and kept on disk.
    """
    data = raw if raw is not None else _get(CONTESTS)
    rows = data.get("activities") if isinstance(data, dict) else data
    out = []
    for r in rows or []:
        start, end = _when(r.get("start_at")), _when(r.get("end_at"))
        if not start:
            continue
        out.append({
            "name": str(r.get("name") or "").strip(),
            "type": str(r.get("type") or "").strip(),
            "start": start, "end": end or start,
            "bands": [str(b) for b in (r.get("bands") or [])],
            "modes": [str(m) for m in (r.get("modes") or [])],
            "country": str(r.get("country") or ""),
            "website": str(r.get("website") or ""),
            "notes": str(r.get("description") or ""),
        })
    out.sort(key=lambda c: c["start"])
    return out


def live(rows: list[dict], now: float, soon_days: float = 14.0) -> dict:
    """Split contests into what is running and what is next.

    Running is the whole point of the card - "is there a contest on right
    now" is why the band is full of people - so it is answered separately
    rather than left as the top of a list sorted by start time, where a
    contest that began yesterday has already scrolled away.
    """
    on = [c for c in rows if c["start"] <= now <= c["end"]]
    ahead = [c for c in rows
             if now < c["start"] <= now + soon_days * 86400]
    return {"on": on, "soon": ahead[:40]}
