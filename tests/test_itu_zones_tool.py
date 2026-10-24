"""tools/itu_zones.py, which builds the ITU zones overlay's data.

The rules worth pinning are the two about the date line, because the
source writes two different things the same way: a whole parallel (a
real zone edge, round a polar cap) and a seam (an edge lying on +-180).
The first version treated both as seams and the polar zones drew nothing.

Run with:  python -m unittest discover -s tests
"""
import importlib.util
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "itu_zones.py"
spec = importlib.util.spec_from_file_location("itu_zones_tool", TOOL)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def a_zone(n, ring, at=(0, 0)):
    return {"type": "Feature",
            "properties": {"itu_zone_number": n, "itu_zone_name_loc": list(at)},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


class TestTheDateLine(unittest.TestCase):
    def test_a_polar_band_keeps_both_parallels(self):
        """Zone 74 as the source has it: four corners, two of them jumps."""
        ring = [[180, -80], [-180, -80], [-180, -85], [180, -85]]
        got = tool.lines([tuple(p) for p in ring])
        lats = sorted(line[0][1] for line in got)
        self.assertEqual(lats, [-85, -80])
        for line in got:
            self.assertEqual((line[0][0], line[-1][0]), (-180, 180))
            self.assertTrue(all(abs(b[0] - a[0]) <= tool.PARALLEL_STEP + 1e-9
                                for a, b in zip(line, line[1:])))

    def test_an_edge_on_the_date_line_is_dropped(self):
        ring = [(170, 10), (180, 10), (180, 20), (170, 20), (170, 10)]
        got = tool.lines(ring)
        for line in got:
            for a, b in zip(line, line[1:]):
                self.assertFalse(a[0] == b[0] == 180, "drew along the date line")

    def test_an_ordinary_zone_is_one_closed_line(self):
        ring = [(-10, 40), (10, 40), (10, 60), (-10, 60), (-10, 40)]
        got = tool.lines(ring)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0], got[0][-1])


class TestTheBuild(unittest.TestCase):
    def test_a_whole_parallel_is_not_simplified_back_to_its_ends(self):
        geo = {"features": [a_zone(75, [[-180, 80], [180, 80], [180, 85], [-180, 85]],
                                   at=(82.5, 0))]}
        (zone,) = tool.build(geo)
        self.assertEqual(zone["n"], 75)
        self.assertTrue(all(len(line) > 60 for line in zone["lines"]))

    def test_points_are_rounded_and_unwrapped_longitudes_kept(self):
        geo = {"features": [a_zone(1, [[-200.04, 60], [-141, 60], [-141, 70.06],
                                       [-200.04, 70.06], [-200.04, 60]], at=(65, -170))]}
        (zone,) = tool.build(geo)
        pts = [p for line in zone["lines"] for p in line]
        self.assertIn([-200.0, 60], pts, "a longitude past -180 was folded back")
        self.assertTrue(all(p[1] == round(p[1], 1) for p in pts))

    def test_it_is_pinned_to_a_commit(self):
        self.assertRegex(tool.COMMIT, r"^[0-9a-f]{40}$")
        self.assertIn(tool.COMMIT, tool.URL)


if __name__ == "__main__":
    unittest.main()
