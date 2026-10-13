"""The solar numbers on the rail: SFI, sunspot number, A, K, X-ray class.

Three separate NOAA files, and the point of the tests is that **any one of
them may fail without taking the others down**. A rail showing three numbers
and a gap is more use than one showing an error where four numbers were, and
that is not the behaviour you get for free.

The parsing has its own traps: the daily table marks a missing value as -999
or `*`, and the row for today may be there with nothing in it yet. Reading
-999 as a sunspot number puts "SSN -999" in front of the operator, which is
worse than a dash because it looks like a measurement.

Run with:  python -m unittest discover -s tests
"""
import unittest
from unittest import mock

import requests

from hamdash.services import space


DSD = """:Product: Daily Solar Data
#                 Radio  SESC     Sunspot
# Date            Flux   Sunspot  Area
2026 10 11  122     94      670      1    -999      *   4  0  0  3  0  0  0
2026 10 12  123     93      650      2    -999      *   4  0  0  0  0  0  0
"""

KP = [
    {"time_tag": "2026-10-12T21:00:00", "Kp": 1.33, "a_running": 5},
    {"time_tag": "2026-10-13T00:00:00", "Kp": 3.33, "a_running": 18},
]

XRAY = [{"current_class": "B8.2", "max_class": "C1.2"}]


class Reply:
    def __init__(self, text="", payload=None):
        self.text = text
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def serve(**overrides):
    """A fake NOAA. `overrides` replaces one source with an exception."""
    def get(url, **kw):
        for key, reply in (("daily-solar-indices", Reply(text=DSD)),
                           ("planetary-k-index", Reply(payload=KP)),
                           ("xray-flares", Reply(payload=XRAY))):
            if key in url:
                if key in overrides:
                    raise overrides[key]
                return reply
        raise AssertionError(f"unexpected url {url}")
    return get


class TestItReadsTheNumbers(unittest.TestCase):
    def solar(self, **overrides):
        with mock.patch.object(space.requests, "get", serve(**overrides)):
            return space.Solar().get()

    def test_the_flux_and_sunspot_number_come_from_the_last_good_row(self):
        d = self.solar()
        self.assertEqual(d["sfi"], 123)
        self.assertEqual(d["ssn"], 93)
        self.assertEqual(d["observed"], "2026-10-12")

    def test_the_k_index_is_the_most_recent_one(self):
        self.assertEqual(self.solar()["k"], 3.33)

    def test_the_a_index_rides_along_with_it(self):
        """`a_running` is in the same file, so the A index costs no extra
        request - which is the only reason it is on the rail."""
        self.assertEqual(self.solar()["a"], 18)

    def test_the_xray_class_is_now_and_the_day_s_largest(self):
        d = self.solar()
        self.assertEqual(d["xray"], "B8.2")
        self.assertEqual(d["xray_max"], "C1.2")


class TestMissingValues(unittest.TestCase):
    """-999 and `*` are how the table says "not measured"."""

    def rows(self, table):
        def get(url, **kw):
            if "daily-solar-indices" in url:
                return Reply(text=table)
            if "planetary-k-index" in url:
                return Reply(payload=KP)
            return Reply(payload=XRAY)
        with mock.patch.object(space.requests, "get", get):
            return space.Solar().get()

    def test_a_minus_999_row_is_skipped_for_the_one_before_it(self):
        """Today's row often exists before the numbers do. Reading it gives
        `SSN -999`, which looks like a measurement rather than a gap."""
        table = DSD + "2026 10 13 -999   -999     -999      0    -999      *\n"
        d = self.rows(table)
        self.assertEqual(d["sfi"], 123)
        self.assertEqual(d["ssn"], 93)

    def test_a_table_of_nothing_leaves_the_numbers_out(self):
        d = self.rows(":Product: Daily Solar Data\n#\n")
        self.assertNotIn("sfi", d)
        self.assertNotIn("ssn", d)
        # but the other two sources still answered
        self.assertEqual(d["k"], 3.33)
        self.assertTrue(d["ok"])


class TestOneSourceFailingIsNotAllOfThem(unittest.TestCase):
    """The reason each fetch is its own try block."""

    def solar(self, **overrides):
        with mock.patch.object(space.requests, "get", serve(**overrides)):
            return space.Solar().get()

    def test_no_xray_still_gives_the_indices(self):
        d = self.solar(**{"xray-flares": requests.ConnectionError("nope")})
        self.assertTrue(d["ok"])
        self.assertEqual(d["sfi"], 123)
        self.assertEqual(d["k"], 3.33)
        self.assertNotIn("xray", d)

    def test_no_geomagnetic_still_gives_the_flux(self):
        d = self.solar(**{"planetary-k-index": requests.Timeout("slow")})
        self.assertTrue(d["ok"])
        self.assertEqual(d["sfi"], 123)
        self.assertNotIn("k", d)

    def test_it_says_which_part_is_missing(self):
        """A gap on the rail the operator has to guess about is worse than
        a gap with a reason attached to it."""
        d = self.solar(**{"xray-flares": requests.ConnectionError("nope")})
        self.assertIn("x-ray", d["incomplete"])

    def test_everything_failing_is_an_error_not_an_empty_answer(self):
        d = self.solar(**{
            "daily-solar-indices": requests.ConnectionError("a"),
            "planetary-k-index": requests.ConnectionError("b"),
            "xray-flares": requests.ConnectionError("c"),
        })
        self.assertFalse(d["ok"])
        self.assertIn("NOAA", d["error"])


class TestItDoesNotHammerNOAA(unittest.TestCase):
    def test_a_second_look_is_served_from_the_cache(self):
        """The flux and sunspot number are daily and Kp is three-hourly, so
        asking faster is the same question many times for one answer."""
        calls = []
        base = serve()

        def counting(url, **kw):
            calls.append(url)
            return base(url, **kw)

        with mock.patch.object(space.requests, "get", counting):
            s = space.Solar()
            s.get()
            first = len(calls)
            s.get()
            self.assertEqual(len(calls), first, "asked NOAA twice")

    def test_the_cache_is_longer_than_the_map_layers(self):
        self.assertGreater(space.SOLAR_TTL, space.TTL)


if __name__ == "__main__":
    unittest.main()
