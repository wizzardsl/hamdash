"""The local store behind the PSK Reporter card.

A JSON blob per query was the first shape and it worked, but it wasted the
best thing their API offers. This keeps reports in SQLite and pulls
*incrementally*.

Why that is better, in the order it matters:

* **`lastseqno`.** Every reply carries the last sequence number in their
  database; passing it back asks only for what has arrived since. After the
  first pull each one is small - which is precisely the "adaptive use of the
  retrieval mechanism" their developer notes ask for, in as many words.
* **More history than one reply can hold.** A single query is capped
  (`rptlimit`, and 24 hours whatever you ask for), so a busy square truncated
  at two thousand reports and the card had to admit it. Accumulating across
  polls builds the window up instead of re-fetching and re-truncating it.
* **Time stops costing requests.** The whole retained window is local, so
  moving from "last 15 minutes" to "last 24 hours" is a query against this
  file. Under the old shape a wider window was a different cache key and
  therefore a new request.
* **One target, one entry.** Band and mode are filtered here or in the page,
  so they never fragment the cache the way a mode in the query string did.

Correctness never depends on `lastseqno` behaving. Rows are keyed by what
makes a report unique - who, to whom, when, on what - so a pull that overlaps
what is already held updates rather than duplicates, and a `lastseqno` that
is ignored costs bandwidth and nothing else.
"""
from __future__ import annotations

import contextlib
import sqlite3
import time

# The host's band table, not a fourth copy of it: three had already
# drifted apart. bands.py imports nothing, so a card may have it.
from hamdash.services.bands import for_mhz as band_for

# Their own limit is 24 hours of history, so there is no sense keeping more:
# nothing could refill it after a restart.
RETAIN = 24 * 3600
COLUMNS = ("target", "tx", "rx", "tx_grid", "rx_grid", "hz", "mode", "snr",
           "at", "is_tx", "is_rx", "tx_dxcc", "rx_dxcc", "tx_region",
           "rx_region")


class Store:
    """Reports on disk, one row per report, pruned to `RETAIN`."""

    def __init__(self, path) -> None:
        self.path = str(path)
        with self._conn() as db:
            db.execute(f"""CREATE TABLE IF NOT EXISTS report (
                {', '.join(f'{c} TEXT' for c in COLUMNS)},
                PRIMARY KEY (target, tx, rx, at, hz))""")
            db.execute("CREATE INDEX IF NOT EXISTS report_when "
                       "ON report(target, at)")
            db.execute("""CREATE TABLE IF NOT EXISTS fetch (
                target TEXT PRIMARY KEY, as_grid INTEGER, last_seq TEXT,
                at REAL, wait REAL, error TEXT, failed INTEGER,
                first_at REAL)""")

    @contextlib.contextmanager
    def _conn(self):
        """A connection that is committed *and* closed.

        `with sqlite3.connect(...)` commits and leaves the handle open - the
        same trap the logbook fell into, and which this fell into again while
        quoting the warning about it. Every read here is short, so one
        connection per call is fine; leaking one per call is not.
        """
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    # ---------------------------------------------------------------- state
    def state(self, target: str) -> dict:
        with self._conn() as db:
            row = db.execute("SELECT * FROM fetch WHERE target=?",
                             (target,)).fetchone()
        return dict(row) if row else {}

    def mark(self, target: str, **fields) -> None:
        keep = {"as_grid", "last_seq", "at", "wait", "error", "failed",
                "first_at"}
        fields = {k: v for k, v in fields.items() if k in keep}
        with self._conn() as db:
            db.execute("INSERT OR IGNORE INTO fetch (target) VALUES (?)",
                       (target,))
            if fields:
                sets = ", ".join(f"{k}=?" for k in fields)
                db.execute(f"UPDATE fetch SET {sets} WHERE target=?",
                           [*fields.values(), target])
            db.commit()

    # ---------------------------------------------------------------- rows
    def add(self, target: str, rows: list[dict]) -> int:
        """Store reports. Returns how many were new to us.

        `INSERT OR REPLACE` on the natural key, so an overlapping pull - or
        one where `lastseqno` was ignored entirely - updates in place instead
        of doubling the map up.
        """
        if not rows:
            return 0
        with self._conn() as db:
            before = db.execute("SELECT COUNT(*) FROM report WHERE target=?",
                                (target,)).fetchone()[0]
            db.executemany(
                f"INSERT OR REPLACE INTO report ({', '.join(COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(COLUMNS))})",
                [[target] + [_text(r.get(c)) for c in COLUMNS[1:]]
                 for r in rows])
            after = db.execute("SELECT COUNT(*) FROM report WHERE target=?",
                               (target,)).fetchone()[0]
            db.commit()
        return after - before

    def since(self, target: str, seconds: float) -> list[dict]:
        cut = int(time.time() - seconds)
        with self._conn() as db:
            rows = db.execute(
                "SELECT * FROM report WHERE target=? AND CAST(at AS INTEGER) >= ? "
                "ORDER BY CAST(at AS INTEGER) DESC", (target, cut)).fetchall()
        return [_row(r) for r in rows]

    def count(self, target: str) -> int:
        with self._conn() as db:
            return db.execute("SELECT COUNT(*) FROM report WHERE target=?",
                              (target,)).fetchone()[0]

    def oldest(self, target: str) -> int:
        with self._conn() as db:
            got = db.execute("SELECT MIN(CAST(at AS INTEGER)) FROM report "
                             "WHERE target=?", (target,)).fetchone()[0]
        return int(got or 0)

    def prune(self, keep: float = RETAIN) -> int:
        """Drop what their API could no longer replace anyway."""
        cut = int(time.time() - keep)
        with self._conn() as db:
            n = db.execute("DELETE FROM report WHERE CAST(at AS INTEGER) < ?",
                           (cut,)).rowcount
            # A target nobody watches any more takes its marker with it.
            db.execute("DELETE FROM fetch WHERE target NOT IN "
                       "(SELECT DISTINCT target FROM report) AND at < ?",
                       (time.time() - keep,))
            db.commit()
        return n

    def targets(self) -> list[str]:
        with self._conn() as db:
            return [r[0] for r in db.execute(
                "SELECT DISTINCT target FROM report")]


def _text(v):
    if v is None:
        return ""
    if v is True:
        return "1"
    if v is False:
        return "0"
    return str(v)


def _row(r: sqlite3.Row) -> dict:
    hz = _int(r["hz"]) or 0
    mhz = round(hz / 1e6, 6)
    return {"tx": r["tx"], "rx": r["rx"],
            "tx_grid": r["tx_grid"], "rx_grid": r["rx_grid"],
            "hz": hz, "mhz": mhz, "band": band_for(mhz),
            "mode": r["mode"], "snr": _int(r["snr"]),
            "at": _int(r["at"]) or 0,
            "is_tx": r["is_tx"] == "1", "is_rx": r["is_rx"] == "1",
            "tx_dxcc": r["tx_dxcc"], "rx_dxcc": r["rx_dxcc"],
            "tx_region": r["tx_region"], "rx_region": r["rx_region"]}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
