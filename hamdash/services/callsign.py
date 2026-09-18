"""Callsign lookup: provider in front, SQLite cache behind, prefix table as
the floor so the map card still has something to draw when you are offline.

Providers:
  auto     the free ones first, then whatever has credentials  (default)
  callook  free, no account, US callsigns only
  hamdb    free, no account, US and Canadian callsigns
  hamqth   free account needed (session id, re-auth on expiry)
  qrz      QRZ.com XML interface - account plus an XML subscription
  offline  prefix table only

Nothing here needs to be paid for to be useful: callook and hamdb want no
account at all, and HamQTH only a free registration. QRZ is the one that
charges, and only for its XML interface.

A provider that refuses for a reason the operator can act on (bad password, no
subscription) raises Refused, and that text reaches the UI. Falling back to the
prefix table silently would turn "your QRZ subscription lapsed" into a station
sitting in the middle of its own country, which is worse than an error.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET

import requests

HAMQTH_NS = "{https://www.hamqth.com}"
QRZ_NS = "{http://xmldata.qrz.com}"


class Refused(Exception):
    """The provider answered, and the answer was no. Says why."""


# QRZ locks an account out after a few rejected logins and says until when.
# Reading that back means we wait exactly that long instead of guessing, and
# never spend an attempt inside a window it has already told us is closed.
BLOCKED_UNTIL = re.compile(
    r"blocked until\s+(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})",
    re.I)
# How long a rejected login is believed when the provider does not say. Long
# enough that a wrong password costs one attempt rather than one per keystroke,
# short enough that fixing it is not punished for the rest of the afternoon.
AUTH_COOLDOWN = 900.0
# How long "the provider has no record of this callsign" is believed. It is a
# real answer, so re-asking on every keystroke is rude, but it is the kind of
# answer that changes - a new licensee, or a record that had not propagated -
# so it must not sit in the cache for the month a real hit gets.
MISS_TTL = 3600.0


def _blocked_until(message: str) -> float:
    """The unix time a provider said it stays blocked until, or 0."""
    m = BLOCKED_UNTIL.search(message or "")
    if not m:
        return 0.0
    try:
        when = dt.datetime(*(int(x) for x in m.groups()), tzinfo=dt.timezone.utc)
    except ValueError:
        return 0.0
    return when.timestamp()

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
                 password: str = "", cache_path=None, cache_days: int = 30,
                 account: str = "") -> None:
        self.provider = provider
        self.user = user
        self.password = password
        # Whose account the username and password are. Naming a provider
        # outright is itself the answer; `auto` has to be told, because the
        # credentials for one service are useless and unwelcome at another.
        self.account = account if provider == "auto" else provider
        self.cache_path = cache_path
        self.ttl = cache_days * 86400
        # Per provider, because hamqth and qrz issue different session ids and
        # handing one service the other's key logs you out of both.
        self._session: dict[str, tuple[str, float]] = {}
        # A login that was rejected, and until when to believe it. Without
        # this every lookup re-attempts the same bad password: type six
        # characters of a callsign and you have spent several failed logins,
        # which is how a wrong password turns into a locked account.
        self._refused: dict[str, tuple[float, str]] = {}
        # One login at a time. Several waitress threads can miss the cache at
        # once, and each would otherwise open a session of its own.
        self._auth = threading.Lock()
        if cache_path:
            self._db().close()
            self._repair()

    # ---------------------------------------------------------------- session
    def _key(self, name: str) -> str | None:
        """The live session key for `name`, or None if it needs a new one."""
        got = self._session.get(name)
        return got[0] if got and time.time() - got[1] < 3000 else None

    def _check_block(self, name: str) -> None:
        """Refuse without touching the network if the last login was rejected."""
        got = self._refused.get(name)
        if not got:
            return
        until, msg = got
        if time.time() >= until:
            self._refused.pop(name, None)
            return
        raise Refused(msg)

    def _reject(self, name: str, msg: str) -> None:
        """Remember that a login was refused, and for how long. Always raises."""
        self._refused[name] = (_blocked_until(msg) or time.time() + AUTH_COOLDOWN,
                               msg)
        raise Refused(msg)

    def retry_auth(self) -> None:
        """Forget rejected logins, so an explicit test tries again right now."""
        self._refused.clear()

    # ------------------------------------------------------------------ cache
    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.cache_path)
        db.execute("""CREATE TABLE IF NOT EXISTS lookup_cache
                      (call TEXT PRIMARY KEY, data TEXT, fetched REAL)""")
        db.commit()
        return db

    def _repair(self) -> None:
        """Drop rows written before a failure stopped counting as an answer.

        Left alone they keep reporting a provider outage that ended weeks ago,
        against whichever callsigns happened to be typed during it. A cached
        miss says so with `miss`; anything else carrying a note predates the
        distinction and is cheap to look up again.
        """
        db = self._db()
        db.execute("DELETE FROM lookup_cache "
                   "WHERE data LIKE '%\"note\":%' AND data NOT LIKE '%\"miss\": true%'")
        db.commit()
        db.close()

    def _ttl_for(self, data: dict) -> float:
        """How long this answer is good for."""
        return MISS_TTL if data.get("source") == "prefix" else self.ttl

    def _cached(self, call: str) -> dict | None:
        if not self.cache_path:
            return None
        db = self._db()
        row = db.execute("SELECT data, fetched FROM lookup_cache WHERE call=?",
                         (call,)).fetchone()
        db.close()
        if not row:
            return None
        out = json.loads(row[0])
        if time.time() - row[1] >= self._ttl_for(out):
            return None
        out["cached"] = True
        return out

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

    def _hamdb(self, call: str) -> dict | None:
        """hamdb.org - free, no account needed.

        Worth having beside callook rather than instead of it: it answers for
        Canadian callsigns as well as US ones (VE7CC resolves here and not at
        callook), and the two rate limit separately.
        """
        r = requests.get(f"https://api.hamdb.org/v1/{call}/json/hamdash",
                         timeout=8)
        r.raise_for_status()
        d = (r.json() or {}).get("hamdb", {})
        c = d.get("callsign") or {}
        if (d.get("messages", {}).get("status") or "").upper() != "OK":
            return None
        if not c.get("call"):
            return None
        name = " ".join(x for x in (c.get("fname", ""), c.get("name", "")) if x)
        return {
            "ok": True, "callsign": c["call"].upper(), "name": name.strip(),
            "qth": ", ".join(x for x in (c.get("addr2", ""),
                                         c.get("state", "")) if x),
            "country": c.get("country", "United States"),
            "locator": (c.get("grid") or "").upper(),
            "lat": _f(c.get("lat")), "lon": _f(c.get("lon")),
            "state": c.get("state", ""), "grid_src": "hamdb",
            "license_class": c.get("class", ""), "source": "hamdb",
            "approximate": False,
        }

    def _qrz(self, call: str) -> dict | None:
        """QRZ.com XML interface.

        Needs a QRZ account *and* an XML subscription; without one QRZ answers
        the session request with an error rather than a key, and that text is
        passed straight through because it is the only thing that tells the
        operator what to buy or fix.
        """
        if not (self.user and self.password):
            raise Refused("QRZ needs a username and password "
                          "(Settings -> Integrations -> callsign lookup)")
        self._check_block("qrz")
        sid = self._key("qrz")
        if not sid:
            with self._auth:
                sid = self._key("qrz")          # another thread may have won
                if not sid:
                    self._check_block("qrz")
                    r = requests.get("https://xmldata.qrz.com/xml/current/",
                                     params={"username": self.user,
                                             "password": self.password,
                                             "agent": "hamdash"}, timeout=8)
                    root = ET.fromstring(r.text)
                    err = root.find(f".//{QRZ_NS}Error")
                    key = root.find(f".//{QRZ_NS}Key")
                    if key is None or not key.text:
                        self._reject("qrz", (err.text or "").strip()
                                     if err is not None and err.text
                                     else "QRZ did not return a session key")
                    sid = key.text
                    self._session["qrz"] = (sid, time.time())

        r = requests.get("https://xmldata.qrz.com/xml/current/",
                         params={"s": sid, "callsign": call},
                         timeout=8)
        root = ET.fromstring(r.text)
        err = root.find(f".//{QRZ_NS}Error")
        node = root.find(f".//{QRZ_NS}Callsign")
        if node is None:
            text = (err.text or "").strip() if err is not None and err.text else ""
            if "session" in text.lower() or "timeout" in text.lower():
                self._session.pop("qrz", None)  # expired: next call re-auths
                return None
            if text and "not found" not in text.lower():
                raise Refused(f"QRZ: {text}")
            return None
        g = {c.tag.replace(QRZ_NS, ""): (c.text or "") for c in node}
        name = " ".join(x for x in (g.get("fname", ""), g.get("name", "")) if x)
        return {
            "ok": True, "callsign": (g.get("call") or call).upper(),
            "name": name.strip(), "qth": g.get("addr2", ""),
            "country": g.get("country", ""),
            "locator": (g.get("grid") or "").upper(),
            "lat": _f(g.get("lat")), "lon": _f(g.get("lon")),
            "state": g.get("state", ""),
            "county": g.get("county", ""), "license_class": g.get("class", ""),
            "source": "qrz", "approximate": False,
        }

    def _hamqth(self, call: str) -> dict | None:
        if not (self.user and self.password):
            return None
        self._check_block("hamqth")
        sid = self._key("hamqth")
        if not sid:
            with self._auth:
                sid = self._key("hamqth")
                if not sid:
                    self._check_block("hamqth")
                    r = requests.get(
                        "https://www.hamqth.com/xml.php",
                        params={"u": self.user, "p": self.password}, timeout=8)
                    root = ET.fromstring(r.text)
                    node = root.find(f".//{HAMQTH_NS}session_id")
                    if node is None or not node.text:
                        # HamQTH blocks an account that keeps failing to log
                        # in, so a silent retry here is the expensive kind.
                        err = root.find(f".//{HAMQTH_NS}error")
                        self._reject("hamqth", (err.text or "").strip()
                                     if err is not None and err.text
                                     else "HamQTH refused the login")
                    sid = node.text
                    self._session["hamqth"] = (sid, time.time())
        r = requests.get("https://www.hamqth.com/xml.php",
                         params={"id": sid, "callsign": call,
                                 "prg": "hamdash"}, timeout=8)
        root = ET.fromstring(r.text)
        search = root.find(f".//{HAMQTH_NS}search")
        if search is None:
            self._session.pop("hamqth", None)   # expired: next call re-auths
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

    def _chain(self) -> list[str]:
        """Which providers to try, in order.

        `auto` means: the ones that cost nothing and need no account first,
        then whichever account provider has credentials. So a fresh install
        looks things up properly without anyone signing up for anything.
        """
        if self.provider != "auto":
            return [self.provider]
        chain = ["callook", "hamdb"]
        # Only reach for the account provider the credentials were entered
        # for. Guessing would post a QRZ password to HamQTH, which is both a
        # credential handed to the wrong company and a failed login on an
        # account that locks after a few of those.
        if self.account in ("qrz", "hamqth") and self.user and self.password:
            chain.append(self.account)
        return chain

    def _run(self, call: str):
        """Try the chain; return (result, note). The note is what to tell the
        operator when nothing answered."""
        methods = {"callook": self._callook, "hamdb": self._hamdb,
                   "hamqth": self._hamqth, "qrz": self._qrz}
        note, thin = None, None
        for name in self._chain():
            fn = methods.get(name)
            if fn is None:                      # "offline", or a typo
                continue
            try:
                hit = fn(call)
            except Refused as exc:
                note = str(exc)                 # actionable: stop being quiet
                continue
            except requests.RequestException as exc:
                note = note or f"{name} unreachable ({exc})"
                continue
            except (ET.ParseError, ValueError) as exc:
                note = note or f"{name} returned something unreadable ({exc})"
                continue
            if hit and hit.get("locator"):
                return hit, None
            if hit and thin is None:
                # Answered, but with no grid - callook does this for some US
                # calls. It is a real answer (name, country) and worth keeping,
                # but it cannot place anyone on a map, so try the next provider
                # before settling for it.
                thin = hit
        if thin:
            return thin, None
        return None, note

    # ------------------------------------------------------------------ public
    def lookup(self, call: str, refresh: bool = False) -> dict:
        call = (call or "").strip().upper()
        if not call:
            return {"ok": False, "error": "no callsign given"}
        if not refresh:
            hit = self._cached(call)
            if hit:
                return hit

        result, note = self._run(call)

        failed = False
        if result is None:
            result = self._prefix(call)
            if note:
                # The provider refused, was unreachable or is blocked.
                failed = True
                result["note"] = note
            elif self.provider != "offline":
                # It answered, and the answer was that it has no such record.
                tried = ", ".join(self._chain())
                result["miss"] = True
                result["note"] = f"no record for {call} from {tried}"

        result["cached"] = False
        result["links"] = links_for(result.get("callsign") or call)
        # A provider refusing is a fact about us, not about the callsign.
        # Storing it used to pin the failure to that callsign for a month, so
        # the callsign went on reporting an outage that had ended - and the
        # operator, quite reasonably, blamed the callsign. "No such record" is
        # a real answer and is cached, but only briefly (MISS_TTL).
        if not failed:
            self._store(call, result)
        return result


def links_for(call: str) -> dict:
    """Public pages for a callsign, for the UI to offer.

    LoTW is deliberately absent: it has no public per-callsign page, only a
    login and a public list of who has uploaded recently. Linking to
    lotw.arrl.org for a specific callsign would be a link that goes nowhere
    useful.
    """
    c = (call or "").strip().upper()
    if not c:
        return {}
    return {"qrz": f"https://www.qrz.com/db/{c}",
            "hamqth": f"https://www.hamqth.com/{c}",
            "callook": f"https://callook.info/{c}"}


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
