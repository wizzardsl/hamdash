"""The aurora feed.

NOAA publishes a 360x181 grid that is mostly zero. What matters here is that
it is cached (they run the model every five minutes and every open tile
polling them would be rude), thinned (the zeros carry no information), and
that longitudes come out in the convention the rest of the app uses.

Run with:  python -m unittest discover -s tests
"""
import unittest
from unittest import mock

import requests

from hamdash.services.space import Aurora


def noaa(cells, observed="2026-09-18T19:28:00Z", forecast="2026-09-18T20:34:00Z"):
    r = mock.Mock()
    r.status_code = 200
    r.raise_for_status = mock.Mock()
    r.json = mock.Mock(return_value={
        "Observation Time": observed, "Forecast Time": forecast,
        "coordinates": cells})
    return r


GRID = [[0, 60, 0], [10, 65, 12], [200, 70, 40], [350, -61, 5], [20, 0, 0]]


class TestAurora(unittest.TestCase):
    def test_zero_cells_are_dropped(self):
        a = Aurora()
        with mock.patch("hamdash.services.space.requests.get",
                        return_value=noaa(GRID)):
            d = a.get(threshold=1)
        self.assertEqual(len(d["cells"]), 3)          # the two zeros are gone
        self.assertEqual(d["total_cells"], 3)

    def test_longitudes_come_back_in_minus180_to_180(self):
        """NOAA uses 0..360; every other coordinate in hamdash is -180..180,
        so a cell at 200 degrees would otherwise plot off the map."""
        a = Aurora()
        with mock.patch("hamdash.services.space.requests.get",
                        return_value=noaa(GRID)):
            d = a.get(threshold=1)
        lons = sorted(c[0] for c in d["cells"])
        self.assertTrue(all(-180 <= x <= 180 for x in lons), lons)
        self.assertIn(-160.0, lons)                   # 200 -> -160
        self.assertIn(-10.0, lons)                    # 350 -> -10

    def test_threshold_filters_without_refetching(self):
        a = Aurora()
        with mock.patch("hamdash.services.space.requests.get",
                        return_value=noaa(GRID)) as get:
            self.assertEqual(len(a.get(threshold=1)["cells"]), 3)
            self.assertEqual(len(a.get(threshold=10)["cells"]), 2)
            self.assertEqual(len(a.get(threshold=39)["cells"]), 1)
        self.assertEqual(get.call_count, 1, "thresholding must not refetch")

    def test_it_caches(self):
        a = Aurora()
        with mock.patch("hamdash.services.space.requests.get",
                        return_value=noaa(GRID)) as get:
            a.get()
            a.get()
            a.get()
        self.assertEqual(get.call_count, 1)

    def test_timestamps_are_passed_through(self):
        a = Aurora()
        with mock.patch("hamdash.services.space.requests.get",
                        return_value=noaa(GRID)):
            d = a.get()
        self.assertEqual(d["observed"], "2026-09-18T19:28:00Z")
        self.assertEqual(d["forecast"], "2026-09-18T20:34:00Z")

    def test_a_dead_network_is_an_answer_not_an_exception(self):
        a = Aurora()
        with mock.patch("hamdash.services.space.requests.get",
                        side_effect=requests.ConnectionError("offline")):
            d = a.get()
        self.assertFalse(d["ok"])
        self.assertIn("NOAA", d["error"])
        self.assertEqual(d["cells"], [])

    def test_junk_rows_are_skipped_not_fatal(self):
        a = Aurora()
        bad = [[10, 65, 12], ["x", "y", "z"], [1, 2], None]
        with mock.patch("hamdash.services.space.requests.get",
                        return_value=noaa(bad)):
            d = a.get(threshold=1)
        self.assertEqual(len(d["cells"]), 1)


if __name__ == "__main__":
    unittest.main()
