"""SQLite logbook with ADIF in and out.

ADIF is non-negotiable: anyone with a real log will want it in and out of
LoTW, QRZ or Club Log, and a log you can't export is a log you can't keep.
"""
from __future__ import annotations

import contextlib
import re
import sqlite3
import threading
from datetime import datetime, timezone

from ..bus import BUS
from .bands import for_mhz as band_for

FIELDS = ["call", "qso_date", "time_on", "time_off", "band", "mode", "freq",
          "rst_sent", "rst_rcvd", "gridsquare", "name", "qth", "country",
          "comment", "tx_pwr", "my_callsign", "my_gridsquare",
          # Confirmation state, ADIF-standard names so it survives a round
          # trip through anyone else's logger.
          "qsl_sent", "qsl_rcvd", "qslsdate", "qslrdate",
          "lotw_qsl_sent", "lotw_qsl_rcvd", "eqsl_qsl_rcvd",
          "dxcc", "state", "cnty", "cqz", "ituz"]

# How far apart two logs may put the same QSO and still mean the same QSO.
# LoTW itself matches within 30 minutes, and clocks in the shack drift.
DUPE_MINUTES = 30

_TAG = re.compile(r"<([A-Za-z_0-9]+):(\d+)(?::[A-Za-z])?>")


class Logbook:
    def __init__(self, db_path) -> None:
        self.db_path = str(db_path)
        self._lock = threading.Lock()      # one writer; Flask serves threads
        self._init()

    @contextlib.contextmanager
    def _conn(self):
        """A connection that is actually closed afterwards.

        `with sqlite3.connect(...)` commits on the way out but does not close,
        so every read here used to leak a connection until the garbage
        collector happened to notice. On a long-running shack PC that ends as
        "too many open files", a long way from the code that caused it.
        """
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _init(self) -> None:
        cols = ", ".join(f"{f} TEXT" for f in FIELDS)
        with self._conn() as db:
            db.execute(f"CREATE TABLE IF NOT EXISTS qso "
                       f"(id INTEGER PRIMARY KEY AUTOINCREMENT, {cols}, created REAL)")
            db.execute("CREATE INDEX IF NOT EXISTS qso_call ON qso(call)")
            db.execute("CREATE INDEX IF NOT EXISTS qso_date ON qso(qso_date)")
            # Existing logs predate the confirmation columns. Add what is
            # missing rather than asking anyone to start their log again.
            have = {r[1] for r in db.execute("PRAGMA table_info(qso)")}
            for f in FIELDS:
                if f not in have:
                    db.execute(f"ALTER TABLE qso ADD COLUMN {f} TEXT")
            # Which online logbooks already have which contacts. A row per
            # (contact, service), written by a pull that found the contact
            # there and by a push that put it there - see `unheld`.
            db.execute("CREATE TABLE IF NOT EXISTS held "
                       "(qso_id INTEGER NOT NULL, service TEXT NOT NULL, "
                       "PRIMARY KEY (qso_id, service))")

    # --------------------------------------------------------------- writing
    def add(self, qso: dict, notify: bool = True) -> dict:
        """Put one contact in the log.

        `notify` is for bulk callers. One event per contact is right for
        one contact and wrong for a file: an ADIF import of a few
        thousand QSOs published a few thousand events into subscriber
        queues 256 deep, so every open page's stream was dropped as a
        stalled consumer part-way through its own import and had to
        reconnect. `import_adif` publishes once at the end instead.
        """
        call = str(qso.get("call") or "").strip().upper()
        if not call:
            raise ValueError("a QSO needs a callsign")
        now = datetime.now(timezone.utc)
        row = {f: (str(qso.get(f)).strip() if qso.get(f) not in (None, "") else "")
               for f in FIELDS}
        row["call"] = call
        row["qso_date"] = row["qso_date"] or now.strftime("%Y%m%d")
        row["time_on"] = row["time_on"] or now.strftime("%H%M")
        if not row["freq"] and qso.get("freq_hz"):
            row["freq"] = f"{float(qso['freq_hz']) / 1e6:.6f}"
        if not row["band"] and row["freq"]:
            row["band"] = band_for(float(row["freq"]))
        row["rst_sent"] = row["rst_sent"] or ("59" if row["mode"] in ("USB", "LSB", "FM", "AM") else "599")
        row["rst_rcvd"] = row["rst_rcvd"] or row["rst_sent"]

        with self._lock, self._conn() as db:
            cur = db.execute(
                f"INSERT INTO qso ({','.join(FIELDS)},created) "
                f"VALUES ({','.join('?' * len(FIELDS))},?)",
                [row[f] for f in FIELDS] + [now.timestamp()])
            saved = dict(db.execute("SELECT * FROM qso WHERE id=?",
                                    (cur.lastrowid,)).fetchone())
        if notify:
            BUS.publish("log.qso_added", saved, source="logbook")
        return saved

    def delete(self, qso_id: int) -> bool:
        with self._lock, self._conn() as db:
            n = db.execute("DELETE FROM qso WHERE id=?", (int(qso_id),)).rowcount
            db.execute("DELETE FROM held WHERE qso_id=?", (int(qso_id),))
        if n:
            BUS.publish("log.qso_deleted", {"id": int(qso_id)}, source="logbook")
        return bool(n)

    def update(self, qso_id: int, values: dict) -> dict | None:
        """Set some fields on an existing QSO. Used by a sync bringing back
        confirmations for contacts already in the log."""
        cols = [f for f in values if f in FIELDS]
        if not cols:
            return None
        with self._lock, self._conn() as db:
            db.execute(f"UPDATE qso SET {','.join(c + '=?' for c in cols)} "
                       f"WHERE id=?",
                       [str(values[c] or "") for c in cols] + [int(qso_id)])
            row = db.execute("SELECT * FROM qso WHERE id=?",
                             (int(qso_id),)).fetchone()
        return dict(row) if row else None

    def find_match(self, qso: dict, minutes: int = DUPE_MINUTES) -> dict | None:
        """The QSO in this log that another log means by `qso`, if any.

        Same station, same band, same mode, same day, and a start time within
        `minutes`. Matching on the timestamp alone is too strict - two logs
        almost never agree to the second - and on call alone far too loose,
        which would quietly merge every contact you have ever had with a
        regular.
        """
        call = str(qso.get("call") or "").strip().upper()
        date = str(qso.get("qso_date") or "").strip()
        if not (call and date):
            return None
        with self._conn() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT * FROM qso WHERE call=? AND qso_date=?", (call, date))]
        best, best_gap = None, None
        for r in rows:
            gap = qso_gap(qso, r, minutes)
            if gap is None:
                continue
            if best is None or gap < best_gap:
                best, best_gap = r, gap
        return best

    # --------------------------------------------------------------- reading
    def recent(self, limit: int = 25) -> list[dict]:
        with self._conn() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM qso ORDER BY id DESC LIMIT ?", (int(limit),))]

    def search(self, q: str, limit: int = 100) -> list[dict]:
        like = f"%{(q or '').strip().upper()}%"
        with self._conn() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM qso WHERE call LIKE ? OR gridsquare LIKE ? "
                "OR name LIKE ? ORDER BY id DESC LIMIT ?",
                (like, like, like, int(limit)))]

    def page(self, q: str = "", limit: int = 30, offset: int = 0) -> dict:
        """One page of the log, newest contact first, plus how many there are.

        Ordered by when the QSO happened rather than by row id: an imported
        log arrives in whatever order its file had, and paging by id would
        then hand out rows the caller has to re-sort - which silently drops
        contacts off the end of every page but the last.
        """
        where, args = "", []
        if (q or "").strip():
            like = f"%{q.strip().upper()}%"
            where = "WHERE call LIKE ? OR gridsquare LIKE ? OR name LIKE ? "
            args = [like, like, like]
        order = "ORDER BY qso_date DESC, time_on DESC, id DESC "
        with self._conn() as db:
            total = db.execute(f"SELECT COUNT(*) FROM qso {where}",
                               args).fetchone()[0]
            rows = [dict(r) for r in db.execute(
                f"SELECT * FROM qso {where}{order}LIMIT ? OFFSET ?",
                [*args, int(limit), max(0, int(offset))])]
        return {"rows": rows, "total": total,
                "limit": int(limit), "offset": max(0, int(offset))}

    def since(self, date: str = "", limit: int = 10 ** 7) -> list[dict]:
        """QSOs on or after an ADIF date (YYYYMMDD), oldest first.

        Uploads use this. Sending "everything in the log" to somebody's real
        logbook is rarely what was meant, and there is no undo at the far end.
        """
        d = "".join(ch for ch in str(date or "") if ch.isdigit())
        with self._conn() as db:
            if d:
                rows = db.execute(
                    "SELECT * FROM qso WHERE qso_date >= ? ORDER BY id LIMIT ?",
                    (d, int(limit)))
            else:
                rows = db.execute("SELECT * FROM qso ORDER BY id LIMIT ?",
                                  (int(limit),))
            return [dict(r) for r in rows]

    def after_id(self, qso_id: int = 0, limit: int = 10 ** 7) -> list[dict]:
        """QSOs logged after a given row id, oldest first.

        What an automatic upload sends. Re-sending the whole log every minute
        would work - the far end rejects duplicates - but it is rude and slow,
        and a marker is one integer.
        """
        with self._conn() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM qso WHERE id > ? ORDER BY id LIMIT ?",
                (int(qso_id or 0), int(limit)))]

    def mark_held(self, service: str, ids) -> None:
        """Record that `service` has these contacts."""
        ids = sorted({int(i) for i in ids if i})
        if not ids:
            return
        with self._lock, self._conn() as db:
            db.executemany("INSERT OR IGNORE INTO held (qso_id, service) "
                           "VALUES (?, ?)", [(i, service) for i in ids])

    def unheld(self, service: str, since: str = "", marker: int = 0,
               limit: int = 10 ** 7) -> list[dict]:
        """QSOs `service` does not have yet, oldest first - what a push sends.

        Pushing by row id alone was wrong the first time a log was pulled
        rather than typed: the contacts a pull brings in get fresh ids, so
        on a new install every one of them read as "not pushed yet" and a
        push offered to send the service its own log back. QRZ refuses
        those; World Radio League stores them, flagged as duplicates, and
        that is how one account came to hold 193 records of 87 QSOs. So a
        contact is held once a pull has found it there or a push has put
        it there, and only what is held nowhere is sent.

        `marker` is the old per-service high-water mark from config: rows at
        or below it were pushed before this table existed, so they count as
        held without rewriting anybody's config.
        """
        d = "".join(ch for ch in str(since or "") if ch.isdigit())
        sql = ("SELECT * FROM qso WHERE id > ? AND id NOT IN "
               "(SELECT qso_id FROM held WHERE service=?)")
        args: list = [int(marker or 0), service]
        if d:
            sql += " AND qso_date >= ?"
            args.append(d)
        with self._conn() as db:
            return [dict(r) for r in db.execute(
                sql + " ORDER BY id LIMIT ?", [*args, int(limit)])]

    def max_id(self) -> int:
        with self._conn() as db:
            return int(db.execute("SELECT COALESCE(MAX(id), 0) FROM qso")
                       .fetchone()[0])

    def worked_before(self, call: str, limit: int = 25) -> list[dict]:
        """Previous contacts with this station.

        Carries the comment as well as the log line: what you wrote down last
        time is usually the reason you wanted to know you had worked them.
        """
        with self._conn() as db:
            return [dict(r) for r in db.execute(
                "SELECT id, qso_date, time_on, band, mode, freq, rst_sent, "
                "rst_rcvd, comment FROM qso "
                "WHERE call=? ORDER BY id DESC LIMIT ?",
                ((call or "").strip().upper(), int(limit)))]

    def stats(self) -> dict:
        with self._conn() as db:
            total = db.execute("SELECT COUNT(*) FROM qso").fetchone()[0]
            uniq = db.execute("SELECT COUNT(DISTINCT call) FROM qso").fetchone()[0]
            by_band = {r[0] or "?": r[1] for r in db.execute(
                "SELECT band, COUNT(*) FROM qso GROUP BY band ORDER BY 2 DESC")}
            by_mode = {r[0] or "?": r[1] for r in db.execute(
                "SELECT mode, COUNT(*) FROM qso GROUP BY mode ORDER BY 2 DESC")}
            today = db.execute("SELECT COUNT(*) FROM qso WHERE qso_date=?",
                               (datetime.now(timezone.utc).strftime("%Y%m%d"),)).fetchone()[0]
        return {"total": total, "unique_calls": uniq, "today": today,
                "by_band": by_band, "by_mode": by_mode}

    # ------------------------------------------------------------------ ADIF
    def export_adif(self, my_callsign: str = "") -> str:
        rows = self.recent(limit=10 ** 7)
        head = [f"ADIF export from hamdash {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%SZ}",
                "<PROGRAMID:7>hamdash", "<EOH>", ""]
        out = list(head)
        for r in reversed(rows):
            if my_callsign and not r.get("my_callsign"):
                r["my_callsign"] = my_callsign
            parts = [f"<{f.upper()}:{len(str(r[f]))}>{r[f]}"
                     for f in FIELDS if r.get(f)]
            out.append(" ".join(parts) + " <EOR>")
        return "\n".join(out) + "\n"

    @staticmethod
    def parse_adif(text: str) -> list[dict]:
        """ADIF text to records. Fields are lower-cased ADIF names."""
        body = re.split(r"<eoh>", text, flags=re.I)[-1]
        out = []
        for chunk in re.split(r"<eor>", body, flags=re.I):
            rec, pos = {}, 0
            while (m := _TAG.search(chunk, pos)):
                n = int(m.group(2))
                rec[m.group(1).lower()] = chunk[m.end():m.end() + n]
                pos = m.end() + n
            if rec.get("call"):
                out.append(rec)
        return out

    def export_adif_record(self, row: dict, my_callsign: str = "") -> str:
        """One QSO as a single ADIF record. QRZ inserts them one at a time."""
        r = dict(row)
        if my_callsign and not r.get("my_callsign"):
            r["my_callsign"] = my_callsign
        return " ".join(f"<{f.upper()}:{len(str(r[f]))}>{r[f]}"
                        for f in FIELDS if r.get(f)) + " <EOR>"

    def import_adif(self, text: str, merge: bool = True,
                    held_by: str = "") -> dict:
        """Import ADIF, without duplicating what is already there.

        `merge` updates a QSO already in the log with any field the incoming
        record has and this one doesn't - which is how a confirmation from
        LoTW lands on the contact it belongs to instead of beside it. Returns
        counts rather than one number, because "nothing was added" and
        "nothing happened" are different answers.

        `held_by` names the service the text was pulled from: every contact
        it added or matched is one that service already has, so a push
        must not send it back (`unheld`).

        `repeats` counts records that are a second or later copy of one
        contact *within this text*. They are not in the other three, which
        count contacts: World Radio League stores duplicates, and a pull
        from an account holding 193 records of 87 QSOs said "193 already
        had" - true of the records, and read as the log having grown.
        """
        added = updated = skipped = repeats = 0
        last = None
        changed = False
        seen: set[int] = set()
        for rec in self.parse_adif(text):
            existing = self.find_match(rec)
            if existing is None:
                try:
                    last = self.add(rec, notify=False)
                    seen.add(last["id"])
                    added += 1
                    changed = True
                except ValueError:
                    skipped += 1
                continue
            repeat = existing["id"] in seen
            seen.add(existing["id"])
            fresh = {f: rec[f] for f in FIELDS
                     if rec.get(f) and not (existing.get(f) or "").strip()}
            if merge and fresh:
                # A later copy can still know something the first did not,
                # so it is merged like any other match - only counted apart.
                self.update(existing["id"], fresh)
                changed = True
            if repeat:
                repeats += 1
            elif merge and fresh:
                updated += 1
            else:
                skipped += 1
        if held_by:
            self.mark_held(held_by, seen)
        counts = {"added": added, "updated": updated, "skipped": skipped,
                  "repeats": repeats}
        if changed:
            # One event for the whole import, carrying a contact that was
            # actually added as well as the counts. A single-record
            # import - which is what WSJT-X sends on every contact - then
            # looks exactly like `add()`, so a card reading
            # `payload["call"]` keeps working; a file also gets the
            # counts. Nothing is published when nothing changed: a tile
            # redrawing for an import that added no rows is a fetch and
            # a flicker for no reason.
            BUS.publish("log.qso_added", {**(last or {}), **counts},
                        source="logbook")
        return counts


def qso_gap(qso: dict, row: dict, minutes: int = DUPE_MINUTES) -> int | None:
    """How many minutes apart two records of the same contact are, or None
    if they are not the same contact.

    The rule `find_match` uses, on its own so that a check against somebody
    else's log - World Radio League keeps duplicates rather than refusing
    them - agrees with the importer about what "the same QSO" means.
    """
    call = str(qso.get("call") or "").strip().upper()
    date = str(qso.get("qso_date") or "").strip()
    if not (call and date):
        return None
    if (str(row.get("call") or "").strip().upper() != call
            or str(row.get("qso_date") or "").strip() != date):
        return None
    band = str(qso.get("band") or "").strip().lower()
    mode = str(qso.get("mode") or "").strip().upper()
    if band and (row.get("band") or "").lower() != band:
        return None
    if mode and (row.get("mode") or "").upper() != mode:
        return None
    want = _minutes(qso.get("time_on"))
    if want is None:
        return 0
    got = _minutes(row.get("time_on"))
    if got is None or abs(got - want) > minutes:
        return None
    return abs(got - want)


def _minutes(hhmm) -> int | None:
    """ADIF TIME_ON (HHMM or HHMMSS) as minutes past midnight."""
    t = re.sub(r"\D", "", str(hhmm or ""))
    if len(t) < 4:
        return None
    try:
        return int(t[:2]) * 60 + int(t[2:4])
    except ValueError:
        return None
