"""PSK Reporter card: the store, the incremental pull, and reading their XML.

The store is the part worth guarding. Philip Gladstone asks that data be
retrieved no more often than once every five minutes, says he reserves the
right to rate limit or block anyone who loads his system, and asks for "an
adaptive use of the retrieval mechanism". A map with a filter bar is a machine
for making requests if you let it be, so the floor lives in the card and the
pulls are incremental.

Nothing here touches pskreporter: every response is a recorded one.

Run with:  python -m unittest discover -s tests
"""
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "psk_reporter"))

import card as psk                                            # noqa: E402
import store as pskstore                                      # noqa: E402


def reports_xml(rows, seq="72903109693"):
    """Their reply shape: reports, then the high-water mark."""
    body = "".join(
        f'<receptionReport receiverCallsign="{r["rx"]}" '
        f'receiverLocator="{r.get("rx_grid", "")}" '
        f'senderCallsign="{r["tx"]}" senderLocator="{r.get("tx_grid", "")}" '
        f'frequency="{r.get("hz", 14074000)}" '
        f'flowStartSeconds="{r["at"]}" mode="{r.get("mode", "FT8")}" '
        f'isSender="{1 if r.get("is_tx") else 0}" '
        f'isReceiver="{1 if r.get("is_rx") else 0}" '
        f'senderDXCC="{r.get("tx_dxcc", "")}" '
        f'receiverDXCC="{r.get("rx_dxcc", "")}" '
        f'sNR="{r.get("snr", -10)}" />'
        for r in rows)
    tail = f'<lastSequenceNumber value="{seq}"/>' if seq else ""
    return (f'<?xml version="1.0"?><receptionReports>{body}{tail}'
            f'<maxFlowStartSeconds value="1789860165"/></receptionReports>')


def a_reply(rows=None, seq="72903109693", status=200):
    now = int(time.time())
    if rows is None:
        rows = [{"tx": "JO7KMB", "rx": "VE7WCC", "tx_grid": "QM09ng",
                 "rx_grid": "CN89OF", "hz": 18101796, "at": now - 120,
                 "is_rx": True, "tx_dxcc": "Japan", "rx_dxcc": "Canada",
                 "snr": -9},
                {"tx": "VE7ZAX", "rx": "AC1OR", "tx_grid": "CN89kf",
                 "rx_grid": "FN41ht54", "hz": 14076809, "at": now - 60,
                 "is_tx": True, "tx_dxcc": "Canada",
                 "rx_dxcc": "United States", "snr": -23}]
    r = mock.Mock(status_code=status, text=reports_xml(rows, seq), headers={})
    r.raise_for_status.return_value = None
    return r


class WithStore(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="hamdash-psk-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.store = pskstore.Store(self.home / "reports.sqlite3")
        self.f = psk.Fetcher(self.store)

    def fetch(self, reply=None, **kw):
        with mock.patch("card.requests.get",
                        return_value=reply if reply is not None else a_reply()) as get:
            self.got = self.f.get(kw.pop("watch", "CN89"),
                                  kw.pop("as_grid", True),
                                  kw.pop("window", 86400), **kw)
        self.get = get
        return self.got


class TestReadingTheirXml(WithStore):
    def setUp(self):
        super().setUp()
        self.fetch()

    def rows(self):
        return self.got["reports"]

    def test_both_reports_are_read(self):
        self.assertEqual(len(self.rows()), 2)

    def test_who_heard_whom(self):
        calls = {(r["tx"], r["rx"]) for r in self.rows()}
        self.assertIn(("JO7KMB", "VE7WCC"), calls)

    def test_the_side_comes_from_the_api_not_a_guess(self):
        """A grid query has no callsign to compare against, so isSender and
        isReceiver are the only honest answer to which way a report goes."""
        by_call = {r["tx"]: r for r in self.rows()}
        self.assertTrue(by_call["JO7KMB"]["is_rx"])
        self.assertFalse(by_call["JO7KMB"]["is_tx"])
        self.assertTrue(by_call["VE7ZAX"]["is_tx"])

    def test_frequency_becomes_a_band(self):
        bands = {r["tx"]: r["band"] for r in self.rows()}
        self.assertEqual(bands["JO7KMB"], "17m")
        self.assertEqual(bands["VE7ZAX"], "20m")

    def test_signal_report_survives_the_round_trip(self):
        snrs = {r["tx"]: r["snr"] for r in self.rows()}
        self.assertEqual(snrs["JO7KMB"], -9)

    def test_country_carries_through_for_filtering(self):
        by_call = {r["tx"]: r for r in self.rows()}
        self.assertEqual(by_call["JO7KMB"]["tx_dxcc"], "Japan")

    def test_a_missing_signal_report_is_none_not_zero(self):
        """Zero decibels is a real and rather good report."""
        el = mock.Mock(attrib={"senderCallsign": "A", "receiverCallsign": "B",
                               "frequency": "14074000", "flowStartSeconds": "1"})
        self.assertIsNone(psk._report(el)["snr"])

    def test_a_report_with_no_callsign_is_dropped(self):
        el = mock.Mock(attrib={"receiverCallsign": "B", "frequency": "1",
                               "flowStartSeconds": "1"})
        self.assertIsNone(psk._report(el))

    def test_rubbish_numbers_drop_the_report_rather_than_raising(self):
        el = mock.Mock(attrib={"senderCallsign": "A", "receiverCallsign": "B",
                               "frequency": "banana", "flowStartSeconds": "1"})
        self.assertIsNone(psk._report(el))

    def test_something_that_is_not_their_xml_says_what_arrived(self):
        r = mock.Mock(status_code=200, text="<html>down for maintenance</html>",
                      headers={})
        r.raise_for_status.return_value = None
        f = psk.Fetcher(pskstore.Store(self.home / "other.sqlite3"))
        with mock.patch("card.requests.get", return_value=r):
            got = f.get("VA7SLL", False, 86400)
        self.assertFalse(got["ok"])
        self.assertIn("maintenance", got["error"])


class TestTheIncrementalPull(WithStore):
    """`lastseqno` is their own mechanism for "only what is new", and their
    developer notes ask for it by name."""

    def params(self):
        return self.get.call_args.kwargs["params"]

    def test_the_first_pull_asks_for_a_window(self):
        self.fetch()
        self.assertIn("flowStartSeconds", self.params())
        self.assertNotIn("lastseqno", self.params())

    def test_the_next_pull_asks_only_for_what_is_new(self):
        self.fetch()
        self.store.mark("CN89", at=0)          # as if the floor had passed
        self.fetch(reply=a_reply(rows=[], seq="72903109999"))
        self.assertEqual(self.params().get("lastseqno"), "72903109693")
        self.assertNotIn("flowStartSeconds", self.params())

    def test_the_marker_advances(self):
        self.fetch()
        self.store.mark("CN89", at=0)
        self.fetch(reply=a_reply(rows=[], seq="72903109999"))
        self.assertEqual(self.store.state("CN89")["last_seq"], "72903109999")

    def test_a_reply_without_a_marker_does_not_reset_us(self):
        """Losing it would mean fetching the whole window again."""
        self.fetch()
        self.store.mark("CN89", at=0)
        self.fetch(reply=a_reply(rows=[], seq=""))
        self.assertEqual(self.store.state("CN89")["last_seq"], "72903109693")

    def test_reports_accumulate_rather_than_replace(self):
        """The point of the store: a window deeper than one reply can hold."""
        now = int(time.time())
        self.fetch(reply=a_reply(rows=[{"tx": "A1AA", "rx": "VE7WCC",
                                        "tx_grid": "AA00", "rx_grid": "CN89",
                                        "at": now - 300}]))
        self.store.mark("CN89", at=0)
        self.fetch(reply=a_reply(rows=[{"tx": "B2BB", "rx": "VE7WCC",
                                        "tx_grid": "BB11", "rx_grid": "CN89",
                                        "at": now - 60}], seq="999"))
        calls = {r["tx"] for r in self.got["reports"]}
        self.assertEqual(calls, {"A1AA", "B2BB"})

    def test_an_overlapping_pull_does_not_duplicate(self):
        """So correctness never depends on lastseqno being honoured: if it is
        ignored and the same reports arrive twice, they are still one map."""
        same = a_reply()                        # one reply, sent twice
        self.fetch(reply=same)
        self.store.mark("CN89", at=0)
        self.fetch(reply=same)
        self.assertEqual(len(self.got["reports"]), 2)

    def test_a_grid_is_asked_for_as_a_grid(self):
        self.fetch()
        self.assertEqual(self.params()["modify"], "grid")

    def test_a_callsign_is_not(self):
        self.fetch(watch="VA7SLL", as_grid=False)
        self.assertNotIn("modify", self.params())

    def test_no_contact_address_is_sent_unless_one_was_given(self):
        """It is the operator's email. Empty means empty, not absent-ish."""
        self.fetch()
        self.assertNotIn("appcontact", self.params())
        self.store.mark("CN89", at=0)
        self.fetch(contact="   ")
        self.assertNotIn("appcontact", self.params())

    def test_a_contact_address_is_sent_when_one_was_given(self):
        self.fetch(contact="me@example.com")
        self.assertEqual(self.params()["appcontact"], "me@example.com")

    def test_it_identifies_itself(self):
        self.fetch()
        ua = self.get.call_args.kwargs["headers"]["User-Agent"]
        self.assertIn("hamdash", ua)


class TestTheFloor(WithStore):
    """Five minutes per target, which is the whole reason for a backend."""

    def test_a_second_ask_does_not_reach_them(self):
        with mock.patch("card.requests.get", return_value=a_reply()) as get:
            for _ in range(10):
                self.f.get("CN89", True, 86400)
        self.assertEqual(get.call_count, 1)

    def test_forcing_cannot_jump_the_queue(self):
        """The Refresh button must not be a way round somebody else's rules."""
        with mock.patch("card.requests.get", return_value=a_reply()) as get:
            self.f.get("CN89", True, 86400)
            self.f.get("CN89", True, 86400, force=True)
        self.assertEqual(get.call_count, 1)

    def test_it_says_how_long_until_the_next_one_is_allowed(self):
        self.fetch()
        again = self.f.get("CN89", True, 86400)
        self.assertGreater(again["next_s"], 250)
        self.assertLessEqual(again["next_s"], psk.MIN_INTERVAL)

    def test_the_floor_lifts_once_it_has_passed(self):
        with mock.patch("card.requests.get", return_value=a_reply()) as get:
            self.f.get("CN89", True, 86400)
            self.store.mark("CN89", at=time.time() - psk.MIN_INTERVAL - 1)
            self.f.get("CN89", True, 86400)
        self.assertEqual(get.call_count, 2)

    def test_a_different_target_is_its_own_clock(self):
        with mock.patch("card.requests.get", return_value=a_reply()) as get:
            self.f.get("CN89", True, 86400)
            self.f.get("VA7SLL", False, 86400)
        self.assertEqual(get.call_count, 2)

    def test_the_window_is_never_a_reason_to_fetch(self):
        """It is a SELECT against the store now. Under the old shape a wider
        window was a different cache key and therefore a new request."""
        with mock.patch("card.requests.get", return_value=a_reply()) as get:
            for window in (900, 3600, 21600, 86400):
                self.f.get("CN89", True, window)
        self.assertEqual(get.call_count, 1)

    def test_the_window_actually_narrows_what_comes_back(self):
        now = int(time.time())
        self.fetch(reply=a_reply(rows=[
            {"tx": "OLD", "rx": "VE7WCC", "tx_grid": "AA00",
             "rx_grid": "CN89", "at": now - 7200},
            {"tx": "NEW", "rx": "VE7WCC", "tx_grid": "BB11",
             "rx_grid": "CN89", "at": now - 60}]))
        wide = {r["tx"] for r in self.f.get("CN89", True, 86400)["reports"]}
        narrow = {r["tx"] for r in self.f.get("CN89", True, 900)["reports"]}
        self.assertEqual(wide, {"OLD", "NEW"})
        self.assertEqual(narrow, {"NEW"})


class TestBackingOff(WithStore):
    """A failure has to wait its turn as surely as a success does.

    Found by causing it: several queries in quick succession while developing
    earned a 429, and with nothing holding the clock the page's own poll would
    have retried into the limit for as long as the tile was open.
    """

    def a_429(self, after="120"):
        return mock.Mock(status_code=429, headers={"Retry-After": after})

    def test_a_rate_limit_is_not_retried_on_the_next_ask(self):
        with mock.patch("card.requests.get", return_value=self.a_429()) as get:
            for _ in range(10):
                self.f.get("CN89", True, 86400)
        self.assertEqual(get.call_count, 1)

    def test_it_says_so_in_words_rather_than_as_an_http_error(self):
        with mock.patch("card.requests.get", return_value=self.a_429()):
            got = self.f.get("CN89", True, 86400)
        self.assertFalse(got["ok"])
        self.assertIn("rate limiting", got["error"])
        self.assertIn("Nothing is lost", got["error"])

    def test_their_own_retry_after_is_honoured_when_longer(self):
        with mock.patch("card.requests.get", return_value=self.a_429("3600")):
            got = self.f.get("CN89", True, 86400)
        self.assertGreater(got["next_s"], 3000)

    def test_an_ordinary_outage_also_waits(self):
        with mock.patch("card.requests.get",
                        side_effect=psk.requests.ConnectionError("no route")) as get:
            for _ in range(5):
                self.f.get("CN89", True, 86400)
        self.assertEqual(get.call_count, 1)

    def test_what_is_already_held_survives_an_outage(self):
        """Propagation data going stale beats the map going blank."""
        self.fetch()
        self.store.mark("CN89", at=time.time() - psk.MIN_INTERVAL - 1)
        with mock.patch("card.requests.get",
                        side_effect=psk.requests.ConnectionError("no route")):
            got = self.f.get("CN89", True, 86400)
        self.assertTrue(got["ok"], "an outage hid reports we already had")
        self.assertEqual(len(got["reports"]), 2)
        self.assertIn("no route", got["error"])

    def test_it_recovers_once_the_wait_is_over(self):
        with mock.patch("card.requests.get", return_value=self.a_429("1")):
            self.f.get("CN89", True, 86400)
        self.store.mark("CN89", at=time.time() - psk.MIN_INTERVAL - 1)
        with mock.patch("card.requests.get", return_value=a_reply()):
            got = self.f.get("CN89", True, 86400)
        self.assertTrue(got["ok"])
        self.assertEqual(len(got["reports"]), 2)


class TestAcrossRestarts(WithStore):
    """The store is a file. A restart must not spend a query."""

    def test_a_restart_does_not_reach_them(self):
        with mock.patch("card.requests.get", return_value=a_reply()) as get:
            psk.Fetcher(pskstore.Store(self.home / "reports.sqlite3")) \
                .get("CN89", True, 86400)
            # ...the process ends here, and a new one starts on the same file.
            again = psk.Fetcher(pskstore.Store(self.home / "reports.sqlite3")) \
                .get("CN89", True, 86400)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(len(again["reports"]), 2)

    def test_the_wait_carries_over_too(self):
        """Not just the data: the clock. Otherwise the floor restarts with the
        process and the rule is only ever honoured within one run."""
        with mock.patch("card.requests.get", return_value=a_reply()):
            self.f.get("CN89", True, 86400)
            again = psk.Fetcher(pskstore.Store(self.home / "reports.sqlite3")) \
                .get("CN89", True, 86400)
        self.assertGreater(again["next_s"], 250)

    def test_the_sequence_marker_carries_over(self):
        """So the first pull after a restart is incremental, not a refetch."""
        with mock.patch("card.requests.get", return_value=a_reply()):
            self.f.get("CN89", True, 86400)
        fresh = pskstore.Store(self.home / "reports.sqlite3")
        self.assertEqual(fresh.state("CN89")["last_seq"], "72903109693")


class TestTheStore(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="hamdash-pskstore-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.st = pskstore.Store(self.home / "s.sqlite3")
        self.now = int(time.time())

    def a_row(self, **over):
        row = {"tx": "JA1AA", "rx": "VE7BB", "tx_grid": "PM95",
               "rx_grid": "CN89", "hz": 14074000, "mode": "FT8", "snr": -9,
               "at": self.now - 60, "is_tx": False, "is_rx": True,
               "tx_dxcc": "Japan", "rx_dxcc": "Canada",
               "tx_region": "", "rx_region": ""}
        row.update(over)
        return row

    def test_a_report_round_trips(self):
        self.st.add("CN89", [self.a_row()])
        got = self.st.since("CN89", 3600)[0]
        self.assertEqual(got["tx"], "JA1AA")
        self.assertEqual(got["band"], "20m")
        self.assertEqual(got["snr"], -9)
        self.assertTrue(got["is_rx"])
        self.assertFalse(got["is_tx"])

    def test_the_same_report_twice_is_one_row(self):
        self.st.add("CN89", [self.a_row()])
        self.assertEqual(self.st.add("CN89", [self.a_row()]), 0)
        self.assertEqual(self.st.count("CN89"), 1)

    def test_two_targets_do_not_mix(self):
        self.st.add("CN89", [self.a_row()])
        self.st.add("PM95", [self.a_row(tx="W1AW")])
        self.assertEqual(self.st.count("CN89"), 1)
        self.assertEqual([r["tx"] for r in self.st.since("PM95", 3600)],
                         ["W1AW"])

    def test_band_is_derived_not_stored(self):
        """A stored copy is only a chance to disagree with the frequency."""
        self.st.add("CN89", [self.a_row(hz=7074000)])
        self.assertEqual(self.st.since("CN89", 3600)[0]["band"], "40m")

    def test_pruning_drops_what_their_api_could_not_replace(self):
        self.st.add("CN89", [self.a_row(),
                             self.a_row(tx="OLD", at=self.now - 90000)])
        self.assertEqual(self.st.prune(), 1)
        self.assertEqual([r["tx"] for r in self.st.since("CN89", 86400 * 2)],
                         ["JA1AA"])

    def test_the_window_is_a_window(self):
        self.st.add("CN89", [self.a_row(tx="RECENT", at=self.now - 60),
                             self.a_row(tx="EARLIER", at=self.now - 7200)])
        self.assertEqual([r["tx"] for r in self.st.since("CN89", 900)],
                         ["RECENT"])

    def test_newest_first(self):
        self.st.add("CN89", [self.a_row(tx="OLDER", at=self.now - 600),
                             self.a_row(tx="NEWER", at=self.now - 60)])
        self.assertEqual([r["tx"] for r in self.st.since("CN89", 3600)],
                         ["NEWER", "OLDER"])

    def test_oldest_says_how_far_back_it_reaches(self):
        self.st.add("CN89", [self.a_row(at=self.now - 3600)])
        self.assertAlmostEqual(self.st.oldest("CN89"), self.now - 3600, delta=2)

    def test_the_marker_survives_and_is_per_target(self):
        self.st.mark("CN89", last_seq="123", at=1.0)
        self.st.mark("PM95", last_seq="456", at=2.0)
        fresh = pskstore.Store(self.home / "s.sqlite3")
        self.assertEqual(fresh.state("CN89")["last_seq"], "123")
        self.assertEqual(fresh.state("PM95")["last_seq"], "456")

    def test_marking_one_field_leaves_the_others(self):
        self.st.mark("CN89", last_seq="123", at=1.0)
        self.st.mark("CN89", at=2.0)
        self.assertEqual(self.st.state("CN89")["last_seq"], "123")

    def test_an_unknown_target_is_empty_not_an_error(self):
        self.assertEqual(self.st.state("NOWHERE"), {})
        self.assertEqual(self.st.since("NOWHERE", 3600), [])


class TestGridOrCallsign(unittest.TestCase):
    """Getting this wrong asks the wrong question, so it is strict: anything
    not clearly a locator is treated as a callsign."""

    def test_grids(self):
        for s in ("CN89", "cn89", "FN42hn", "JJ00aa"):
            with self.subTest(s=s):
                self.assertTrue(psk._looks_like_grid(s))

    def test_callsigns(self):
        for s in ("VA7SLL", "W1AW", "JO7KMB", "VE7", "K1A", "VA7SLL/P", ""):
            with self.subTest(s=s):
                self.assertFalse(psk._looks_like_grid(s))


class TestBands(unittest.TestCase):
    def test_the_ones_these_reports_actually_use(self):
        for mhz, band in [(14.074, "20m"), (7.074, "40m"), (18.1, "17m"),
                          (10.136, "30m"), (28.074, "10m"), (50.313, "6m"),
                          (0.4746, "630m"), (144.174, "2m")]:
            with self.subTest(mhz=mhz):
                self.assertEqual(pskstore.band_for(mhz), band)

    def test_something_off_band_is_blank_rather_than_wrong(self):
        self.assertEqual(pskstore.band_for(15.0), "")


if __name__ == "__main__":
    unittest.main()
