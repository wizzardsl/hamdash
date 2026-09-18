"""SQLite logbook with ADIF in and out.

ADIF is non-negotiable: anyone with a real log will want it in and out of
LoTW, QRZ or Club Log, and a log you can't export is a log you can't keep.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timezone

from ..bus import BUS

FIELDS = ["call", "qso_date", "time_on", "time_off", "band", "mode", "freq",
          "rst_sent", "rst_rcvd", "gridsquare", "name", "qth", "country",
          "comment", "tx_pwr", "my_callsign", "my_gridsquare"]

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

    def _conn(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def _init(self) -> None:
        cols = ", ".join(f"{f} TEXT" for f in FIELDS)
        with self._conn() as db:
            db.execute(f"CREATE TABLE IF NOT EXISTS qso "
                       f"(id INTEGER PRIMARY KEY AUTOINCREMENT, {cols}, created REAL)")
            db.execute("CREATE INDEX IF NOT EXISTS qso_call ON qso(call)")
            db.execute("CREATE INDEX IF NOT EXISTS qso_date ON qso(qso_date)")

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

    def worked_before(self, call: str) -> list[dict]:
        with self._conn() as db:
            return [dict(r) for r in db.execute(
                "SELECT id, qso_date, time_on, band, mode FROM qso "
                "WHERE call=? ORDER BY id DESC LIMIT 10",
                ((call or "").strip().upper(),))]

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

    def import_adif(self, text: str) -> int:
        body = re.split(r"<eoh>", text, flags=re.I)[-1]
        count = 0
        for chunk in re.split(r"<eor>", body, flags=re.I):
            rec, pos = {}, 0
            while (m := _TAG.search(chunk, pos)):
                n = int(m.group(2))
                rec[m.group(1).lower()] = chunk[m.end():m.end() + n]
                pos = m.end() + n
            if rec.get("call"):
                try:
                    self.add(rec)
                    count += 1
                except ValueError:
                    pass
        return count
