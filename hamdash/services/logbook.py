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

BANDS = [(1.8, 2.0, "160m"), (3.5, 4.0, "80m"), (5.25, 5.45, "60m"),
         (7.0, 7.3, "40m"), (10.1, 10.15, "30m"), (14.0, 14.35, "20m"),
         (18.068, 18.168, "17m"), (21.0, 21.45, "15m"), (24.89, 24.99, "12m"),
         (28.0, 29.7, "10m"), (50.0, 54.0, "6m"), (144.0, 148.0, "2m"),
         (222.0, 225.0, "1.25m"), (420.0, 450.0, "70cm"),
         (902.0, 928.0, "33cm"), (1240.0, 1300.0, "23cm")]

_TAG = re.compile(r"<([A-Za-z_0-9]+):(\d+)(?::[A-Za-z])?>")


def band_for(mhz: float | None) -> str:
    if not mhz:
        return ""
    return next((n for lo, hi, n in BANDS if lo <= mhz <= hi), "")


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

    # --------------------------------------------------------------- writing
    def add(self, qso: dict) -> dict:
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
        BUS.publish("log.qso_added", saved, source="logbook")
        return saved

    def delete(self, qso_id: int) -> bool:
        with self._lock, self._conn() as db:
            n = db.execute("DELETE FROM qso WHERE id=?", (int(qso_id),)).rowcount
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
        band = str(qso.get("band") or "").strip().lower()
        mode = str(qso.get("mode") or "").strip().upper()
        want = _minutes(qso.get("time_on"))
        with self._conn() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT * FROM qso WHERE call=? AND qso_date=?", (call, date))]
        best = None
        for r in rows:
            if band and (r.get("band") or "").lower() != band:
                continue
            if mode and (r.get("mode") or "").upper() != mode:
                continue
            if want is None:
                return r
            got = _minutes(r.get("time_on"))
            if got is None or abs(got - want) > minutes:
                continue
            if best is None or abs(got - want) < abs(_minutes(best["time_on"]) - want):
                best = r
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

    def import_adif(self, text: str, merge: bool = True) -> dict:
        """Import ADIF, without duplicating what is already there.

        `merge` updates a QSO already in the log with any field the incoming
        record has and this one doesn't - which is how a confirmation from
        LoTW lands on the contact it belongs to instead of beside it. Returns
        counts rather than one number, because "nothing was added" and
        "nothing happened" are different answers.
        """
        added = updated = skipped = 0
        for rec in self.parse_adif(text):
            existing = self.find_match(rec)
            if existing is None:
                try:
                    self.add(rec)
                    added += 1
                except ValueError:
                    skipped += 1
                continue
            if not merge:
                skipped += 1
                continue
            fresh = {f: rec[f] for f in FIELDS
                     if rec.get(f) and not (existing.get(f) or "").strip()}
            if fresh:
                self.update(existing["id"], fresh)
                updated += 1
            else:
                skipped += 1
        return {"added": added, "updated": updated, "skipped": skipped}


def _minutes(hhmm) -> int | None:
    """ADIF TIME_ON (HHMM or HHMMSS) as minutes past midnight."""
    t = re.sub(r"\D", "", str(hhmm or ""))
    if len(t) < 4:
        return None
    try:
        return int(t[:2]) * 60 + int(t[2:4])
    except ValueError:
        return None
