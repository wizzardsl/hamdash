"""Problem reports: the logs, the debug trace, the radio check, the zip.

Everything here is for somebody else's station - a radio nobody here owns,
on an operating system that may not be this one - so the tests drive the
real handlers and the real routes rather than reading the code:

* the main log keeps writing when Windows will not let it rotate (the
  stock handler drops every record, shown failing below);
* debug files are by UTC day, pruned at 48 hours and capped in size;
* a polled answer that has not changed is written once, not twice a second;
* the radio check only ever reads;
* the report is local-only, carries no stored secret, and fits on GitHub.

Run with:  python -m unittest discover -s tests
"""
import io
import json
import logging
import logging.handlers
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from hamdash.services import diagnostics as d
from hamdash.services import rigs


def a_dir(tag="diag"):
    return Path(tempfile.mkdtemp(prefix=f"hamdash-{tag}-"))


def a_record(msg, at=None, name="hamdash.test", level=logging.INFO):
    rec = logging.LogRecord(name, level, __file__, 1, msg, None, None)
    if at is not None:
        rec.created = at
    return rec


class TestTheMainLogRotates(unittest.TestCase):
    def test_by_size_keeping_five(self):
        h = d.main_handler(a_dir(), logging.Formatter("%(message)s"))
        try:
            self.assertEqual((h.maxBytes, h.backupCount), (1_000_000, 5))
            self.assertEqual(h.level, logging.INFO,
                             "the radio trace must never reach the main log")
        finally:
            h.close()

    def _blocked(self, cls):
        """A handler whose rename always fails, as on Windows while
        something has the file open."""
        home = a_dir("rot")
        h = cls(home / "hamdash.log", maxBytes=200, backupCount=5,
                encoding="utf-8")
        h.setFormatter(logging.Formatter("%(message)s"))
        h.rotate = mock.Mock(side_effect=PermissionError(
            32, "The process cannot access the file"))
        h.handleError = mock.Mock()
        return home, h

    def test_a_blocked_rename_loses_nothing(self):
        home, h = self._blocked(d.SafeRotatingFileHandler)
        try:
            for n in range(40):
                h.handle(a_record(f"line {n:03d} " + "x" * 30))
        finally:
            h.close()
        text = (home / "hamdash.log").read_text(encoding="utf-8")
        for n in range(40):
            self.assertIn(f"line {n:03d}", text)
        self.assertIn("could not roll the log over", text)
        self.assertEqual(h.rotate.call_count, 1,
                         "and it does not retry on every record")
        h.handleError.assert_not_called()

    def test_the_stock_handler_does_lose_them(self):
        """Why the subclass exists - so the test above is testing something."""
        home, h = self._blocked(logging.handlers.RotatingFileHandler)
        try:
            for n in range(40):
                h.handle(a_record(f"line {n:03d} " + "x" * 30))
        finally:
            h.close()
        text = (home / "hamdash.log").read_text(encoding="utf-8")
        self.assertNotIn("line 039", text)
        self.assertTrue(h.handleError.called)

    def test_it_tries_again_later(self):
        home, h = self._blocked(d.SafeRotatingFileHandler)
        try:
            for n in range(10):
                h.handle(a_record("y" * 60))
            self.assertEqual(h.rotate.call_count, 1)
            h._blocked_until = 0.0              # RETRY_S has passed
            h.rotate.side_effect = None         # and the file is free again
            h.handle(a_record("z" * 60))
            self.assertEqual(h.rotate.call_count, 2,
                             "the next record after the wait rolled over")
        finally:
            h.close()


class TestTheDebugLog(unittest.TestCase):
    def handler(self, **kw):
        home = a_dir("dbg")
        h = d.DailyDebugHandler(home, **kw)
        h.setFormatter(logging.Formatter("%(message)s"))
        self.addCleanup(h.close)
        return home, h

    def test_one_file_per_utc_day(self):
        home, h = self.handler()
        day1 = time.mktime((2026, 11, 1, 12, 0, 0, 0, 0, 0)) - time.timezone
        h.handle(a_record("first", at=day1))
        h.handle(a_record("second", at=day1 + 86400))
        names = [p.name for p in d.debug_files(home)]
        self.assertEqual(names, ["hamdash-debug-2026-11-01.log",
                                 "hamdash-debug-2026-11-02.log"])

    def test_older_than_48_hours_goes(self):
        home, h = self.handler()
        old = home / "hamdash-debug-2020-01-01.log"
        old.write_text("ancient\n")
        os.utime(old, (time.time() - 49 * 3600,) * 2)
        recent = home / "hamdash-debug-2020-01-02.log"
        recent.write_text("yesterday\n")
        os.utime(recent, (time.time() - 20 * 3600,) * 2)
        h.handle(a_record("now"))
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_the_cap_drops_older_days_first(self):
        home, h = self.handler(cap=4000)
        older = home / "hamdash-debug-2020-01-02.log"
        older.write_text("o" * 3000)
        os.utime(older, (time.time() - 3600,) * 2)
        for _ in range(20):
            h.handle(a_record("n" * 99))
        self.assertFalse(older.exists(), "yesterday made room for today")

    def test_today_alone_at_the_cap_stops_with_a_note(self):
        home, h = self.handler(cap=1000)
        for n in range(50):
            h.handle(a_record(f"{n:02d}" + "n" * 98))
        (today,) = d.debug_files(home)
        text = today.read_text()
        self.assertIn("nothing more is written until", text)
        self.assertLess(len(text), 1200)


class TestTheTrace(unittest.TestCase):
    def setUp(self):
        self.records = []
        catcher = logging.Handler(logging.DEBUG)
        catcher.emit = lambda r: self.records.append(r.getMessage())
        d.TRACE.addHandler(catcher)
        self.addCleanup(d.TRACE.removeHandler, catcher)
        d.TRACE.setLevel(logging.DEBUG)
        self.addCleanup(d.TRACE.setLevel, logging.NOTSET)
        d._state.on = True
        self.addCleanup(setattr, d._state, "on", False)
        d._last.clear()

    def test_an_unchanged_poll_is_one_line(self):
        for _ in range(500):
            d.trace("flrig 127.0.0.1:12345", "rig.get_vfo()", "14074000")
        self.assertEqual(len(self.records), 1)

    def test_a_change_says_how_many_times_the_last_answer_came(self):
        for _ in range(5):
            d.trace("rig", "f", ["14074000"])
        d.trace("rig", "f", ["7074000"])
        self.assertEqual(len(self.records), 3)
        self.assertIn("5 times", self.records[1])
        self.assertIn("7074000", self.records[2])

    def test_a_long_quiet_spell_is_still_noted(self):
        for _ in range(d.REPEAT_NOTE):
            d.trace("rig", "f", ["14074000"])
        self.assertEqual(len(self.records), 2)
        self.assertIn("unchanged", self.records[1])

    def test_writes_are_never_folded(self):
        for _ in range(3):
            d.trace("rig", "F 14074000", [], repeat=False)
        self.assertEqual(len(self.records), 3)

    def test_errors_are_written(self):
        d.trace("rig", "f", error="no rigctld at 127.0.0.1:4532")
        self.assertIn("ERROR no rigctld", self.records[0])

    def test_off_costs_nothing(self):
        d._state.on = False
        d.trace("rig", "f", ["1"])
        self.assertEqual(self.records, [])


class TestSwitchingDebugOnAndOff(unittest.TestCase):
    def test_it_adds_and_removes_its_handler(self):
        home = a_dir("switch")
        root = logging.getLogger()
        d.install(home, logging.Formatter("%(message)s"))
        self.addCleanup(lambda: (d.set_debug(False),
                                 setattr(d._state, "home", None)))
        d.set_debug(True)
        self.assertIn(d._state.handler, root.handlers)
        d.trace("radio FT-900 [flrig]", "state", {"ok": True})
        handler = d._state.handler
        d.set_debug(False)
        self.assertNotIn(handler, root.handlers)
        (f,) = d.debug_files(home)
        self.assertIn("radio FT-900 [flrig] state", f.read_text())


class TestTheRadioCheckOnlyReads(unittest.TestCase):
    """It runs on radios nobody here owns. It must not be able to change
    one, let alone key it."""

    def test_flrig_asks_only_get_questions(self):
        for name in rigs.FlrigBackend.PROBE:
            self.assertIn(".get", name, name)

    def test_rigctld_asks_no_set_command(self):
        for line in rigs.RigctldBackend.PROBE:
            head = line.split()[0]
            self.assertFalse(head[:1].isupper(), f"{line} is a set command")
            self.assertNotIn("set", head)
            self.assertNotIn("dump_caps", line,
                             "it killed a running rigctld here")

    def test_flrig_probe_records_refusals_and_carries_on(self):
        b = rigs.FlrigBackend()
        calls = []

        def fake(name, *args):
            calls.append(name)
            if name == "rig.get_bws":
                raise rigs.RigError("flrig refused rig.get_bws: unknown")
            return "x"

        with mock.patch.object(b, "_call_raw", side_effect=fake):
            got = b.probe()
        self.assertEqual(calls, list(rigs.FlrigBackend.PROBE))
        self.assertIn("error", got["rig.get_bws"])
        self.assertEqual(got["rig.get_xcvr"], "x")
        self.assertFalse(any("set" in c for c in calls))


def a_host():
    from hamdash.app import create_app
    from hamdash.config import Config
    home = a_dir("host")
    (home / "config.json").write_text(json.dumps({
        "station": {"callsign": "VA7SLL", "grid": "CN89kg"},
        "radio": {"mock": True},
        "callsign_lookup": {"user": "me", "password": "lookuppass-9"},
        "logbook_sync": {"qrz_api_key": "QRZ-SECRET-1234"},
        "wsjtx": {"enabled": False},
    }), encoding="utf-8")
    (home / "layout.json").write_text("[]", encoding="utf-8")
    cfg = Config(home)
    app, shutdown = create_app(cfg, port=0)
    return cfg, app, shutdown


class TestTheReport(unittest.TestCase):
    def setUp(self):
        self.cfg, self.app, shutdown = a_host()
        if callable(shutdown):
            self.addCleanup(shutdown)
        self.client = self.app.test_client()
        home = self.cfg.home
        now = time.time()
        stamp = lambda t: time.strftime("%Y-%m-%d %H:%M:%S,000", time.localtime(t))
        (home / "hamdash.log.1").write_text(
            f"{stamp(now - 4 * 86400)} INFO    hamdash: four days ago\n"
            f"{stamp(now - 3600)} ERROR   hamdash: an hour ago\n"
            "Traceback (most recent call last):\n"
            "  the traceback line goes with it\n", encoding="utf-8")
        (home / "hamdash.log").write_text(
            f"{stamp(now - 60)} INFO    hamdash: a minute ago, "
            f"key=QRZ-SECRET-1234 and password=lookuppass-9\n",
            encoding="utf-8")

    def make(self, addr="127.0.0.1"):
        return self.client.post("/api/diag/report", json={},
                                environ_base={"REMOTE_ADDR": addr})

    def test_only_from_this_machine(self):
        r = self.make("192.0.2.44")
        self.assertEqual(r.status_code, 403)
        self.assertIn("machine hamdash is running on", r.get_json()["error"])
        self.assertFalse((self.cfg.home / "reports").exists())

    def test_it_writes_a_zip_with_the_last_two_days(self):
        r = self.make()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        got = r.get_json()
        path = Path(got["path"])
        self.assertTrue(path.is_file())
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            log = z.read("logs/hamdash.log").decode()
            info = json.loads(z.read("info.json"))
        self.assertIn("README.txt", names)
        self.assertIn("an hour ago", log)
        self.assertIn("the traceback line goes with it", log)
        self.assertIn("a minute ago", log)
        self.assertNotIn("four days ago", log)
        self.assertEqual(info["radios"]["selected"],
                         self.cfg.data["radio"]["active"])
        self.assertIn("issues/new?template=bug.yml", got["issue"])

    def test_no_stored_secret_is_anywhere_in_it(self):
        path = Path(self.make().get_json()["path"])
        with zipfile.ZipFile(path) as z:
            everything = b"".join(z.read(n) for n in z.namelist()).decode()
        for secret in ("QRZ-SECRET-1234", "lookuppass-9"):
            self.assertNotIn(secret, everything)
        self.assertIn("[redacted]", everything)

    def test_the_download_is_local_and_by_name_only(self):
        name = self.make().get_json()["name"]
        ok = self.client.get(f"/api/diag/report/{name}",
                             environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.addCleanup(ok.close)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.data[:2], b"PK")
        far = self.client.get(f"/api/diag/report/{name}",
                              environ_base={"REMOTE_ADDR": "192.0.2.44"})
        self.addCleanup(far.close)
        self.assertEqual(far.status_code, 403)
        sly = self.client.get("/api/diag/report/..%2Fconfig.json",
                              environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.addCleanup(sly.close)
        self.assertEqual(sly.status_code, 404)

    def test_only_the_newest_five_are_kept(self):
        folder = self.cfg.home / "reports"
        for n in range(8):
            d.write_report(self.cfg.home, b"PK", now=1_700_000_000 + n)
        self.assertEqual(len(list(folder.glob("*.zip"))), d.REPORT_KEEP)

    def test_the_radio_check_reaches_the_report(self):
        r = self.client.post("/api/diag/radio-check", json={})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["state"]["ok"])
        path = Path(self.make().get_json()["path"])
        with zipfile.ZipFile(path) as z:
            info = json.loads(z.read("info.json"))
        self.assertTrue(info["radios"]["last_check"]["state"]["ok"])


class TestTheReportStaysAttachable(unittest.TestCase):
    def test_a_huge_debug_log_is_cut_to_its_newest_lines(self):
        home = a_dir("big")
        today = home / f"{d.DEBUG_PREFIX}2026-11-02.log"
        today.write_text("old line\n" + "x" * 200 + "\nnewest line\n")
        with mock.patch.object(d, "REPORT_TEXT_CAP", 120):
            data = d.build_report(home, {})
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            text = z.read(f"logs/{today.name}").decode()
        self.assertIn("newest line", text)
        self.assertNotIn("old line", text)
        self.assertIn("left out to keep the report attachable", text)


class TestTheIssueForm(unittest.TestCase):
    def test_the_link_fills_fields_the_form_has(self):
        form = (Path(__file__).resolve().parent.parent / ".github"
                / "ISSUE_TEMPLATE" / "bug.yml").read_text(encoding="utf-8")
        url = d.issue_url("1.2.0", "win32", "omnirig")
        for field in ("version", "report"):
            self.assertIn(f"{field}=", url)
            self.assertIn(f"id: {field}", form)
        self.assertIn("template=bug.yml", url)


if __name__ == "__main__":
    unittest.main()
