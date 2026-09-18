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


DRAP_SAMPLE = """# DRAP Tabular Values
# Product Valid At : 2026-09-18 20:44 UTC
#
      -178 -174 -170 -166
------------------------------------
 89 |  0.0  0.0  0.0  0.0
 87 |  0.0  5.2  0.0  0.0
 85 |  0.0  0.0 12.7  0.0
-89 |  0.0  0.0  0.0  3.1
"""


def text_response(body):
    r = mock.Mock(status_code=200, text=body)
    r.raise_for_status = mock.Mock()
    return r


class TestDrap(unittest.TestCase):
    """NOAA publishes this as a text table, not JSON: a header of longitudes
    then one row per latitude."""

    def drap(self, body=DRAP_SAMPLE):
        from hamdash.services.space import Drap
        d = Drap()
        with mock.patch("hamdash.services.space.requests.get",
                        return_value=text_response(body)):
            return d, d.get(threshold=1.0)

    def test_parses_the_table(self):
        _, out = self.drap()
        self.assertTrue(out["ok"])
        self.assertEqual(out["valid_at"], "2026-09-18 20:44 UTC")
        self.assertIn([-174.0, 87.0, 5.2], out["cells"])
        self.assertIn([-166.0, -89.0, 3.1], out["cells"])

    def test_zero_cells_are_dropped(self):
        _, out = self.drap()
        self.assertEqual(len(out["cells"]), 3)      # of sixteen in the grid

    def test_a_quiet_ionosphere_is_an_answer_not_a_failure(self):
        """All zero is the normal case and the good news. It must not read as
        a layer that failed to load."""
        quiet = DRAP_SAMPLE.replace("5.2", "0.0").replace("12.7", "0.0") \
                           .replace("3.1", "0.0")
        _, out = self.drap(quiet)
        self.assertTrue(out["ok"])
        self.assertTrue(out["quiet"])
        self.assertEqual(out["cells"], [])

    def test_threshold_filters(self):
        d, _ = self.drap()
        with mock.patch("hamdash.services.space.requests.get",
                        return_value=text_response(DRAP_SAMPLE)):
            self.assertEqual(len(d.get(threshold=6.0, force=True)["cells"]), 1)

    def test_network_failure_is_reported(self):
        from hamdash.services.space import Drap
        with mock.patch("hamdash.services.space.requests.get",
                        side_effect=requests.ConnectionError("reset")):
            out = Drap().get()
        self.assertFalse(out["ok"])
        self.assertIn("NOAA", out["error"])


STATIONS = [
    {"station": {"name": "Austin, TX, USA", "code": "AU930",
                 "latitude": "30.4", "longitude": "262.3"},
     "mufd": 28.827, "fof2": 8.6, "time": "2026-09-18T22:10:05"},
    {"station": {"name": "Nowhere", "code": "NO000",
                 "latitude": "bad", "longitude": "10"},
     "mufd": 10.0, "fof2": None, "time": ""},
    {"station": {"name": "Silent", "code": "SI000",
                 "latitude": "10", "longitude": "10"},
     "mufd": None, "fof2": None, "time": ""},
]


class TestIonosondes(unittest.TestCase):
    def fetch(self):
        from hamdash.services.space import Ionosondes
        r = mock.Mock(status_code=200)
        r.raise_for_status = mock.Mock()
        r.json = mock.Mock(return_value=STATIONS)
        with mock.patch("hamdash.services.space.requests.get", return_value=r):
            return Ionosondes().get()

    def test_longitudes_come_back_in_minus180_to_180(self):
        """They publish 0..360; 262.3 is Texas, not the middle of the Pacific."""
        out = self.fetch()
        austin = [s for s in out["stations"] if s["code"] == "AU930"][0]
        self.assertAlmostEqual(austin["lon"], -97.7, places=1)

    def test_unusable_rows_are_skipped(self):
        out = self.fetch()
        codes = {s["code"] for s in out["stations"]}
        self.assertIn("AU930", codes)
        self.assertNotIn("NO000", codes)     # unparseable latitude
        self.assertNotIn("SI000", codes)     # nothing measured

    def test_it_carries_credit(self):
        """prop.kc2g.com and GIRO are the source; saying so is the deal."""
        self.assertIn("kc2g", self.fetch()["credit"])

    def test_measurements_are_kept_as_measurements(self):
        out = self.fetch()
        austin = out["stations"][0]
        self.assertEqual(austin["mufd"], 28.8)
        self.assertEqual(austin["fof2"], 8.6)
