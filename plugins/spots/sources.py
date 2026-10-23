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
# NetLogger's list of nets in progress. Read-only and keyless - the key in
# their API is for subscribing to a net and sending messages, which this
# does not do. Not documented anywhere public; RagChew (MIT, ragchew.site)
# polls it the same way, once a minute, backing off on a 429.
NETS = "https://www.netlogger.org/api/GetActiveNets.php"
NETS_MAX_BYTES = 2_000_000

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
           "mode_from": "", "sideband": "", "at": 0.0, "ref": "", "place": "", "where": "",
           "grid": "", "lat": None, "lon": None, "spotter": "", "comment": "",
           "url": ""}
    row.update({k: v for k, v in kw.items() if k in row})
    row["call"] = str(row["call"] or "").upper().strip()
    row["mode"] = _mode(row["mode"])
    if not row["mode"]:
        # Last resort, after the source's own field and the comment.
        row["mode"] = mode_by_frequency(hz)
        row["mode_from"] = "frequency" if row["mode"] else ""
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
# A mode shortly after one of these is a request, not a report: "PSE SSB"
# is a spotter asking a CW station to go to sideband. Found on the live
# feed - H49A on 12 m, shown as SSB on the strength of somebody hoping it
# was - and then "PSE 18 SSB good time", where a band sits between the two,
# so the request word is looked for in the few words before, not only the
# one right before.
_ASKING = {"PSE", "PLS", "PLZ", "PLEASE", "QSY", "TRY"}
_ASK_REACH = 3

# Where FT8 and FT4 live: the conventional dial frequency on each band, in
# kHz, with the signal anywhere in the 3 kHz of audio above it. Measured
# against 500 live cluster spots before it was written: where a comment
# also named a mode, this agreed 143 times in 144, and the one
# disagreement was a comment asking for SSB, not reporting it. Only these
# two modes: their windows are narrow and used by nearly everyone, which
# no CW or phone segment is - the CW end of a band is full of digital
# modes, and the phone segments differ by region and licence.
_FT8_DIALS = (1840, 3573, 5357, 7074, 10136, 14074, 18100, 21074, 24915,
              28074, 50313, 144174)
_FT4_DIALS = (3575, 7047.5, 10140, 14080, 18104, 21140, 24919, 28180, 50318)
_AUDIO_KHZ = 3.0


def mode_by_frequency(hz) -> str:
    """FT8 or FT4 if `hz` is in one of their windows, else "".

    A guess, and treated as one: the caller marks it `mode_from:
    "frequency"` and uses it only when neither the source nor the comment
    gave a mode. A DXpedition working FT8 off the usual spot (S9R on
    10.133) is left blank rather than misread.
    """
    try:
        khz = float(hz) / 1000.0
    except (TypeError, ValueError):
        return ""
    for name, dials in (("FT8", _FT8_DIALS), ("FT4", _FT4_DIALS)):
        if any(d <= khz <= d + _AUDIO_KHZ for d in dials):
            return name
    return ""


def mode_in_comment(comment) -> str:
    """The mode a cluster comment names outright, or "".

    The cluster has no mode field, so a spot from it carried none and
    every one of them read as unknown - though "FT8 -14 dB 1028 Hz" is the
    spotter saying exactly what they heard, no less than POTA's own mode
    field, which is also typed by the spotter. Only a comment naming one
    mode counts: "CW or SSB" is a question, not an answer, and gets none;
    and a mode asked for ("PSE SSB") is not the mode being heard.
    """
    found = {_COMMENT_FOLD.get(w, w) for w in _named(comment)}
    return found.pop() if len(found) == 1 else ""


def sideband_in_comment(comment) -> str:
    """USB or LSB if the comment says which, else "".

    Only for a comment whose mode is SSB and that names one sideband:
    "LSB JN54kp" is a station on LSB, which beats the 10 MHz rule of
    thumb the card otherwise tunes by. "USB or LSB" says neither.
    """
    if mode_in_comment(comment) != "SSB":
        return ""
    sides = {w for w in _named(comment) if w in ("USB", "LSB")}
    return sides.pop() if len(sides) == 1 else ""


def _named(comment) -> list[str]:
    """Every mode word a comment reports, upper-cased, requests left out."""
    text = str(comment or "")
    out = []
    for m in _COMMENT_MODES.finditer(text):
        before = text[:m.start()].split()[-_ASK_REACH:]
        if any(w.upper().strip(".,:!") in _ASKING for w in before):
            continue
        out.append(m.group(0).upper())
    return out


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
            sideband=sideband_in_comment(r.get("info")),
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


# ------------------------------------------------------------------- nets
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_BAND_WORD = re.compile(r"\b\d+(?:\.\d+)?\s*(?:cm|m|met(?:er|re)s?)\b", re.I)


def net_hz(text, band: str = "") -> int:
    """A net's frequency in hertz, from whatever its logger typed.

    NetLogger's frequency is free text, and live it arrives as "147.090"
    (MHz), "3916" (kHz), "3.919" (MHz) and "2m" (no number at all) in one
    reply. The number is read as MHz, kHz or Hz by size, like the rig
    card's box - except that the net's own Band field breaks a tie when it
    agrees with one reading: "1296" is 23 cm in megahertz and nothing in
    kilohertz. No number is 0, never a guess - "2m" names a band, not a
    frequency, and a click must not tune the radio somewhere invented.
    """
    text = str(text or "")
    # "2m", "70cm", "40 meters": a band written where a frequency goes.
    if _BAND_WORD.search(text):
        return 0
    m = _NUMBER.search(text)
    if not m:
        return 0
    v = float(m.group())
    if v <= 0:
        return 0
    readings = [int(round(v * k)) for k in (1e6, 1e3, 1)]
    if band:
        want = str(band).strip().lower()
        for hz in readings:
            if (band_for(hz) or "").lower() == want:
                return hz
    if "." not in m.group() and v >= 1_000_000:
        return readings[2]
    return readings[0] if v < 1000 else readings[1] if v < 1_000_000 else readings[2]


def _net_time(text) -> float:
    """NetLogger's "2026-10-23 02:58:59", which is UTC (its header says so)."""
    try:
        when = dt.datetime.strptime(str(text or "").strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return 0.0
    return when.replace(tzinfo=dt.timezone.utc).timestamp()


def nets(raw=None) -> list[dict]:
    """Nets in progress, from NetLogger.

    `raw` is the XML, for tests. An empty list is NetLogger saying "404 ...
    query returned an empty result", which is how it says no nets are on -
    an answer, not a failure. Any other response code is a failure, so the
    feed backs off and keeps the list it had.
    """
    import xml.etree.ElementTree as ET

    if raw is None:
        r = requests.get(NETS, timeout=TIMEOUT,
                         headers={"User-Agent": UA, "Accept": "text/xml"})
        r.raise_for_status()
        raw = r.content[:NETS_MAX_BYTES]
    root = ET.fromstring(raw)
    code = (root.findtext("./ServerList/ResponseCode") or "").strip()
    if not code.startswith("200"):
        if code.startswith("404") and "empty" in (root.findtext("./Error") or "").lower():
            return []
        raise ValueError(f"NetLogger answered {code or 'without a response code'}"
                         f": {(root.findtext('./Error') or '').strip()}")
    out = []
    for server in root.findall("./ServerList/Server"):
        server_name = (server.findtext("ServerName") or "").strip()
        for n in server.findall("Net"):
            text = lambda tag: (n.findtext(tag) or "").strip()  # noqa: E731
            name = text("NetName")
            if not name:
                continue
            band = text("Band")
            hz = net_hz(text("Frequency"), band)
            try:
                subs = int(text("SubscriberCount") or 0)
            except ValueError:
                subs = 0
            out.append({
                "name": name, "freq": text("Frequency"), "hz": hz,
                # Their band is what the net said; ours only when they
                # gave none. A frequency of "2m" still gets a band.
                "band": band or (band_for(hz) if hz else ""),
                "mode": _mode(text("Mode")),
                "control": text("NetControl"), "logger": text("Logger"),
                "started": _net_time(text("Date")), "subscribers": subs,
                "server": server_name,
            })
    out.sort(key=lambda n: -n["started"])
    return out

