"""Logs that look after themselves, a debug trace, and the problem report.

Three pieces, all here so they agree with each other about file names:

* **The main log rolls over by size** (`MAIN_BYTES`, `MAIN_KEEP` previous
  files). It grows by about 20 kB a fortnight on a healthy board, so one
  file holds months; a board in trouble logs far more, and that is when
  the recent history matters most.
* **Debug logging writes its own daily files**, kept `DEBUG_KEEP_S` and
  capped at `DEBUG_CAP` bytes between them, so it can be left on for as
  long as somebody likes without filling a disk. What it adds is the
  conversation with the radio (`trace`), which the main log never had -
  and which is what fixing "my radio does not work" on a radio nobody here
  owns comes down to.
* **The report** is a zip of the last `REPORT_WINDOW_S` of both logs plus
  whatever the caller knows about the app, with every stored secret
  scrubbed out of all of it. It is written to disk and nothing is sent:
  the operator reads it and attaches it to a GitHub issue themselves.
"""
from __future__ import annotations

import io
import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

MAIN_NAME = "hamdash.log"
MAIN_BYTES = 1_000_000
MAIN_KEEP = 5

DEBUG_PREFIX = "hamdash-debug-"
DEBUG_KEEP_S = 48 * 3600
DEBUG_CAP = 100_000_000

REPORT_DIR = "reports"
REPORT_NAME = re.compile(r"hamdash-report-\d{8}-\d{6}\.zip")
ISSUES_URL = "https://github.com/wizzardsl/hamdash/issues"
REPORT_WINDOW_S = 48 * 3600
REPORT_KEEP = 5
# Text before compression. Logs compress about ten to one, and a GitHub
# attachment may be 25 MB, so this keeps a report attachable however much
# a board has been logging. The newest lines are the ones kept.
REPORT_TEXT_CAP = 40_000_000

# A poll answer that has not changed is written once, then noted again
# every this many repeats - at two polls a second, every five minutes, so
# the log still shows the poller was alive.
REPEAT_NOTE = 600

TRACE = logging.getLogger("hamdash.trace")
log = logging.getLogger("hamdash.diagnostics")


# ---- the main log ---------------------------------------------------------
class SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that does not lose records when it cannot rotate.

    Rolling over renames files, and on Windows a rename fails while
    anything else has the file open - `Get-Content -Wait`, a second
    hamdash, a log viewer. The stock handler then drops *every* record:
    each one tries the rollover again, fails, and goes to `handleError`.
    Here a failed rollover goes on writing to the file it has and tries
    again `RETRY_S` later.
    """

    RETRY_S = 60.0

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._blocked_until = 0.0

    def shouldRollover(self, record) -> bool:      # noqa: N802 - stdlib name
        if time.monotonic() < self._blocked_until:
            return False
        return super().shouldRollover(record)

    def doRollover(self) -> None:                   # noqa: N802 - stdlib name
        try:
            super().doRollover()
        except OSError as exc:
            self._blocked_until = time.monotonic() + self.RETRY_S
            if self.stream is None:
                self.stream = self._open()
            self.stream.write(
                f"--- could not roll the log over ({exc}); still writing "
                f"here and trying again in {int(self.RETRY_S)} s. Something "
                f"else has this file open. ---\n")


def main_handler(home: Path, formatter: logging.Formatter) -> logging.Handler:
    handler = SafeRotatingFileHandler(
        Path(home) / MAIN_NAME, maxBytes=MAIN_BYTES, backupCount=MAIN_KEEP,
        encoding="utf-8", errors="replace")
    handler.setLevel(logging.INFO)
    handler.setFormatter(formatter)
    return handler


# ---- the debug log --------------------------------------------------------
def _utc_day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def debug_files(home: Path) -> list[Path]:
    """Oldest first: the names sort by date."""
    return sorted(Path(home).glob(f"{DEBUG_PREFIX}*.log"))


class DailyDebugHandler(logging.Handler):
    """One file per UTC day, `hamdash-debug-YYYY-MM-DD.log`.

    Named by date rather than renamed at midnight, so the Windows rename
    problem above cannot arise here at all. Files older than `DEBUG_KEEP_S`
    are deleted at each new day and when debug is switched on; if the
    files together reach `DEBUG_CAP`, the oldest go first and, if today's
    alone is that big, writing stops for the day with a line saying so.
    """

    def __init__(self, home: Path, keep_s: float = DEBUG_KEEP_S,
                 cap: int = DEBUG_CAP) -> None:
        super().__init__(logging.DEBUG)
        self.home = Path(home)
        self.keep_s, self.cap = keep_s, cap
        self._day = ""
        self._stream = None
        self._total = 0
        self._capped = False

    def _path(self, day: str) -> Path:
        return self.home / f"{DEBUG_PREFIX}{day}.log"

    def prune(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        today = self._path(self._day) if self._day else None
        for f in debug_files(self.home):
            if f == today:
                continue
            try:
                if now - f.stat().st_mtime > self.keep_s:
                    f.unlink()
            except OSError:
                pass

    def _sizes(self) -> list[tuple[Path, int]]:
        out = []
        for f in debug_files(self.home):
            try:
                out.append((f, f.stat().st_size))
            except OSError:
                pass
        return out

    def _roll(self, day: str) -> None:
        if self._stream:
            self._stream.close()
            self._stream = None
        self._day = day
        self.prune()
        self._stream = open(self._path(day), "a", encoding="utf-8",
                            errors="replace")
        self._total = sum(n for _, n in self._sizes())
        self._capped = False

    def _make_room(self, need: int) -> bool:
        """Drop the oldest earlier days until `need` fits under the cap."""
        today = self._path(self._day)
        for f, n in self._sizes():
            if self._total + need <= self.cap:
                break
            if f == today:
                continue
            try:
                f.unlink()
                self._total -= n
            except OSError:
                pass
        return self._total + need <= self.cap

    def emit(self, record) -> None:
        try:
            day = _utc_day(record.created)
            if day != self._day or self._stream is None:
                self._roll(day)
            if self._capped:
                return
            line = self.format(record) + "\n"
            size = len(line.encode("utf-8", "replace"))
            if self._total + size > self.cap and not self._make_room(size):
                self._capped = True
                note = (f"--- debug log has reached {self.cap // 1_000_000} "
                        f"MB; nothing more is written until "
                        f"{self._day} ends (UTC) ---\n")
                self._stream.write(note)
                self._stream.flush()
                return
            self._stream.write(line)
            self._stream.flush()
            self._total += size
        except Exception:                           # noqa: BLE001 - logging
            self.handleError(record)

    def close(self) -> None:
        with self.lock:
            if self._stream:
                self._stream.close()
                self._stream = None
        super().close()


# ---- switching it on and off ----------------------------------------------
class _State:
    home: Path | None = None
    formatter: logging.Formatter | None = None
    handler: DailyDebugHandler | None = None
    on = False


_state = _State()
_switch = threading.Lock()


def install(home: Path, formatter: logging.Formatter) -> None:
    """Called once by the launcher, after the main log is set up."""
    _state.home = Path(home)
    _state.formatter = formatter


def debug_on() -> bool:
    return _state.on


def set_debug(on: bool) -> None:
    """Start or stop the debug log. Safe to call repeatedly with the same
    answer - every config save calls it."""
    on = bool(on)
    with _switch:
        if on == _state.on:
            return
        root = logging.getLogger()
        app = logging.getLogger("hamdash")
        if on:
            if _state.home is None:
                return                  # not installed: a test, or a card
            handler = DailyDebugHandler(_state.home)
            if _state.formatter:
                handler.setFormatter(_state.formatter)
            root.addHandler(handler)
            app.setLevel(logging.DEBUG)
            _state.handler, _state.on = handler, True
            log.info("debug logging on: %s", handler._path(_utc_day(time.time())))
        else:
            log.info("debug logging off")
            app.setLevel(logging.NOTSET)
            if _state.handler:
                root.removeHandler(_state.handler)
                _state.handler.close()
            _state.handler, _state.on = None, False
            with _trace_lock:
                _last.clear()


# ---- the radio conversation -----------------------------------------------
_last: dict[tuple[str, str], list] = {}
_trace_lock = threading.Lock()


def _short(value, limit: int = 600) -> str:
    text = value if isinstance(value, str) else repr(value)
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return text if len(text) <= limit else f"{text[:limit]}… ({len(text)} chars)"


def trace(source: str, call: str, reply=None, error=None,
          repeat: bool = True) -> None:
    """One exchange with a radio, written to the debug log only.

    `repeat=True` is for reads that are polled: an answer the same as last
    time is counted rather than written, so a quiet radio costs a line
    every few minutes instead of two a second. Writes are never folded.
    Costs one attribute read when debug is off.
    """
    if not _state.on:
        return
    text = f"ERROR {_short(error)}" if error is not None else _short(reply)
    lines = []
    if repeat:
        key = (source, call)
        with _trace_lock:
            prev = _last.get(key)
            if prev and prev[0] == text:
                prev[1] += 1
                if prev[1] % REPEAT_NOTE:
                    return
                lines.append(f"{source} {call} -> {text} "
                             f"(unchanged, {prev[1]} times since "
                             f"{time.strftime('%H:%M:%S', time.localtime(prev[2]))})")
            else:
                if prev and prev[1] > 1:
                    lines.append(f"{source} {call} answered the previous "
                                 f"line {prev[1]} times in all")
                _last[key] = [text, 1, time.time()]
                lines.append(f"{source} {call} -> {text}")
    else:
        lines.append(f"{source} {call} -> {text}")
    for line in lines:
        TRACE.debug("%s", line)


# ---- the report -----------------------------------------------------------
_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
# Secrets that turn up in text without being a stored value we know of:
# a query string or a form body quoted in an error.
_PATTERNS = [
    re.compile(r"(?i)\b(password|passwd|pass|api[_-]?key|key|token|session"
               r"[_-]?key|apikey)(\s*[=:]\s*)([^\s&;,'\"]+)"),
]


def scrub(text: str, secrets) -> str:
    for value in secrets:
        if value and len(value) >= 4:
            text = text.replace(value, "[redacted]")
    for pat in _PATTERNS:
        text = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)
    return text


def recent_lines(path: Path, since: float) -> str:
    """The records in a log written at or after `since`, local time, as the
    main log stamps them. A line with no stamp - a traceback - belongs to
    the record above it."""
    out, keep = [], False
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = _STAMP.match(line)
                if m:
                    try:
                        at = time.mktime(time.strptime(m.group(1),
                                                       "%Y-%m-%d %H:%M:%S"))
                    except (ValueError, OverflowError):
                        at = since
                    keep = at >= since
                if keep:
                    out.append(line)
    except OSError:
        return ""
    return "".join(out)


def main_logs(home: Path) -> list[Path]:
    """Oldest first: hamdash.log.5 ... hamdash.log.1, hamdash.log."""
    home = Path(home)
    olds = [home / f"{MAIN_NAME}.{n}" for n in range(MAIN_KEEP, 0, -1)]
    return [p for p in olds + [home / MAIN_NAME] if p.exists()]


def _tail(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    cut = text[-budget:]
    nl = cut.find("\n")
    return ("--- earlier lines left out to keep the report attachable ---\n"
            + (cut[nl + 1:] if nl >= 0 else cut))


def build_report(home: Path, info: dict, secrets=(), now: float | None = None,
                 window_s: float = REPORT_WINDOW_S) -> bytes:
    """The zip, in memory. `info` is whatever the app knows about itself."""
    home = Path(home)
    now = time.time() if now is None else now
    since = now - window_s
    texts: list[tuple[str, str]] = []
    main = "".join(recent_lines(p, since) for p in main_logs(home))
    texts.append((MAIN_NAME, main))
    for f in debug_files(home):
        try:
            if now - f.stat().st_mtime > window_s + 86400:
                continue
            texts.append((f.name, f.read_text(encoding="utf-8",
                                              errors="replace")))
        except OSError:
            pass
    # Newest files first get the budget, so a busy debug log cannot push
    # out the main log, which goes first of all.
    budget = REPORT_TEXT_CAP
    order = [texts[0]] + list(reversed(texts[1:]))
    kept = {}
    for name, text in order:
        part = _tail(text, max(budget, 0))
        kept[name] = part
        budget -= len(part)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", README.format(
            when=time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(now)),
            hours=int(window_s // 3600)))
        z.writestr("info.json", scrub(json.dumps(info, indent=2, default=str),
                                      secrets))
        for name, _ in texts:
            z.writestr(f"logs/{name}", scrub(kept.get(name, ""), secrets))
    return buf.getvalue()


README = """hamdash problem report, made {when}.

What is in here:
  info.json   versions, operating system, radios, sound devices, cards and
              their recent output. Passwords and API keys are removed.
  logs/       the last {hours} hours of hamdash.log, and the debug logs if
              debug logging was on.

The logs still carry your callsign, your grid square and the callsigns you
have looked up or logged. Read them before you share this if that matters
to you.

Attach this file to an issue at https://github.com/wizzardsl/hamdash/issues
"""


def issue_url(version: str, platform: str, backend: str = "") -> str:
    """A new issue on the bug form, with what we already know filled in.

    GitHub issue forms take a field's starting value from a query parameter
    named after the field's `id` (.github/ISSUE_TEMPLATE/bug.yml) - inputs
    and text boxes, which is why the platform and radio go into the report
    box as words rather than into the dropdowns. A file cannot travel in a
    URL, so the operator drags the zip in themselves.
    """
    from urllib.parse import urlencode
    note = f"hamdash {version} on {platform}"
    if backend:
        note += f", radio through {backend}"
    note += ". Report zip attached below:\n\n"
    q = {"template": "bug.yml", "version": version, "report": note}
    return f"{ISSUES_URL}/new?{urlencode(q)}"


def reveal(folder: Path) -> None:
    """Show a folder in the file manager of the machine this runs on."""
    folder = str(folder)
    if sys.platform.startswith("win"):
        os.startfile(folder)                        # noqa: S606 - a folder
    elif sys.platform == "darwin":
        subprocess.Popen(["open", folder])
    else:
        subprocess.Popen(["xdg-open", folder], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def write_report(home: Path, data: bytes, now: float | None = None) -> Path:
    """Save it under <home>/reports and keep only the newest few."""
    now = time.time() if now is None else now
    folder = Path(home) / REPORT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / time.strftime("hamdash-report-%Y%m%d-%H%M%S.zip",
                                  time.localtime(now))
    path.write_bytes(data)
    for old in sorted(folder.glob("hamdash-report-*.zip"))[:-REPORT_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    return path


# ---- sending a report ---------------------------------------------------------
# For operators without a GitHub account: Send uploads the zip to the
# project's report service (tools/report-service, a Cloudflare Worker),
# which files it as an issue in a private repository and answers with a
# reference to quote. Only ever when the operator presses Send, after the
# sheet has said what goes; nothing here runs by itself.
REPORT_SERVICE = "https://hamdash-reports.wizzardsl.workers.dev/report"
SEND_TIMEOUT = (10, 300)        # connect, then a slow uplink for 25 MB
NOTE_CAP = 4000


class SendError(Exception):
    """The service refused or could not be reached; the text says why."""


def send_report(path: Path, info: dict, note: str = "",
                url: str = REPORT_SERVICE, post=None) -> str:
    """Upload one report zip and return the reference the service gave.

    `info` is {"version", "platform", "radio"}, sent as headers beside the
    zip so the issue's title says what it is; the note is the operator's
    own words. Header values are URL-encoded - a note has newlines and
    accents, and a header may not."""
    from urllib.parse import quote
    if post is None:
        import requests
        post = requests.post
    headers = {"Content-Type": "application/zip",
               "User-Agent": f"hamdash/{info.get('version', '?')}"}
    for key in ("version", "platform", "radio"):
        if info.get(key):
            headers[f"X-Hamdash-{key.title()}"] = quote(str(info[key])[:200])
    if note.strip():
        headers["X-Hamdash-Note"] = quote(note.strip()[:NOTE_CAP])
    try:
        with open(path, "rb") as f:
            r = post(url, data=f, headers=headers, timeout=SEND_TIMEOUT)
    except OSError as exc:
        raise SendError(f"could not reach the report service ({exc})") from exc
    except Exception as exc:                            # noqa: BLE001 - requests' own
        raise SendError(f"could not reach the report service ({exc})") from exc
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or not body.get("ok") or not body.get("reference"):
        raise SendError(body.get("error")
                        or f"the report service answered {r.status_code}")
    return str(body["reference"])


class Sender:
    """One upload at a time, off the request thread (invariant 4): a 25 MB
    report on a slow uplink is minutes, and a waitress thread is scarce.
    The page asks `status()` until the state is no longer "sending"."""

    def __init__(self, send=None) -> None:
        self._send = send       # None: send_report, looked up when it runs
        self._lock = threading.Lock()
        self._state = {"state": "idle", "reference": "", "error": "",
                       "name": ""}

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def start(self, path: Path, info: dict, note: str = "") -> bool:
        with self._lock:
            if self._state["state"] == "sending":
                return False
            self._state = {"state": "sending", "reference": "", "error": "",
                           "name": Path(path).name}
        threading.Thread(target=self._run, args=(path, info, note),
                         daemon=True, name="report-send").start()
        return True

    def _run(self, path: Path, info: dict, note: str) -> None:
        try:
            ref = (self._send or send_report)(path, info, note)
            update = {"state": "sent", "reference": ref}
            logging.getLogger("hamdash").info(
                "problem report %s sent as %s", Path(path).name, ref)
        except SendError as exc:
            update = {"state": "failed", "error": str(exc)}
        except Exception as exc:                        # noqa: BLE001 - said
            update = {"state": "failed", "error": f"{type(exc).__name__}: {exc}"}
        if update["state"] == "failed":
            logging.getLogger("hamdash").warning(
                "problem report %s not sent: %s", Path(path).name, update["error"])
        with self._lock:
            self._state.update(update)
