"""Where a decoded FT8/FT4 station is: country, state or province, distance.

`plugins/decoder/where.py` is driven here with a fake lookup and a fake
station, so no lookup service is asked anything. What it pins:

* the best grid wins - the one sent on the air, then the last one heard
  from that call, then the lookup's, then the province's population centre,
  then the country's - and only the last two are marked approximate;
* a US state comes from the lookup, and the lookup is asked slowly: US
  calls only, once each, never for the mock's invented stations, and a
  failure is remembered rather than retried;
* the page's column headings line up with the columns under them.

None of this needs numpy, so it runs in CI's host-only job too.

Run with:  python -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "decoder"))

import where                                            # noqa: E402

from hamdash.services import dxcc                       # noqa: E402

HOME = {"grid": "CN89kg"}


class Asked:
    def __init__(self, answers=None, fail=False):
        self.calls, self.answers, self.fail = [], answers or {}, fail

    def __call__(self, call):
        self.calls.append(call)
        if self.fail:
            raise RuntimeError("callook: too many requests")
        return self.answers.get(call, {})


def places(lookup=None):
    return where.Places(lookup=lookup or Asked(), station=lambda: HOME,
                        start=False)


class TestProvinces(unittest.TestCase):
    def test_a_canadian_prefix_says_the_province(self):
        for call, code in (("VA7SLL", "BC"), ("VE3ABC", "ON"), ("VA2XYZ", "QC"),
                           ("VE9AA", "NB"), ("VY2ZZ", "PE"), ("VO1AA", "NL"),
                           ("VE7ABC/P", "BC")):
            with self.subTest(call=call):
                self.assertEqual(dxcc.province_for(call)[0], code)

    def test_a_us_call_does_not_pretend_to(self):
        self.assertEqual(dxcc.province_for("W7XYZ"), ("", ""))

    def test_every_province_has_a_rough_grid(self):
        codes = {code for code, _ in dxcc._CA_PROVINCES.values()}
        for code in codes:
            with self.subTest(code=code):
                self.assertTrue(dxcc.province_grid(code))


class TestWhichGridWins(unittest.TestCase):
    def test_sent_on_the_air_is_exact(self):
        w = places().place("VE3ABC", "FN03")
        self.assertEqual((w["grid"], w["approx"]), ("FN03", False))
        self.assertAlmostEqual(w["dist_km"], 3394.6, delta=1)

    def test_heard_before_is_remembered(self):
        p = places()
        p.heard_grid("VE7XYZ", "CN88")
        w = p.place("VE7XYZ")               # a report, no grid in it
        self.assertEqual((w["grid"], w["approx"]), ("CN88", False))

    def test_the_province_beats_the_middle_of_canada(self):
        """The country centre put a VE7 3,500 km from a VE7 listening."""
        w = places().place("VE7XYZ", may_look_up=False)
        self.assertTrue(w["approx"])
        self.assertLess(w["dist_km"], 100)

    def test_a_lookup_grid_is_used_unless_it_is_itself_a_guess(self):
        p = places(Asked({"W7XYZ": {"state": "WA", "locator": "CN87"}}))
        p.place("W7XYZ")
        p._lookup_one("W7XYZ")
        w = p.place("W7XYZ")
        self.assertEqual((w["state"], w["grid"], w["approx"]), ("WA", "CN87", False))
        q = places(Asked({"W7XYZ": {"state": "", "locator": "EM48",
                                    "approximate": True}}))
        q._lookup_one("W7XYZ")
        self.assertTrue(q.place("W7XYZ")["approx"])

    def test_nothing_known_is_the_country_marked_approximate(self):
        w = places().place("DL1ABC")
        self.assertEqual((w["country"], w["iso"]), ("Germany", "de"))
        self.assertTrue(w["approx"])

    def test_not_a_callsign_is_not_placed(self):
        p = places()
        for call in ("<...>", "CQ", "", "TNX"):
            with self.subTest(call=call):
                self.assertIsNone(p.place(call))


class TestTheLookupIsAskedSlowly(unittest.TestCase):
    def test_only_us_calls(self):
        asked = Asked()
        p = places(asked)
        for call in ("VE3ABC", "DL1ABC", "JA1XYZ"):
            self.assertFalse(p.place(call)["pending"])
        self.assertTrue(p._queue.empty())

    def test_once_each(self):
        p = places()
        for _ in range(5):
            self.assertTrue(p.place("K1ABC")["pending"])
        self.assertEqual(p._queue.qsize(), 1)

    def test_never_for_the_mock_s_invented_stations(self):
        p = places()
        self.assertFalse(p.place("K1ABC", may_look_up=False)["pending"])
        self.assertTrue(p._queue.empty())

    def test_a_failure_is_remembered_not_retried(self):
        asked = Asked(fail=True)
        p = places(asked)
        p.place("K1ABC")
        p._lookup_one(p._queue.get_nowait())
        w = p.place("K1ABC")
        self.assertFalse(w["pending"], "the row stops saying it is waiting")
        self.assertTrue(p._queue.empty(), "and it is not asked again")
        self.assertEqual(asked.calls, ["K1ABC"])

    def test_the_gap_between_lookups(self):
        self.assertGreaterEqual(where.LOOKUP_GAP, 1.0)


class TestTheOperatorsGrid(unittest.TestCase):
    def test_kept_for_a_while(self):
        now = [1000.0]
        seen = []

        def station():
            seen.append(1)
            return HOME

        p = where.Places(station=station, clock=lambda: now[0], start=False)
        p.station_grid()
        p.station_grid()
        self.assertEqual(len(seen), 1)
        now[0] += where.STATION_TTL + 1
        p.station_grid()
        self.assertEqual(len(seen), 2)

    def test_no_grid_means_no_distance(self):
        p = where.Places(station=lambda: {"grid": ""}, start=False)
        self.assertIsNone(p.place("VE3ABC", "FN03")["dist_km"])


class TestThePage(unittest.TestCase):
    PAGE = (ROOT / "plugins" / "decoder" / "index.html").read_text(encoding="utf-8")

    def test_the_headings(self):
        for head in ("UTC", "dB", "DT", "Hz", "Message", "Where", "Dist"):
            self.assertIn(f">{head}</span>", self.PAGE)

    def test_heading_and_rows_share_one_template_and_one_font_size(self):
        """Widths are in `ch`; an 11px heading over 13px rows put the "Hz"
        heading 14px left of its numbers."""
        self.assertIn(".cols, #out.slots .row {", self.PAGE)
        cols = self.PAGE[self.PAGE.index("  .cols { font-family"):]
        cols = cols[:cols.index("}")]
        self.assertIn("font-size: 13px", cols)
        self.assertIn("font-size: 13px", self.PAGE[self.PAGE.index("#out.slots {"):])

    def test_distance_follows_the_units(self):
        """hamdash.js fires it on document; a listener on window never
        heard it."""
        self.assertIn("document.addEventListener('hamdash:units'", self.PAGE)
        self.assertIn("HD.fmtDistance(", self.PAGE)


if __name__ == "__main__":
    unittest.main()
