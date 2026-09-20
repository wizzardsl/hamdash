"""PSK Reporter card: the cache, and reading their XML.

The cache is the part worth guarding. Philip Gladstone asks that data be
retrieved no more often than once every five minutes and says plainly that he
reserves the right to rate limit or block anyone who loads his system. A map
with a filter bar is a machine for making requests if you let it be, so the
floor is enforced in the card and not left to the page's good manners.

Nothing here touches pskreporter: every response is a recorded one.

Run with:  python -m unittest discover -s tests
"""
import os
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


# One real reply, trimmed: two reports, one each way, plus the trailing
# elements their API always sends.
SAMPLE = """<?xml version="1.0"?>
<receptionReports>
  <receptionReport receiverCallsign="VE7WCC" receiverLocator="CN89OF"
    senderCallsign="JO7KMB" senderLocator="QM09ng" frequency="18101796"
    flowStartSeconds="1789860176" mode="FT8" isSender="0" isReceiver="1"
    senderRegion="Iwate" senderDXCC="Japan" senderDXCCCode="JA"
    receiverRegion="British Columbia" receiverDXCC="Canada" sNR="-9" />
  <receptionReport receiverCallsign="AC1OR" receiverLocator="FN41ht54"
    senderCallsign="VE7ZAX" senderLocator="CN89kf" frequency="14076809"
    flowStartSeconds="1789860899" mode="FT8" isSender="1" isReceiver="0"
    senderDXCC="Canada" receiverDXCC="United States" sNR="-23" />
  <lastSequenceNumber value="72903109693"/>
  <maxFlowStartSeconds value="1789860165"/>
</receptionReports>"""

EMPTY = """<?xml version="1.0"?>
<receptionReports>
  <lastSequenceNumber value="1"/>
</receptionReports>"""


def a_reply(text=SAMPLE):
    r = mock.Mock(status_code=200, text=text)
    r.raise_for_status.return_value = None
    return r


class TestReadingTheirXml(unittest.TestCase):
    def setUp(self):
        self.f = psk.Fetcher()
        with mock.patch("card.requests.get", return_value=a_reply()):
            self.got = self.f.get("CN89", True, 1, "")

    def rows(self):
        return self.got["reports"]

    def test_both_reports_are_read(self):
        self.assertEqual(len(self.rows()), 2)

    def test_who_heard_whom(self):
        first = self.rows()[0]
        self.assertEqual(first["tx"], "JO7KMB")
        self.assertEqual(first["rx"], "VE7WCC")

    def test_the_side_comes_from_the_api_not_a_guess(self):
        """A grid query has no callsign to compare against, so isSender and
        isReceiver are the only honest answer to which way a report goes."""
        self.assertTrue(self.rows()[0]["is_rx"])
        self.assertFalse(self.rows()[0]["is_tx"])
        self.assertTrue(self.rows()[1]["is_tx"])

    def test_frequency_becomes_a_band(self):
        self.assertEqual(self.rows()[0]["band"], "17m")
        self.assertEqual(self.rows()[1]["band"], "20m")

    def test_signal_report_is_a_number(self):
        self.assertEqual(self.rows()[0]["snr"], -9)

    def test_country_and_region_carry_through_for_filtering(self):
        self.assertEqual(self.rows()[0]["tx_dxcc"], "Japan")
        self.assertEqual(self.rows()[0]["tx_region"], "Iwate")

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

    def test_an_empty_answer_is_not_an_error(self):
        f = psk.Fetcher()
        with mock.patch("card.requests.get", return_value=a_reply(EMPTY)):
            got = f.get("VA7SLL", False, 1, "")
        self.assertTrue(got["ok"])
        self.assertEqual(got["reports"], [])

    def test_something_that_is_not_their_xml_says_what_arrived(self):
        f = psk.Fetcher()
        with mock.patch("card.requests.get",
                        return_value=a_reply("<html>down for maintenance</html>")):
            got = f.get("VA7SLL", False, 1, "")
        self.assertFalse(got["ok"])
        self.assertIn("maintenance", got["error"])


class TestTheQuery(unittest.TestCase):
    def params(self, **kw):
        f = psk.Fetcher()
        with mock.patch("card.requests.get", return_value=a_reply()) as get:
            f.get(kw.pop("watch", "CN89"), kw.pop("as_grid", True),
                  kw.pop("hours", 1), kw.pop("mode", ""), kw.pop("contact", ""))
        return get.call_args.kwargs["params"]

    def test_a_grid_is_asked_for_as_a_grid(self):
        self.assertEqual(self.params()["modify"], "grid")

    def test_a_callsign_is_not(self):
        self.assertNotIn("modify", self.params(watch="VA7SLL", as_grid=False))

    def test_the_window_is_negative_seconds_as_their_api_wants(self):
        self.assertEqual(self.params(hours=3)["flowStartSeconds"], -10800)

    def test_the_window_is_capped_at_their_limit_of_a_day(self):
        self.assertEqual(self.params(hours=100)["flowStartSeconds"], -86400)

    def test_no_contact_address_is_sent_unless_one_was_given(self):
        """It is the operator's email. Empty means empty, not absent-ish."""
        self.assertNotIn("appcontact", self.params())
        self.assertNotIn("appcontact", self.params(contact="   "))

    def test_a_contact_address_is_sent_when_one_was_given(self):
        self.assertEqual(self.params(contact="me@example.com")["appcontact"],
                         "me@example.com")

    def test_it_identifies_itself(self):
        f = psk.Fetcher()
        with mock.patch("card.requests.get", return_value=a_reply()) as get:
            f.get("CN89", True, 1, "")
        ua = get.call_args.kwargs["headers"]["User-Agent"]
        self.assertIn("hamdash", ua)


class TestTheCache(unittest.TestCase):
    """The five minute floor, which is the whole reason this card has a
    backend at all."""

    def setUp(self):
        self.f = psk.Fetcher()

    def test_a_second_ask_does_not_reach_them(self):
        with mock.patch("card.requests.get", return_value=a_reply()) as get:
            for _ in range(10):
                self.f.get("CN89", True, 1, "")
        self.assertEqual(get.call_count, 1)

    def test_the_cached_answer_is_the_same_data(self):
        with mock.patch("card.requests.get", return_value=a_reply()):
            first = self.f.get("CN89", True, 1, "")
            again = self.f.get("CN89", True, 1, "")
        self.assertEqual(first["reports"], again["reports"])
        self.assertTrue(first["fresh"])
        self.assertFalse(again["fresh"])

    def test_forcing_cannot_jump_the_queue(self):
        """The Refresh button must not be a way round somebody else's rules."""
        with mock.patch("card.requests.get", return_value=a_reply()) as get:
            self.f.get("CN89", True, 1, "")
            self.f.get("CN89", True, 1, "", force=True)
        self.assertEqual(get.call_count, 1)

    def test_it_says_how_long_until_the_next_one_is_allowed(self):
        with mock.patch("card.requests.get", return_value=a_reply()):
            self.f.get("CN89", True, 1, "")
            got = self.f.get("CN89", True, 1, "")
        self.assertGreater(got["next_s"], 250)
        self.assertLessEqual(got["next_s"], psk.MIN_INTERVAL)

    def test_the_floor_lifts_once_it_has_passed(self):
        with mock.patch("card.requests.get", return_value=a_reply()) as get:
            self.f.get("CN89", True, 1, "")
            for entry in self.f._by_key.values():       # as if five minutes passed
                entry["at"] -= psk.MIN_INTERVAL + 1
            self.f.get("CN89", True, 1, "")
        self.assertEqual(get.call_count, 2)

    def test_a_different_query_is_a_different_cache_entry(self):
        with mock.patch("card.requests.get", return_value=a_reply()) as get:
            self.f.get("CN89", True, 1, "")
            self.f.get("VA7SLL", False, 1, "")
            self.f.get("CN89", True, 6, "")
        self.assertEqual(get.call_count, 3)

    def test_an_outage_keeps_showing_what_it_had(self):
        """Propagation data going stale is better than the map going blank."""
        with mock.patch("card.requests.get", return_value=a_reply()):
            self.f.get("CN89", True, 1, "")
        for entry in self.f._by_key.values():
            entry["at"] -= psk.MIN_INTERVAL + 1
        with mock.patch("card.requests.get",
                        side_effect=psk.requests.ConnectionError("no route")):
            got = self.f.get("CN89", True, 1, "")
        self.assertTrue(got["ok"])
        self.assertEqual(len(got["reports"]), 2)
        self.assertIn("no route", got["error"])

    def test_an_outage_with_nothing_cached_is_an_honest_failure(self):
        with mock.patch("card.requests.get",
                        side_effect=psk.requests.ConnectionError("no route")):
            got = self.f.get("CN89", True, 1, "")
        self.assertFalse(got["ok"])
        self.assertEqual(got["reports"], [])

    def test_it_does_not_grow_without_bound(self):
        with mock.patch("card.requests.get", return_value=a_reply()):
            for i in range(30):
                self.f.get(f"AA{i:02d}", False, 1, "")
        self.assertLessEqual(len(self.f._by_key), 12)


class TestTheCacheSurvivesARestart(unittest.TestCase):
    """In memory alone the cache lasts exactly as long as the card process.
    Restarting hamdash three times inside five minutes would then be three
    queries - which is exactly what the rule asks you not to do."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="hamdash-psk-"))
        self.addCleanup(shutil.rmtree, self.home, True)

    def a_card(self):
        """A fresh Card, as a restarted card process would build."""
        with mock.patch.dict(os.environ, {"HAMDASH_HOME": str(self.home),
                                          "HAMDASH_CARD_ID": "psk_reporter"}):
            return psk.Card(str(ROOT / "plugins" / "psk_reporter" / "card.py"))

    def test_a_restart_does_not_spend_a_query(self):
        with mock.patch("card.requests.get", return_value=a_reply()) as get:
            psk.Fetcher(store=self.a_card()).get("CN89", True, 1, "")
            # ...the process ends here, and a new one starts.
            again = psk.Fetcher(store=self.a_card()).get("CN89", True, 1, "")
        self.assertEqual(get.call_count, 1, "asked pskreporter again after a restart")
        self.assertEqual(len(again["reports"]), 2)
        self.assertFalse(again["fresh"])

    def test_the_wait_carries_over_too(self):
        """Not just the data: the clock. Otherwise the floor restarts with
        the process and the rule is only ever honoured within one run."""
        with mock.patch("card.requests.get", return_value=a_reply()):
            psk.Fetcher(store=self.a_card()).get("CN89", True, 1, "")
            again = psk.Fetcher(store=self.a_card()).get("CN89", True, 1, "")
        self.assertGreater(again["next_s"], 250)

    def test_something_stale_is_not_restored(self):
        f = psk.Fetcher(store=self.a_card())
        with mock.patch("card.requests.get", return_value=a_reply()):
            f.get("CN89", True, 1, "")
        for entry in f._by_key.values():
            entry["at"] -= psk.STALE_AFTER + 60
        f._save()
        with mock.patch("card.requests.get", return_value=a_reply()) as get:
            psk.Fetcher(store=self.a_card()).get("CN89", True, 1, "")
        self.assertEqual(get.call_count, 1)

    def test_a_clock_that_went_backwards_does_not_freeze_it(self):
        """An entry stamped in the future would otherwise look fresh for
        ever, and the card would never ask again."""
        f = psk.Fetcher(store=self.a_card())
        with mock.patch("card.requests.get", return_value=a_reply()):
            f.get("CN89", True, 1, "")
        for entry in f._by_key.values():
            entry["at"] = time.time() + 86400
        f._save()
        with mock.patch("card.requests.get", return_value=a_reply()) as get:
            psk.Fetcher(store=self.a_card()).get("CN89", True, 1, "")
        self.assertEqual(get.call_count, 1)

    def test_a_corrupt_cache_file_is_ignored_not_fatal(self):
        card_obj = self.a_card()
        (card_obj.store / psk.CACHE_FILE).write_text("{not json", encoding="utf-8")
        with mock.patch("card.requests.get", return_value=a_reply()):
            got = psk.Fetcher(store=card_obj).get("CN89", True, 1, "")
        self.assertTrue(got["ok"])

    def test_it_works_with_nowhere_to_write(self):
        """A card run by hand, with no host to tell it where home is."""
        with mock.patch("card.requests.get", return_value=a_reply()):
            got = psk.Fetcher().get("CN89", True, 1, "")
        self.assertTrue(got["ok"])


class TestBackingOff(unittest.TestCase):
    """A failure has to wait its turn as surely as a success does.

    The bug this guards was found by causing it: several queries in quick
    succession while developing earned a 429, and with nothing cached there
    was nothing holding the clock - so the page's own one-minute poll would
    have retried into the limit for as long as it was open. Against a rate
    limit, retrying is the thing that caused it.
    """

    def setUp(self):
        self.f = psk.Fetcher()

    def a_429(self, after="120"):
        r = mock.Mock(status_code=429, headers={"Retry-After": after})
        return r

    def test_a_rate_limit_is_not_retried_on_the_next_ask(self):
        with mock.patch("card.requests.get", return_value=self.a_429()) as get:
            for _ in range(10):
                self.f.get("CN89", True, 1, "")
        self.assertEqual(get.call_count, 1)

    def test_it_says_so_in_words_rather_than_as_an_http_error(self):
        with mock.patch("card.requests.get", return_value=self.a_429()):
            got = self.f.get("CN89", True, 1, "")
        self.assertFalse(got["ok"])
        self.assertIn("rate limiting", got["error"])
        self.assertIn("Nothing is lost", got["error"])

    def test_their_own_retry_after_is_honoured_when_longer(self):
        with mock.patch("card.requests.get", return_value=self.a_429("3600")):
            got = self.f.get("CN89", True, 1, "")
        self.assertGreater(got["next_s"], 3000)

    def test_an_ordinary_outage_also_waits(self):
        with mock.patch("card.requests.get",
                        side_effect=psk.requests.ConnectionError("no route")) as get:
            for _ in range(5):
                self.f.get("CN89", True, 1, "")
        self.assertEqual(get.call_count, 1)

    def test_a_failure_does_not_pretend_to_be_an_answer(self):
        with mock.patch("card.requests.get", return_value=self.a_429()):
            self.f.get("CN89", True, 1, "")
            again = self.f.get("CN89", True, 1, "")
        self.assertFalse(again["ok"], "a remembered failure read as success")
        self.assertEqual(again["reports"], [])

    def test_it_recovers_once_the_wait_is_over(self):
        with mock.patch("card.requests.get", return_value=self.a_429("1")):
            self.f.get("CN89", True, 1, "")
        for entry in self.f._by_key.values():
            entry["at"] -= psk.MIN_INTERVAL + 1
        with mock.patch("card.requests.get", return_value=a_reply()):
            got = self.f.get("CN89", True, 1, "")
        self.assertTrue(got["ok"])
        self.assertEqual(len(got["reports"]), 2)

    def test_data_already_held_survives_a_rate_limit(self):
        with mock.patch("card.requests.get", return_value=a_reply()):
            self.f.get("CN89", True, 1, "")
        for entry in self.f._by_key.values():
            entry["at"] -= psk.MIN_INTERVAL + 1
        with mock.patch("card.requests.get", return_value=self.a_429()):
            got = self.f.get("CN89", True, 1, "")
        self.assertTrue(got["ok"])
        self.assertEqual(len(got["reports"]), 2)
        self.assertIn("rate limiting", got["error"])


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


class TestTheModeIsAsked(unittest.TestCase):
    """The page says whether it means a grid or a callsign. Guessing from the
    shape still happens when nothing says - an older page, or the card run on
    its own - but a WA7 that looks a bit like a locator should never decide
    which question gets asked."""

    def test_explicit_grid_wins_over_the_shape(self):
        self.assertTrue(psk._looks_like_grid("CN89"))
        self.assertFalse(psk._looks_like_grid("VA7SLL"))

    def test_the_shape_test_is_strict_about_callsigns(self):
        for s in ("VE7A", "K1AB", "W1AW", "AB1CD"):
            with self.subTest(s=s):
                # Four characters, but the third and fourth are not both
                # digits, so it is not mistaken for a locator.
                self.assertFalse(psk._looks_like_grid(s))


class TestBands(unittest.TestCase):
    def test_the_ones_these_reports_actually_use(self):
        for mhz, band in [(14.074, "20m"), (7.074, "40m"), (18.1, "17m"),
                          (10.136, "30m"), (28.074, "10m"), (50.313, "6m"),
                          (0.4746, "630m"), (144.174, "2m")]:
            with self.subTest(mhz=mhz):
                self.assertEqual(psk._band(mhz), band)

    def test_something_off_band_is_blank_rather_than_wrong(self):
        self.assertEqual(psk._band(15.0), "")


if __name__ == "__main__":
    unittest.main()
