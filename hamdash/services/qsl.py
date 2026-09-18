"""Pushing and pulling QSOs: LoTW and the QRZ logbook.

What each service will actually let you do differs, and the difference is not
a detail:

**LoTW pull** needs nothing but your LoTW username and password. It hands back
ADIF, including the confirmations, which is the useful half.

**LoTW push cannot be done with a username and password at all.** ARRL only
accepts QSOs inside a `.tq8` signed with your station certificate, and the
only thing that can make one is TQSL, which holds the private key. So there is
no API to write here: either TQSL is installed and we hand it an ADIF file to
sign and upload, or we write the ADIF out and say what to do with it. Anything
that claimed to "upload to LoTW" with a password would be lying.

**QRZ** is a plain HTTP API in both directions, keyed on a logbook API key
from the logbook's settings page. The key is per logbook, not per account, and
QRZ gates the API behind a subscription.

Nothing here is on the request path for anything else: a sync is something the
operator asks for, it takes as long as it takes, and it must never be started
from inside a request handler that is holding a server thread open.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

import requests

# QRZ requires an identifying User-Agent and names python-requests as the
# kind it rate limits.
try:
    from .. import __version__ as _v
except Exception:                                       # noqa: BLE001
    _v = "0"
USER_AGENT = f"hamdash/{_v} (+https://github.com/wizzardsl/hamdash)"

_LOGID = re.compile(r"<app_qrzlog_logid:\d+>(\d+)", re.I)
_EOR = re.compile(r"<eor>", re.I)


def _pairs(text: str) -> dict:
    """QRZ nests name=value lists inside a field, & separated."""
    out = {}
    for part in (text or "").replace(";", "&").split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().upper()] = v.strip()
    return out


LOTW_REPORT = "https://lotw.arrl.org/lotwuser/lotwreport.adi"
QRZ_API = "https://logbook.qrz.com/api"
TIMEOUT = 60          # a year of QSOs is a slow download, not a hung one


class SyncError(Exception):
    """Something the operator can act on. The message is shown as-is."""


def _looks_like_adif(text: str) -> bool:
    low = text[:4000].lower()
    return "<eoh>" in low or "<call:" in low


class Lotw:
    """ARRL Logbook of the World."""

    name = "lotw"

    def __init__(self, user: str = "", password: str = "",
                 tqsl_path: str = "", station_location: str = "") -> None:
        self.user = (user or "").strip()
        self.password = password or ""
        self.tqsl_path = (tqsl_path or "").strip()
        self.station_location = (station_location or "").strip()

    # ------------------------------------------------------------------ pull
    def pull(self, since: str = "", owncall: str = "",
             confirmed_only: bool = False) -> str:
        """Your QSOs from LoTW as ADIF.

        `since` is an ADIF date (YYYYMMDD) and is what makes this cheap to run
        often - without it LoTW sends everything you have ever uploaded.
        """
        if not (self.user and self.password):
            raise SyncError("LoTW needs your username and password "
                            "(Settings -> logbook sync)")
        params = {
            "login": self.user, "password": self.password,
            "qso_query": "1",
            "qso_qsl": "yes" if confirmed_only else "no",
            "qso_qsldetail": "yes",
            "qso_mydetail": "yes",
        }
        if since:
            # LoTW wants yyyy-mm-dd here even though ADIF is yyyymmdd.
            params["qso_qsorxsince" if confirmed_only else "qso_startdate"] = (
                f"{since[:4]}-{since[4:6]}-{since[6:8]}" if len(since) == 8
                else since)
        if owncall:
            params["qso_owncall"] = owncall
        try:
            r = requests.get(LOTW_REPORT, params=params, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise SyncError(f"could not reach LoTW: {exc}") from exc
        if r.status_code != 200:
            raise SyncError(f"LoTW answered {r.status_code}")
        if not _looks_like_adif(r.text):
            # A bad login gets an HTML page, not an error code.
            snippet = " ".join(r.text.split())[:200]
            raise SyncError(
                f"LoTW did not return a log. It usually means the username or "
                f"password is wrong. It said: {snippet}")
        return r.text

    def test(self) -> dict:
        """Read-only: are these credentials good?

        Asks for one day of a year that cannot have QSOs in it, so the answer
        is about the login and nothing is downloaded.
        """
        self.pull(since="20991231")
        return {"ok": True, "message": "LoTW accepted the login",
                "can_push": bool(self.tqsl()), "tqsl": self.tqsl() or ""}

    # ------------------------------------------------------------------ push
    def tqsl(self) -> str | None:
        """Where TQSL is, if it is anywhere."""
        if self.tqsl_path and Path(self.tqsl_path).exists():
            return self.tqsl_path
        found = shutil.which("tqsl")
        if found:
            return found
        for guess in (
            r"C:\Program Files (x86)\TrustedQSL\tqsl.exe",
            r"C:\Program Files\TrustedQSL\tqsl.exe",
            "/Applications/TrustedQSL/tqsl.app/Contents/MacOS/tqsl",
            "/usr/bin/tqsl", "/usr/local/bin/tqsl",
        ):
            if Path(guess).exists():
                return guess
        return None

    def push(self, adif_text: str) -> dict:
        """Sign and upload through TQSL.

        There is no way round TQSL: the certificate that signs a LoTW upload
        lives in it and does not leave it. If it isn't installed this raises
        with what to do, rather than pretending a password would have done.
        """
        exe = self.tqsl()
        if not exe:
            raise SyncError(
                "LoTW uploads have to be signed by TQSL, which is not "
                "installed here. Install TrustedQSL from arrl.org/tqsl and "
                "set up your station location, or use Export ADIF and drop "
                "the file on TQSL yourself.")
        tmp = Path(tempfile.mkdtemp(prefix="hamdash-lotw-")) / "upload.adi"
        tmp.write_text(adif_text, encoding="utf-8")
        cmd = [exe, "-x", "-u", "-a", "compliant", "-d"]
        if self.station_location:
            cmd += ["-l", self.station_location]
        cmd.append(str(tmp))
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=300, encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SyncError(f"could not run TQSL ({exc})") from exc
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        if p.returncode != 0:
            raise SyncError(
                f"TQSL refused the upload (exit {p.returncode}). It said: "
                f"{' '.join(out.split())[:400] or '(nothing)'}")
        return {"ok": True, "output": out[:2000], "tqsl": exe}


class QrzLogbook:
    """QRZ.com logbook API.

    Written against QRZ's published guide. Three things in it are easy to get
    wrong and all three were:

    * **A real User-Agent is mandatory.** QRZ names `python-requests` as the
      sort of generic agent that gets rate limited, so every call identifies
      the app and version.
    * **STATUS answers in `DATA`**, a nested `&`-separated list of name=value
      pairs, not as top-level fields.
    * **FETCH pages.** QRZ warns that fetching a large book in one go times
      out, and asks for `MAX:n,AFTERLOGID:n` walked by the
      `app_qrzlog_logid` of the last record seen.

    DELETE is deliberately not implemented. It is permanent and QRZ says so
    twice; nothing in hamdash should be able to remove somebody's QSOs.
    """

    name = "qrz"
    PAGE = 250              # what QRZ's own paging advice suggests

    def __init__(self, api_key: str = "") -> None:
        self.api_key = (api_key or "").strip()

    last_body: str = ""          # raw text of the last response, for diagnosis

    def _call(self, action: str, **extra) -> dict:
        if not self.api_key:
            raise SyncError("QRZ logbook needs its API key (Settings -> "
                            "logbook sync). Find it on the logbook's own "
                            "settings page at logbook.qrz.com, not your "
                            "account password.")
        data = {"KEY": self.api_key, "ACTION": action, **extra}
        try:
            r = requests.post(QRZ_API, data=data, timeout=TIMEOUT,
                              headers={"User-Agent": USER_AGENT})
        except requests.RequestException as exc:
            raise SyncError(f"could not reach QRZ: {exc}") from exc
        if r.status_code != 200:
            raise SyncError(f"QRZ answered {r.status_code}")
        # Response is url-encoded key=value pairs joined by &.
        self.last_body = r.text
        out = {}
        for part in r.text.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip().upper()] = urllib.parse.unquote_plus(v)
        if out.get("RESULT") == "AUTH":
            raise SyncError("QRZ rejected the API key. QRZ's logbook API "
                            "needs a subscription as well as a key.")
        if out.get("RESULT") == "FAIL":
            raise SyncError(f"QRZ: {out.get('REASON') or 'refused, no reason given'}")
        return out

    def status(self) -> dict:
        d = self._call("STATUS")
        # The interesting values arrive nested in DATA as their own
        # &-separated name=value list, not as top-level fields.
        inner = _pairs(d.get("DATA", ""))
        get = lambda *names: next(                       # noqa: E731
            (inner.get(n) or d.get(n) for n in names
             if inner.get(n) or d.get(n)), "")
        return {"ok": True,
                "book_name": get("BOOKNAME", "NAME"),
                "count": get("COUNT", "QSOCOUNT"),
                "confirmed": get("CONFIRMED"),
                "owner": get("OWNER", "CALLSIGN"),
                "bookid": get("BOOKID"),
                "start": get("START_DATE", "STARTDATE"),
                "end": get("END_DATE", "ENDDATE")}

    def test(self) -> dict:
        s = self.status()
        return {"ok": True, "message": (f"{s['book_name'] or 'logbook'}: "
                                        f"{s['count']} QSOs on QRZ"), **s}

    def pull(self, max_pages: int = 200) -> str:
        """The whole logbook as ADIF, a page at a time.

        QRZ asks for this: a big book fetched in one request times out. Walk
        `AFTERLOGID` by the highest `app_qrzlog_logid` seen so far until a
        short page arrives.
        """
        chunks, after, seen = [], 0, 0
        for _ in range(max_pages):
            d = self._call("FETCH",
                           OPTION=f"MAX:{self.PAGE},AFTERLOGID:{after}")
            adif = d.get("ADIF", "")
            if not adif:
                if seen or str(d.get("COUNT", "")).strip() in ("0", ""):
                    break                    # nothing more, or nothing at all
                keys = ", ".join(sorted(d)) or "nothing parseable"
                snippet = " ".join(self.last_body.split())[:300]
                raise SyncError(
                    f"QRZ replied to FETCH without an ADIF field. It sent: "
                    f"{keys}. Raw: {snippet}")
            chunks.append(adif)
            ids = [int(x) for x in _LOGID.findall(adif) if x.isdigit()]
            got = len(_EOR.findall(adif))
            seen += got
            if got < self.PAGE or not ids:
                break
            after = max(ids) + 1
        if not chunks:
            raise SyncError("QRZ's logbook for this key is empty. The key is "
                            "per logbook, so check it is the one holding your "
                            "contacts.")
        return "\n".join(chunks)

    def push_record(self, adif_record: str, replace: bool = False) -> dict:
        kw = {"ADIF": adif_record}
        if replace:
            kw["OPTION"] = "REPLACE"
        d = self._call("INSERT", **kw)
        return {"ok": True, "logid": d.get("LOGID", ""),
                "count": d.get("COUNT", "")}


class Runner:
    """Runs one sync at a time, off the request thread.

    A LoTW pull is a minute of waiting on someone else's server. Doing that
    inside a request handler would hold one of the server's threads for the
    duration (hamdash invariant 4), so the endpoint starts a job and returns,
    and progress arrives on the bus as `log.sync`.

    One at a time on purpose: two pulls racing each other into the same
    logbook is not a thing anyone wants, and serialising is simpler than
    making the importer safe against itself.
    """

    def __init__(self, publish) -> None:
        self._publish = publish
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.state: dict = {"running": False, "job": "", "at": 0.0,
                            "ok": None, "message": "", "result": {}}

    def busy(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, job: str, fn) -> dict:
        with self._lock:
            if self.busy():
                raise SyncError(f"{self.state['job']} is still running")
            self.state = {"running": True, "job": job, "at": time.time(),
                          "ok": None, "message": "working…", "result": {}}
            started = dict(self.state)
            self._thread = threading.Thread(
                target=self._run, args=(job, fn), daemon=True,
                name=f"sync-{job}")
            # Announce the start before the thread can finish, or a fast job
            # emits its result twice and never says it began.
            self._emit()
            self._thread.start()
        return started

    def _run(self, job: str, fn) -> None:
        try:
            result = fn() or {}
            self.state.update(running=False, ok=True, result=result,
                              message=result.get("message", "done"))
        except SyncError as exc:
            self.state.update(running=False, ok=False, message=str(exc))
        except Exception as exc:                        # noqa: BLE001
            # A sync talks to someone else's server and parses what comes
            # back; an unexpected shape must not take the host down with it.
            self.state.update(running=False, ok=False,
                              message=f"{job} failed unexpectedly: {exc}")
        self.state["at"] = time.time()
        self._emit()

    def _emit(self) -> None:
        try:
            self._publish("log.sync", dict(self.state))
        except Exception:                               # noqa: BLE001
            pass
