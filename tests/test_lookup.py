"""Callsign lookup: which provider gets asked, and what is said when none can
answer.

The thing worth guarding is the honesty of the result. The prefix table
answers with the middle of a country, so a caller that cannot tell a real
locator from a fallback will happily set a station grid hundreds of miles
wrong and make every distance it ever reports wrong with it.

Run with:  python -m unittest discover -s tests
"""
import unittest
from unittest import mock

import requests

from hamdash.services.callsign import Lookup, Refused, links_for


def offline_lookup(**kw) -> Lookup:
    """A Lookup with no cache, so nothing here touches disk."""
    return Lookup(cache_path=None, **kw)


class TestChain(unittest.TestCase):
    def test_auto_needs_no_account(self):
        """The point of `auto`: a fresh install looks things up for free."""
        self.assertEqual(offline_lookup(provider="auto")._chain(),
                         ["callook", "hamdb"])

    def test_auto_adds_an_account_provider_when_there_are_credentials(self):
        c = offline_lookup(provider="auto", user="me", password="pw")._chain()
        self.assertEqual(c, ["callook", "hamdb", "hamqth"])

    def test_auto_does_not_reach_for_qrz_unasked(self):
        """QRZ only works with a paid subscription, so it is opt-in."""
        self.assertNotIn(
            "qrz", offline_lookup(provider="auto", user="me", password="pw")._chain())

    def test_naming_a_provider_uses_only_that_one(self):
        for name in ("callook", "hamdb", "hamqth", "qrz", "offline"):
            with self.subTest(provider=name):
                self.assertEqual(offline_lookup(provider=name)._chain(), [name])


class TestFallback(unittest.TestCase):
    def test_nothing_answers_gives_an_approximate_result_and_says_so(self):
        lk = offline_lookup(provider="auto")
        with mock.patch.object(lk, "_callook", return_value=None), \
             mock.patch.object(lk, "_hamdb", return_value=None):
            r = lk.lookup("G3TXF")
        self.assertTrue(r["approximate"])
        self.assertEqual(r["source"], "prefix")
        self.assertIn("callook, hamdb", r["note"])

    def test_a_refusal_is_reported_not_swallowed(self):
        """'your subscription lapsed' must not turn into a silent guess."""
        lk = offline_lookup(provider="qrz", user="me", password="pw")
        with mock.patch.object(lk, "_qrz",
                               side_effect=Refused("QRZ: not subscribed")):
            r = lk.lookup("W1AW")
        self.assertEqual(r["note"], "QRZ: not subscribed")
        self.assertTrue(r["approximate"])

    def test_one_provider_down_does_not_stop_the_next(self):
        lk = offline_lookup(provider="auto")
        good = {"ok": True, "callsign": "W1AW", "locator": "FN31PR",
                "source": "hamdb", "approximate": False}
        with mock.patch.object(lk, "_callook",
                               side_effect=requests.ConnectionError("down")), \
             mock.patch.object(lk, "_hamdb", return_value=good):
            r = lk.lookup("W1AW")
        self.assertEqual(r["source"], "hamdb")
        self.assertFalse(r["approximate"])
        self.assertIsNone(r.get("note"))

    def test_qrz_without_credentials_says_what_to_do(self):
        r = offline_lookup(provider="qrz").lookup("W1AW")
        self.assertIn("username and password", r["note"])
        self.assertIn("Settings", r["note"])

    def test_no_callsign(self):
        self.assertFalse(offline_lookup(provider="auto").lookup("")["ok"])


class TestLinks(unittest.TestCase):
    def test_public_pages(self):
        links = links_for("w1aw")
        self.assertEqual(links["qrz"], "https://www.qrz.com/db/W1AW")
        self.assertEqual(links["hamqth"], "https://www.hamqth.com/W1AW")
        self.assertEqual(links["callook"], "https://callook.info/W1AW")

    def test_no_lotw_link(self):
        """LoTW has no public per-callsign page, only a login. A link there
        would go nowhere useful, so there isn't one."""
        self.assertNotIn("lotw", links_for("W1AW"))

    def test_empty(self):
        self.assertEqual(links_for(""), {})

    def test_results_carry_links(self):
        lk = offline_lookup(provider="offline")
        self.assertIn("qrz", lk.lookup("W1AW")["links"])



class TestGridlessAnswers(unittest.TestCase):
    """callook answers for some US callsigns with a name and no gridsquare.

    That is a real answer, but it cannot put anyone on a map, so it must not
    end the chain while a provider that does know the grid is still untried.
    """

    def setUp(self):
        self.thin = {"ok": True, "callsign": "K1JT", "locator": "",
                     "country": "United States", "source": "callook",
                     "approximate": False}
        self.full = {"ok": True, "callsign": "K1JT", "locator": "FN20QI",
                     "source": "hamdb", "approximate": False}

    def test_chain_continues_past_an_answer_with_no_grid(self):
        lk = offline_lookup(provider="auto")
        with mock.patch.object(lk, "_callook", return_value=self.thin), \
             mock.patch.object(lk, "_hamdb", return_value=self.full):
            r = lk.lookup("K1JT")
        self.assertEqual(r["source"], "hamdb")
        self.assertEqual(r["locator"], "FN20QI")

    def test_it_is_still_used_when_nothing_better_turns_up(self):
        """A name and a country beat the prefix table's guess."""
        lk = offline_lookup(provider="auto")
        with mock.patch.object(lk, "_callook", return_value=self.thin), \
             mock.patch.object(lk, "_hamdb", return_value=None):
            r = lk.lookup("K1JT")
        self.assertEqual(r["source"], "callook")
        self.assertFalse(r["approximate"])

    def test_naming_one_provider_still_means_only_that_one(self):
        lk = offline_lookup(provider="callook")
        with mock.patch.object(lk, "_callook", return_value=self.thin), \
             mock.patch.object(lk, "_hamdb", return_value=self.full) as hamdb:
            lk.lookup("K1JT")
        hamdb.assert_not_called()

if __name__ == "__main__":
    unittest.main()
