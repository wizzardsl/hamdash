"""The aurora grid, as one byte a cell.

It is sent this way so the browser can draw the aurora as an *image* and let
the GPU interpolate it. The old overlay was one dot per cell at a fixed three
screen pixels, which can tile the globe at exactly one zoom and looks like
stipple at every other.

Two things here are easy to get wrong and invisible when you do. NOAA gives
longitude as 0..360 and the map wants -180..180, so the image has to be
rolled half a turn - get that wrong and the aurora is drawn over Asia instead
of Canada, which looks like plausible aurora. And the grid runs south to
north while image rows run top down, so a missing flip puts the northern oval
over Antarctica, which also looks like plausible aurora.

Measured against a live NOAA run: 360 x 181 at exactly 1 degree, 65160 rows,
no gaps.

Run with:  python -m unittest discover -s tests
"""
import base64
import unittest

from hamdash.services.space import _raster


def noaa_like(w=360, h=181, hot=None):
    """A grid in NOAA's own shape: lon 0..359, lat -90..90, 1 degree."""
    lons = {float(x) for x in range(w)}
    lats = {float(y - 90) for y in range(h)}
    seen = {(float(x), float(y - 90)): 0 for x in range(w) for y in range(h)}
    for lon, lat, pct in (hot or []):
        seen[(float(lon), float(lat))] = pct
    return lons, lats, seen


def read(g, lon, lat):
    """What the raster holds at a real-world position."""
    buf = base64.b64decode(g["data"])
    ix = int(round((lon - g["lon0"]) / g["step_lon"])) % g["w"]
    iy = int(round((lat - g["lat0"]) / g["step_lat"]))
    return buf[iy * g["w"] + ix]


class TestTheShapeIsMeasuredNotAssumed(unittest.TestCase):
    def test_a_one_degree_grid(self):
        g = _raster(*noaa_like())
        self.assertEqual((g["w"], g["h"]), (360, 181))
        self.assertEqual((g["step_lon"], g["step_lat"]), (1.0, 1.0))
        self.assertEqual(len(base64.b64decode(g["data"])), 360 * 181)

    def test_a_finer_grid_is_read_as_one(self):
        """NOAA has published this at more than one resolution, so the step
        is taken from the data rather than written down here."""
        lons = {x / 2 for x in range(0, 720)}
        lats = {y / 2 - 90 for y in range(0, 361)}
        seen = {(lo, la): 0 for lo in lons for la in lats}
        g = _raster(lons, lats, seen)
        self.assertEqual((g["step_lon"], g["step_lat"]), (0.5, 0.5))
        self.assertEqual((g["w"], g["h"]), (720, 361))


class TestItLandsInTheRightPlace(unittest.TestCase):
    def test_longitude_is_rolled_to_start_at_minus_180(self):
        """NOAA's 0..360 drawn straight onto a -180..180 map puts the
        northern oval over Asia instead of Canada - which looks like
        perfectly plausible aurora, and is the reason this is tested."""
        g = _raster(*noaa_like(hot=[(250, 65, 99)]))       # 250E == 110W
        self.assertEqual(g["lon0"], -180.0)
        self.assertEqual(read(g, -110, 65), 99)
        self.assertEqual(read(g, 110, 65), 0)

    def test_the_prime_meridian_stays_put(self):
        g = _raster(*noaa_like(hot=[(0, 70, 42)]))
        self.assertEqual(read(g, 0, 70), 42)

    def test_the_antimeridian_is_one_place(self):
        """180E and 180W are the same line; after the roll it is column 0."""
        g = _raster(*noaa_like(hot=[(180, 60, 55)]))
        self.assertEqual(read(g, -180, 60), 55)

    def test_south_is_not_north(self):
        """The grid runs south to north. A missing flip draws the northern
        oval over Antarctica, which also looks like plausible aurora."""
        g = _raster(*noaa_like(hot=[(0, 65, 90), (0, -65, 10)]))
        self.assertEqual(read(g, 0, 65), 90)
        self.assertEqual(read(g, 0, -65), 10)

    def test_every_cell_survives_the_round_trip(self):
        hot = [(lon, lat, (lon + lat) % 100)
               for lon in range(0, 360, 7) for lat in range(-80, 81, 11)]
        g = _raster(*noaa_like(hot=hot))
        for lon, lat, pct in hot:
            with self.subTest(lon=lon, lat=lat):
                self.assertEqual(read(g, lon - 360 if lon > 180 else lon,
                                      lat), pct)


class TestItRefusesWhatIsNotAGrid(unittest.TestCase):
    """Returning something wrong is worse than returning nothing: the client
    falls back to drawing cells, which is ugly but correct."""

    def test_a_ragged_grid_is_refused(self):
        lons, lats, seen = noaa_like()
        seen.pop((5.0, 5.0))
        self.assertIsNone(_raster(lons, lats, seen))

    def test_too_few_rows_is_refused(self):
        self.assertIsNone(_raster({0.0}, {0.0}, {(0.0, 0.0): 1}))

    def test_a_value_over_a_byte_is_clamped_not_wrapped(self):
        """255 wrapping to 0 would turn the brightest cell transparent."""
        g = _raster(*noaa_like(hot=[(10, 10, 300)]))
        self.assertEqual(read(g, 10, 10), 255)


class TestWhatItCosts(unittest.TestCase):
    def test_it_is_smaller_than_the_cells_it_replaces(self):
        """The whole field, with no threshold, against the thresholded
        triples it replaces: 85 kB of base64 against 115 kB of JSON,
        measured on a live run. Being complete is what lets the faint edge
        fade instead of stopping square at a contour."""
        g = _raster(*noaa_like())
        self.assertLess(len(g["data"]), 90 * 1024)


if __name__ == "__main__":
    unittest.main()
