"""Nets in progress, from NetLogger, for the Spots card's Nets tab.

The fixture is a real reply from https://www.netlogger.org/api/GetActiveNets.php,
taken on 2026-10-23 - not one built here, because a parser tested only against
XML its author wrote tests the author's guess about the format (the
`bandsBetween` lesson in CLAUDE.md). It carries the mess that made the
frequency rule necessary: "147.090" (MHz), "3916" (kHz), "3.919" (MHz) and
"2m" (a band, no number) in one reply.

Run with:  python -m unittest discover -s tests
"""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent / "plugins" / "spots"
sys.path.insert(0, str(HERE))

import sources                                          # noqa: E402

REAL = (Path(__file__).resolve().parent / "fixtures" / "netlogger_active.xml").read_bytes()

EMPTY = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
<NetLoggerXML><Header><TimeZone>UTC</TimeZone></Header>
<ServerList><ResponseCode>404 Not Found</ResponseCode></ServerList>
<Error>Query returned an empty result</Error></NetLoggerXML>"""

REFUSED = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
<NetLoggerXML><ServerList><ResponseCode>429 Too Many Requests</ResponseCode>
</ServerList><Error>Slow down</Error></NetLoggerXML>"""


class TestTheRealReply(unittest.TestCase):
    def setUp(self):
        self.nets = sources.nets(REAL)
        self.by = {n["name"]: n for n in self.nets}

    def test_every_net_is_read(self):
        self.assertEqual(len(self.nets), 8)

    def test_megahertz_kilohertz_and_megahertz_again(self):
        self.assertEqual(self.by["BCARC ARES"]["hz"], 147_090_000)        # "147.090"
        self.assertEqual(self.by["FREEWHEELERS"]["hz"], 3_916_000)        # "3916"
        self.assertEqual(self.by["Friendly Bunch Conversation"]["hz"], 3_919_000)

    def test_a_band_where_the_frequency_goes_is_not_tuned_to(self):
        """ "2m" read as a number is 2 MHz - a click would put the radio on
        nothing at all, somewhere the card made up."""
        n = self.by["The Fusion Net"]
        self.assertEqual(n["freq"], "2m", "what they wrote is kept, to show")
        self.assertEqual(n["hz"], 0)

    def test_what_a_net_is(self):
        n = self.by["FREEWHEELERS"]
        self.assertEqual((n["band"], n["mode"], n["control"]), ("80m", "SSB", "KI4BIY"))
        self.assertEqual(n["subscribers"], 3)
        self.assertEqual(n["server"], "NETLOGGER")

    def test_start_times_are_utc(self):
        """Read as local time, every net would start hours in the future."""
        import datetime as dt
        n = self.by["FREEWHEELERS"]                # 2026-10-23 02:58:59
        want = dt.datetime(2026, 10, 23, 2, 58, 59, tzinfo=dt.timezone.utc).timestamp()
        self.assertEqual(n["started"], want)

    def test_newest_first(self):
        starts = [n["started"] for n in self.nets]
        self.assertEqual(starts, sorted(starts, reverse=True))


class TestTheFrequencyRule(unittest.TestCase):
    def test_by_size(self):
        for text, hz in (("147.090", 147_090_000), ("3916", 3_916_000),
                         ("14300000", 14_300_000), ("7.188 LSB", 7_188_000),
                         ("3.919 MHz", 3_919_000)):
            with self.subTest(text=text):
                self.assertEqual(sources.net_hz(text), hz)

    def test_the_nets_own_band_breaks_a_tie(self):
        """1296 by size is kilohertz - 1.296 MHz, no band at all. Their Band
        field says 23cm, which only the megahertz reading agrees with."""
        self.assertEqual(sources.net_hz("1296", "23cm"), 1_296_000_000)
        self.assertEqual(sources.net_hz("3916", "80m"), 3_916_000)

    def test_bands_and_nothing_are_no_frequency(self):
        for text in ("2m", "70cm", "40 meters", "", None, "Allstar", "0"):
            with self.subTest(text=text):
                self.assertEqual(sources.net_hz(text), 0)


class TestWhatNetLoggerSaysOtherwise(unittest.TestCase):
    def test_no_nets_on_is_an_answer_not_a_failure(self):
        self.assertEqual(sources.nets(EMPTY), [])

    def test_any_other_refusal_is_a_failure(self):
        """So the feed backs off and keeps the list it had, rather than
        replacing it with nothing."""
        with self.assertRaises(ValueError) as got:
            sources.nets(REFUSED)
        self.assertIn("429", str(got.exception))

    def test_it_says_who_is_asking(self):
        fake = mock.Mock(content=REAL)
        fake.raise_for_status = mock.Mock()
        with mock.patch.object(sources.requests, "get", return_value=fake) as get:
            sources.nets()
        url = get.call_args.args[0]
        self.assertEqual(url, "https://www.netlogger.org/api/GetActiveNets.php")
        self.assertIn("hamdash", get.call_args.kwargs["headers"]["User-Agent"])
        self.assertNotIn("APIKey", str(get.call_args), "this needs no key")


class TestTheCard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("spots_card_nets", HERE / "card.py")
        cls.card = importlib.util.module_from_spec(spec)
        sys.modules["spots_card_nets"] = cls.card
        spec.loader.exec_module(cls.card)

    def test_netlogger_is_asked_once_a_minute_at_most(self):
        self.assertGreaterEqual(self.card.NETS_FLOOR, 60)
        self.assertEqual(self.card._nets.floor, self.card.NETS_FLOOR)

    def test_the_page_asks_only_while_the_nets_tab_is_open(self):
        page = (HERE / "index.html").read_text(encoding="utf-8")
        at = page.index("async function loadNets(force)")
        body = page[at:page.index("\n}\n", at)]
        self.assertIn("tab !== 'nets'", body)
        # A click on a net must not announce some spot row as picked.
        self.assertIn("tab === 'spots' ? visible()", page)
        # Whose list it is, on screen.
        self.assertIn("https://www.netlogger.org/", page)


if __name__ == "__main__":
    unittest.main()
