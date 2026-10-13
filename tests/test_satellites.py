"""Reading two-line elements, and not pestering Celestrak for them.

The orbital arithmetic is SGP4 and it happens in the browser, where the
clock is. What is tested here is the half that would otherwise go wrong
quietly: parsing a text format that has no delimiters to speak of, and
keeping to a rate floor on somebody else's server.

The parser matters because **Celestrak serves an error page with a 200**.
A run of prose read as elements does not raise, it produces satellites
with nonsense orbits that propagate to somewhere - and a dot on a map is
a dot on a map whether or not it means anything.

Run with:  python -m unittest discover -s tests
"""
import importlib.util
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

CARD = Path(__file__).resolve().parent.parent / "plugins" / "satellites"


def load():
    """By path, under a name of its own.

    Every `plugins/*/card.py` shares the module name `card`, so a plain
    import gets whichever test ran first. The SDK is stubbed because
    importing the real one ends in `card.run()`, which binds a port and
    blocks.
    """
    sdk = types.ModuleType("hamdash.sdk")

    class FakeCard:
        def __init__(self, *a, **kw):
            # A temp dir, not one inside tests/: the card writes its
            # element cache here and a test must not leave files in the
            # tree it is testing.
            self.store = Path(tempfile.mkdtemp(prefix='hamdash-sat-'))

        def get(self, *a, **kw):
            return lambda fn: fn

        def settings(self, *a, **kw):
            return {}

        def run(self):
            pass

    sdk.Card = FakeCard
    with mock.patch.dict(sys.modules, {"hamdash.sdk": sdk}):
        spec = importlib.util.spec_from_file_location(
            "satellites_card", CARD / "card.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


sat = load()


def tearDownModule():
    shutil.rmtree(sat.card.store, ignore_errors=True)

REAL = """OSCAR 7 (AO-7)
1 07530U 74089B   26285.99683185 -.00000045  00000+0  68016-5 0  9990
2 07530 101.9903 300.4707 0011942 266.5495 121.7346 12.53700018375281
ISS (ZARYA)
1 25544U 98067A   26286.50000000  .00016717  00000+0  10270-3 0  9000
2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391 56353
"""


class TestReadingElements(unittest.TestCase):
    def test_a_normal_file_gives_one_entry_per_satellite(self):
        got = sat._parse(REAL)
        self.assertEqual([s["name"] for s in got],
                         ["OSCAR 7 (AO-7)", "ISS (ZARYA)"])
        self.assertTrue(got[0]["tle1"].startswith("1 07530"))
        self.assertTrue(got[0]["tle2"].startswith("2 07530"))

    def test_blank_lines_and_trailing_whitespace_do_not_shift_it(self):
        messy = REAL.replace("\n", "   \n").replace("(AO-7)", "(AO-7)\n\n")
        self.assertEqual(len(sat._parse(messy)), 2)

    def test_an_error_page_yields_no_satellites(self):
        """Celestrak answers 200 with prose when it is unhappy. Read as
        elements that is satellites with nonsense orbits, which propagate
        to somewhere and draw a dot like any other."""
        self.assertEqual(sat._parse(
            "<html><body>Invalid query or too many requests</body></html>"), [])
        self.assertEqual(sat._parse("Service temporarily unavailable.\n"), [])

    def test_a_truncated_file_keeps_the_whole_entries(self):
        """A download cut short must not produce half a satellite."""
        cut = "\n".join(REAL.splitlines()[:5])      # ISS loses its line 2
        got = sat._parse(cut)
        self.assertEqual([s["name"] for s in got], ["OSCAR 7 (AO-7)"])

    def test_a_name_that_looks_like_a_line_number_is_not_one(self):
        """Element lines are `1 ` / `2 ` plus a five-digit catalogue
        number; a satellite called "2 LINE TEST" is not line 2."""
        odd = "2 LINE TEST\n" + "\n".join(REAL.splitlines()[1:3])
        got = sat._parse(odd)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["name"], "2 LINE TEST")


class TestItKeepsToTheFloor(unittest.TestCase):
    """Elements are published a few times a day. A tile reloads all day."""

    def setUp(self):
        sat._sets.clear()
        for f in sat.card.store.glob("tle-*.json"):
            f.unlink()
        self.addCleanup(sat._sets.clear)

    def reply(self, text=REAL, status=200):
        r = mock.Mock()
        r.text = text
        r.raise_for_status = mock.Mock()
        return r

    def test_a_second_ask_does_not_reach_celestrak(self):
        with mock.patch.object(sat.requests, "get",
                               return_value=self.reply()) as get:
            sat.elements("amateur")
            sat.elements("amateur")
            self.assertEqual(get.call_count, 1)

    def test_the_floor_is_hours_not_minutes(self):
        self.assertGreaterEqual(sat.FLOOR, 3600)

    def test_a_restart_does_not_mean_a_fresh_request(self):
        """The cache is on disk for exactly this: "no more than once every
        six hours" is not honoured by restarting."""
        with mock.patch.object(sat.requests, "get",
                               return_value=self.reply()) as get:
            sat.elements("amateur")
            self.assertEqual(get.call_count, 1)
        sat._sets.clear()                      # as if the process restarted
        with mock.patch.object(sat.requests, "get",
                               return_value=self.reply()) as get:
            d = sat.elements("amateur")
            self.assertEqual(get.call_count, 0, "went back to Celestrak")
            self.assertEqual(len(d["satellites"]), 2)

    def test_a_failure_serves_what_it_had(self):
        """An outage must not empty the map."""
        with mock.patch.object(sat.requests, "get",
                               return_value=self.reply()):
            sat.elements("amateur")
        sat._sets["amateur"]["at"] -= sat.FLOOR + 1        # due a refresh
        with mock.patch.object(sat.requests, "get",
                               side_effect=sat.requests.ConnectionError("x")):
            d = sat.elements("amateur")
        self.assertEqual(len(d["satellites"]), 2)
        self.assertTrue(d["stale"])
        self.assertIn("could not refresh", d["error"])

    def test_a_200_full_of_prose_counts_as_a_failure(self):
        """Otherwise the good elements are replaced by nothing at all."""
        with mock.patch.object(sat.requests, "get",
                               return_value=self.reply()):
            sat.elements("amateur")
        sat._sets["amateur"]["at"] -= sat.FLOOR + 1
        with mock.patch.object(sat.requests, "get",
                               return_value=self.reply(text="<html>no</html>")):
            d = sat.elements("amateur")
        self.assertEqual(len(d["satellites"]), 2, "kept the good set")
        self.assertTrue(d["stale"])

    def test_a_refusal_is_not_retried_on_the_next_request(self):
        """A page polling every minute would otherwise retry straight into
        whatever just refused it."""
        with mock.patch.object(sat.requests, "get",
                               side_effect=sat.requests.ConnectionError("x")) as get:
            sat.elements("amateur")
            sat.elements("amateur")
            self.assertEqual(get.call_count, 1)

    def test_an_unknown_group_falls_back_rather_than_asking_for_it(self):
        with mock.patch.object(sat.requests, "get",
                               return_value=self.reply()) as get:
            sat.elements("../../etc/passwd")
            self.assertIn("GROUP=amateur", get.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
