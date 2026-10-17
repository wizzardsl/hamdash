"""A push sends only what the far end does not already have.

The bug this guards: what a push sent was chosen by row id against a
per-service marker in config, and a pull never moved that marker. So a new
install that *pulled* its log - 87 QSOs from QRZ into a blank logbook - got
them as rows 1-87, every one above a marker of 0, and the logbook then
offered to push all 87 straight back to the service it had just read them
from. QRZ refuses a duplicate; World Radio League stores it, flagged
`isDuplicate`, and answers success. One real account held 193 records of 87
QSOs that way, from three pushes.

Nothing here touches a real service: the two clients are mocked at the
class, and everything else - the routes, the runner, the logbook - is the
real thing.

Run with:  python -m unittest discover -s tests
"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from hamdash.app import create_app
from hamdash.config import Config
from hamdash.services.logbook import Logbook
from hamdash.services.qsl import SyncError

QRZ_ADIF = """<EOH>
<CALL:5>W1AW <QSO_DATE:8>20260101 <TIME_ON:4>1200 <BAND:3>20m <MODE:3>SSB <EOR>
<CALL:4>K1JT <QSO_DATE:8>20260102 <TIME_ON:4>1300 <BAND:3>40m <MODE:2>CW <EOR>
<CALL:5>JA0RU <QSO_DATE:8>20260103 <TIME_ON:4>0900 <BAND:3>15m <MODE:3>FT8 <EOR>
"""

# The same three contacts as World Radio League keeps them: one instant,
# a band in metres, and clocks that disagree with ours by a minute.
WRL_HAS = [
    {"call": "W1AW", "timestamp": "2026-01-01T12:01:00Z", "freq": 14.2,
     "band": 20, "mode": "SSB"},
    {"call": "K1JT", "timestamp": "2026-01-02T12:59:00Z", "freq": 7.03,
     "band": 40, "mode": "CW"},
]


def a_host(**sync):
    home = Path(tempfile.mkdtemp(prefix="hamdash-held-"))
    (home / "config.json").write_text(json.dumps({
        "station": {"callsign": "VA7SLL", "grid": "CN89kg"},
        "radio": {"mock": True},
        "wsjtx": {"enabled": False},
        "logbook_sync": {"qrz_api_key": "K", "wrl_api_key": "W", **sync},
    }), encoding="utf-8")
    (home / "layout.json").write_text("[]", encoding="utf-8")
    app, shutdown = create_app(Config(home), port=0)
    return app.test_client(), shutdown


class Host(unittest.TestCase):
    sync: dict = {}

    def setUp(self):
        self.client, self._shutdown = a_host(**self.sync)
        pause = mock.patch("hamdash.app.WrlLogbook.WRITE_PAUSE", 0)
        pause.start()
        self.addCleanup(pause.stop)

    def tearDown(self):
        try:
            self._shutdown()
        except Exception:
            pass

    def run_job(self, service, direction, **body):
        got = self.client.post(f"/api/log/sync/{service}/{direction}",
                               json=body)
        self.assertEqual(got.status_code, 200, got.get_json())
        end = time.time() + 5
        while time.time() < end:
            state = self.client.get("/api/log/sync").get_json()["state"]
            if not state["running"]:
                self.assertTrue(state["ok"], state["message"])
                return state["result"]
            time.sleep(0.01)
        self.fail(f"{service} {direction} did not finish")

    def message_of(self, service, direction, **body):
        self.run_job(service, direction, **body)
        return self.client.get("/api/log/sync").get_json()["state"]["message"]

    def pending(self, service):
        return self.client.get("/api/log/sync").get_json()[service]["pending"]

    def log_one(self, call, date, hhmm, band="20m", mode="SSB"):
        self.client.post("/api/log", json={"call": call, "qso_date": date,
                                           "time_on": hhmm, "band": band,
                                           "mode": mode})


class TestAPulledLogIsNotPushedBack(Host):
    def pull_from_qrz(self):
        with mock.patch("hamdash.app.QrzLogbook.pull", return_value=QRZ_ADIF):
            res = self.run_job("qrz", "pull")
        self.assertEqual(res["added"], 3)

    def test_nothing_is_pending_for_the_service_it_came_from(self):
        """The reported case, on a blank log."""
        self.pull_from_qrz()
        self.assertEqual(self.pending("qrz"), 0)

    def test_a_push_would_send_nothing(self):
        self.pull_from_qrz()
        with mock.patch("hamdash.app.QrzLogbook.push_record") as push:
            res = self.run_job("qrz", "push", new_only=True, dry_run=False)
        push.assert_not_called()
        self.assertEqual(res["sent"], 0)

    def test_a_manual_date_range_push_skips_them_too(self):
        """A dated push used to send everything in the range, held or not -
        which is how a whole log went to WRL a second and third time."""
        self.pull_from_qrz()
        res = self.run_job("qrz", "push", since="20250101")
        self.assertEqual(res["would_send"], 0)

    def test_a_contact_logged_afterwards_is_still_pending(self):
        self.pull_from_qrz()
        self.log_one("VE7SL", "20260110", "1500")
        self.assertEqual(self.pending("qrz"), 1)

    def test_pulling_again_still_counts_them_as_held(self):
        """A second pull matches rather than adds - and a match is just as
        much "QRZ has this" as an add."""
        self.pull_from_qrz()
        with mock.patch("hamdash.app.QrzLogbook.pull", return_value=QRZ_ADIF):
            self.assertEqual(self.run_job("qrz", "pull")["added"], 0)
        self.assertEqual(self.pending("qrz"), 0)

    def test_held_by_one_service_is_not_held_by_another(self):
        """QRZ having the log says nothing about World Radio League."""
        self.pull_from_qrz()
        self.assertEqual(self.pending("wrl"), 3)


class TestQrzSaysWhatItHas(Host):
    def test_a_duplicate_refusal_counts_as_held(self):
        self.log_one("W1AW", "20260101", "1200")
        with mock.patch("hamdash.app.QrzLogbook.push_record",
                        side_effect=SyncError("QRZ refused it: Duplicate QSO")):
            self.run_job("qrz", "push", new_only=True, dry_run=False)
        self.assertEqual(self.pending("qrz"), 0)

    def test_a_real_refusal_does_not(self):
        self.log_one("W1AW", "20260101", "1200")
        with mock.patch("hamdash.app.QrzLogbook.push_record",
                        side_effect=SyncError("QRZ refused it: bad band")):
            self.run_job("qrz", "push", new_only=True, dry_run=False)
        self.assertEqual(self.pending("qrz"), 1)

    def test_a_sent_contact_is_not_sent_twice(self):
        self.log_one("W1AW", "20260101", "1200")
        with mock.patch("hamdash.app.QrzLogbook.push_record") as push:
            self.run_job("qrz", "push", new_only=True, dry_run=False)
            self.run_job("qrz", "push", new_only=True, dry_run=False)
        self.assertEqual(push.call_count, 1)


class TestWorldRadioLeagueIsAskedFirst(Host):
    """WRL keeps duplicates, so the far end cannot be left to dedupe."""

    def setUp(self):
        super().setUp()
        self.log_one("W1AW", "20260101", "1200", "20m", "SSB")
        self.log_one("K1JT", "20260102", "1300", "40m", "CW")
        self.log_one("JA0RU", "20260103", "0900", "15m", "FT8")

    def test_only_what_they_lack_is_sent(self):
        with mock.patch("hamdash.app.WrlLogbook.contacts",
                        return_value=iter(WRL_HAS)), \
             mock.patch("hamdash.app.WrlLogbook.push_record",
                        return_value={"ok": True, "duplicate": False}) as push:
            res = self.run_job("wrl", "push", new_only=True, dry_run=False)
        self.assertEqual(push.call_count, 1)
        self.assertEqual(push.call_args.args[0]["call"], "JA0RU")
        self.assertEqual((res["sent"], res["already"]), (1, 2))
        self.assertEqual(self.pending("wrl"), 0)

    def test_the_dry_run_count_leaves_them_out_too(self):
        """The count on the button is what misled in the first place."""
        with mock.patch("hamdash.app.WrlLogbook.contacts",
                        return_value=iter(WRL_HAS)), \
             mock.patch("hamdash.app.WrlLogbook.push_record") as push:
            res = self.run_job("wrl", "push")
        push.assert_not_called()
        self.assertEqual((res["would_send"], res["already"]), (1, 2))

    def test_a_second_push_sends_nothing_and_asks_nothing(self):
        with mock.patch("hamdash.app.WrlLogbook.contacts",
                        return_value=iter(WRL_HAS)), \
             mock.patch("hamdash.app.WrlLogbook.push_record",
                        return_value={"ok": True}):
            self.run_job("wrl", "push", new_only=True, dry_run=False)
        with mock.patch("hamdash.app.WrlLogbook.contacts") as asked, \
             mock.patch("hamdash.app.WrlLogbook.push_record") as push:
            self.run_job("wrl", "push", new_only=True, dry_run=False)
        push.assert_not_called()
        asked.assert_not_called()

    def test_if_their_list_cannot_be_read_nothing_is_sent(self):
        """Not knowing what is there is not permission to send it all."""
        with mock.patch("hamdash.app.WrlLogbook.contacts",
                        side_effect=SyncError("World Radio League: down")), \
             mock.patch("hamdash.app.WrlLogbook.push_record") as push:
            self.client.post("/api/log/sync/wrl/push",
                             json={"new_only": True, "dry_run": False})
            end = time.time() + 5
            while (self.client.get("/api/log/sync").get_json()
                   ["state"]["running"] and time.time() < end):
                time.sleep(0.01)
        push.assert_not_called()
        self.assertEqual(self.pending("wrl"), 3)


class TestAPullCountsContactsNotRecords(Host):
    """A WRL account holding 193 records of 87 QSOs pulled as "193 already
    had" - true of the records, and read as the log having grown."""

    def two_copies_of_one(self):
        # The second W1AW is the same contact a minute later: a duplicate
        # WRL stored, not a second QSO.
        return WRL_HAS + [dict(WRL_HAS[0], timestamp="2026-01-01T12:02:00Z")]

    def test_a_repeat_is_not_counted_as_a_contact(self):
        with mock.patch("hamdash.app.WrlLogbook.contacts",
                        return_value=iter(self.two_copies_of_one())):
            res = self.run_job("wrl", "pull")
        self.assertEqual((res["added"], res["skipped"], res["repeats"]),
                         (2, 0, 1))

    def test_the_message_says_where_the_extra_records_came_from(self):
        self.log_one("W1AW", "20260101", "1200", "20m", "SSB")
        self.log_one("K1JT", "20260102", "1300", "40m", "CW")
        with mock.patch("hamdash.app.WrlLogbook.contacts",
                        return_value=iter(self.two_copies_of_one())):
            msg = self.message_of("wrl", "pull")
        # Two contacts, both updated (WRL knows the frequency, the test
        # log does not) - and the third record said for what it is.
        self.assertEqual(msg, "0 new, 2 updated, 0 already had - World Radio "
                              "League sent 3 records, 1 of them extra copies "
                              "of the same QSOs")

    def test_no_repeats_no_extra_words(self):
        with mock.patch("hamdash.app.QrzLogbook.pull", return_value=QRZ_ADIF):
            msg = self.message_of("qrz", "pull")
        self.assertEqual(msg, "3 new, 0 updated, 0 already had")


class TestTheOldMarkerStillCounts(Host):
    """A config from before this change has `wrl_pushed_id` and no held
    rows. Rows at or below it were pushed then, and must not be again."""
    sync = {"wrl_pushed_id": 2}

    def test_rows_up_to_the_marker_are_held(self):
        self.log_one("W1AW", "20260101", "1200")
        self.log_one("K1JT", "20260102", "1300")
        self.log_one("JA0RU", "20260103", "0900")
        self.assertEqual(self.pending("wrl"), 1)


class TestTheLogbookItself(unittest.TestCase):
    def a_log(self):
        return Logbook(Path(tempfile.mkdtemp(prefix="hamdash-held-")) / "l.db")

    def test_an_import_marks_what_it_added_and_what_it_matched(self):
        log = self.a_log()
        mine = log.add({"call": "W1AW", "qso_date": "20260101",
                        "time_on": "1158", "band": "20m", "mode": "SSB"})
        log.import_adif(QRZ_ADIF, held_by="qrz")
        self.assertEqual(log.unheld("qrz"), [])
        self.assertNotIn(mine["id"], [r["id"] for r in log.unheld("qrz")])

    def test_an_import_without_a_service_marks_nothing(self):
        """A file dropped on the logbook came from nowhere in particular."""
        log = self.a_log()
        log.import_adif(QRZ_ADIF)
        self.assertEqual(len(log.unheld("qrz")), 3)

    def test_since_narrows_it(self):
        log = self.a_log()
        log.import_adif(QRZ_ADIF)
        self.assertEqual(len(log.unheld("qrz", since="20260102")), 2)

    def test_a_deleted_contact_leaves_nothing_behind(self):
        log = self.a_log()
        log.import_adif(QRZ_ADIF, held_by="qrz")
        first = log.recent(1)[0]
        log.delete(first["id"])
        with log._conn() as db:
            left = db.execute("SELECT COUNT(*) FROM held WHERE qso_id=?",
                              (first["id"],)).fetchone()[0]
        self.assertEqual(left, 0)


if __name__ == "__main__":
    unittest.main()
