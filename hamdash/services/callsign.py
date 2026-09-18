"""Callsign lookup: provider in front, SQLite cache behind, prefix table as
the floor so the map card still has something to draw when you are offline.

Providers:
  callook  free, no account, US callsigns only
  hamqth   free account needed (session id, re-auth on expiry)
  offline  prefix table only
"""
from __future__ import annotations

import json
import sqlite3
import time
import xml.etree.ElementTree as ET

import requests

HAMQTH_NS = "{https://www.hamqth.com}"

# Short on purpose: replace with a cty.dat parser when you want real DXCC.
PREFIX = {
    "VE": ("Canada", "FN25"), "VA": ("Canada", "FN25"), "VY": ("Canada", "FP53"),
    "VO": ("Canada", "GN37"), "CY": ("Canada", "GN03"),
    "K": ("United States", "EM48"), "W": ("United States", "EM48"),
    "N": ("United States", "EM48"), "AA": ("United States", "EM48"),
    "KL": ("Alaska", "BP51"), "KH6": ("Hawaii", "BL11"),
    "G": ("England", "IO91"), "M": ("England", "IO91"), "GM": ("Scotland", "IO85"),
    "GW": ("Wales", "IO81"), "GI": ("Northern Ireland", "IO74"),
    "EI": ("Ireland", "IO63"), "F": ("France", "JN18"), "DL": ("Germany", "JO62"),
    "PA": ("Netherlands", "JO22"), "ON": ("Belgium", "JO20"),
    "OZ": ("Denmark", "JO65"), "SM": ("Sweden", "JO89"), "LA": ("Norway", "JO59"),
    "OH": ("Finland", "KP20"), "ES": ("Estonia", "KO29"), "YL": ("Latvia", "KO26"),
    "LY": ("Lithuania", "KO24"), "SP": ("Poland", "KO02"), "OK": ("Czechia", "JO70"),
    "OM": ("Slovakia", "JN88"), "HA": ("Hungary", "JN97"), "YO": ("Romania", "KN34"),
    "LZ": ("Bulgaria", "KN12"), "SV": ("Greece", "KM18"), "TA": ("Turkey", "KM69"),
    "I": ("Italy", "JN61"), "EA": ("Spain", "IN80"), "CT": ("Portugal", "IM58"),
    "HB": ("Switzerland", "JN47"), "OE": ("Austria", "JN88"), "S5": ("Slovenia", "JN76"),
    "9A": ("Croatia", "JN85"), "UA": ("Russia", "KO85"), "UR": ("Ukraine", "KO50"),
    "JA": ("Japan", "PM95"), "HL": ("South Korea", "PM37"), "BY": ("China", "OM89"),
    "BV": ("Taiwan", "PL05"), "VU": ("India", "MK88"), "9M": ("Malaysia", "OJ03"),
    "HS": ("Thailand", "OK03"), "YB": ("Indonesia", "OI33"), "DU": ("Philippines", "PK04"),
    "VK": ("Australia", "QF56"), "ZL": ("New Zealand", "RE68"),
    "PY": ("Brazil", "GG66"), "LU": ("Argentina", "GF05"), "CE": ("Chile", "FF46"),
    "XE": ("Mexico", "EK09"), "CO": ("Cuba", "EL82"), "ZS": ("South Africa", "KG33"),
    "5Z": ("Kenya", "KI88"), "4X": ("Israel", "KM72"), "A7": ("Qatar", "LL55"),
    "JY": ("Jordan", "KM71"), "TF": ("Iceland", "HP94"), "OY": ("Faroe Islands", "IP62"),
}


class Lookup:
    def __init__(self, provider: str = "callook", user: str = "",
                 password: str = "", cache_path=None, cache_days: int = 30) -> None:
        self.provider = provider
        self.user = user
        self.password = password
        self.cache_path = cache_path
        self.ttl = cache_days * 86400
        self._session_id: str | None = None
        self._session_at = 0.0
        if cache_path:
            self._db().close()

    # ------------------------------------------------------------------ cache
    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.cache_path)
        db.execute("""CREATE TABLE IF NOT EXISTS lookup_cache
                      (call TEXT PRIMARY KEY, data TEXT, fetched REAL)""")
        db.commit()
        return db

    def _cached(self, call: str) -> dict | None:
        if not self.cache_path:
            return None
        db = self._db()
        row = db.execute("SELECT data, fetched FROM lookup_cache WHERE call=?",
                         (call,)).fetchone()
        db.close()
        if row and time.time() - row[1] < self.ttl:
            out = json.loads(row[0])
            out["cached"] = True
            return out
        return None

    def _store(self, call: str, data: dict) -> None:
        if not self.cache_path:
            return
        db = self._db()
        db.execute("INSERT OR REPLACE INTO lookup_cache VALUES (?,?,?)",
                   (call, json.dumps(data), time.time()))
        db.commit()
        db.close()

    # -------------------------------------------------------------- providers
    def _callook(self, call: str) -> dict | None:
        r = requests.get(f"https://callook.info/{call}/json", timeout=8)
        r.raise_for_status()
        d = r.json()
        if d.get("status") != "VALID":
            return None
        loc = d.get("location") or {}
        addr = d.get("address") or {}
        cur = d.get("current") or {}
        return {
            "ok": True, "callsign": cur.get("callsign", call), "name": d.get("name", ""),
            "qth": addr.get("line2", ""), "country": "United States",
            "locator": (loc.get("gridsquare") or "").upper(),
            "lat": _f(loc.get("latitude")), "lon": _f(loc.get("longitude")),
            "license_class": cur.get("operClass", ""), "source": "callook",
            "approximate": False,
        }

    def _hamqth(self, call: str) -> dict | None:
        if not (self.user and self.password):
            return None
        if not self._session_id or time.time() - self._session_at > 3000:
            r = requests.get("https://www.hamqth.com/xml.php",
                             params={"u": self.user, "p": self.password}, timeout=8)
            root = ET.fromstring(r.text)
            sid = root.find(f".//{HAMQTH_NS}session_id")
            if sid is None or not sid.text:
                return None
            self._session_id, self._session_at = sid.text, time.time()
        r = requests.get("https://www.hamqth.com/xml.php",
                         params={"id": self._session_id, "callsign": call,
                                 "prg": "hamdash"}, timeout=8)
        root = ET.fromstring(r.text)
        search = root.find(f".//{HAMQTH_NS}search")
        if search is None:
            self._session_id = None            # expired session: next call re-auths
            return None
        g = {child.tag.replace(HAMQTH_NS, ""): (child.text or "") for child in search}
        return {
            "ok": True, "callsign": g.get("callsign", call).upper(),
            "name": g.get("nick") or g.get("adr_name", ""),
            "qth": g.get("qth", ""), "country": g.get("country", ""),
            "locator": (g.get("grid") or "").upper(),
            "lat": _f(g.get("latitude")), "lon": _f(g.get("longitude")),
            "itu": g.get("itu", ""), "cq": g.get("cq", ""),
            "source": "hamqth", "approximate": False,
        }

    @staticmethod
    def _prefix(call: str) -> dict:
        c = call.upper()
        for n in (3, 2, 1):
            hit = PREFIX.get(c[:n])
            if hit:
                country, locator = hit
                return {"ok": True, "callsign": c, "country": country,
                        "locator": locator, "name": "", "qth": "",
                        "source": "prefix", "approximate": True}
        return {"ok": False, "callsign": c, "country": "", "locator": "",
                "source": "unknown", "approximate": True,
                "error": f"no provider or prefix match for {c}"}

    # ------------------------------------------------------------------ public
    def lookup(self, call: str, refresh: bool = False) -> dict:
        call = (call or "").strip().upper()
        if not call:
            return {"ok": False, "error": "no callsign given"}
        if not refresh:
            hit = self._cached(call)
            if hit:
                return hit

        result, note = None, None
        try:
            if self.provider == "callook":
                result = self._callook(call)
            elif self.provider == "hamqth":
                result = self._hamqth(call)
        except requests.RequestException as exc:
            note = f"{self.provider} unreachable ({exc}); using prefix table"
        except ET.ParseError as exc:
            note = f"{self.provider} returned unparseable XML ({exc})"

        if result is None:
            result = self._prefix(call)
            if note:
                result["note"] = note
            elif self.provider != "offline":
                result["note"] = f"{self.provider} had no record for {call}"

        result["cached"] = False
        self._store(call, result)
        return result


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
