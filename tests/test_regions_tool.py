"""tools/regions.py, which builds the ITU Regions overlay's data.

The lines are pure arithmetic and always run. The region shapes need
shapely, which is a dependency of the tool and not of hamdash, so those
tests skip without it. They use boxes for countries rather than Natural
Earth, so they say what the rule does without fetching anything: the
real build was checked against 35 real places when it was made.
"""
import importlib.util
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "regions.py"
spec = importlib.util.spec_from_file_location("regions_tool", TOOL)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

try:
    import shapely  # noqa: F401
    HAVE_SHAPELY = True
except ImportError:
    HAVE_SHAPELY = False


class TestTheLines(unittest.TestCase):
    def test_line_a(self):
        a = tool.line_a()
        self.assertEqual(a[0], (40, 90))
        self.assertIn((40, 40), a)
        self.assertIn((60, tool.TROPIC), a)
        self.assertEqual(a[-1], (60, -90))

    def test_line_b(self):
        b = tool.line_b()
        for corner in ((-10, 90), (-10, 72), (-50, 40), (-20, -10), (-20, -90)):
            self.assertIn(corner, b)

    def test_line_c_is_continuous_across_the_date_line(self):
        """165 E is -195 here; a jump to +165 would draw a line round the world."""
        c = tool.line_c()
        self.assertIn((-195, 50), c)
        self.assertIn((-120, 10), c)
        for p, q in zip(c, c[1:]):
            self.assertLess(abs(p[0] - q[0]), 2, (p, q))

    def test_a_great_circle_is_not_a_straight_line(self):
        """From 50 W 40 N to 20 W 10 S: a rhumb line would pass 35 W at 15 N."""
        arc = tool.great_circle((-50, 40), (-20, -10))
        mid = min(arc, key=lambda p: abs(p[0] + 35))
        self.assertGreater(abs(mid[1] - 15), 0.5)


def box(x0, y0, x1, y1):
    from shapely.geometry import box as b
    return b(x0, y0, x1, y1)


@unittest.skipUnless(HAVE_SHAPELY, "shapely is needed by tools/regions.py only")
class TestTheRule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        far = box(-60, -60, -59, -59)              # somewhere harmless
        countries = {k: far for k in tool.REGION_1_TERRITORY}
        countries.update({
            "RUS": box(100, 50, 130, 60),          # east of line A
            "KAZ": box(100, 40, 130, 48),          # with 2 degrees of sea between
            "IRN": box(45, 27, 62, 38),            # straddling line A
            "CHN": box(100, 20, 130, 39),          # an ordinary Region 3 country
        })
        cls.r = tool.regions(countries)

    def at(self, lon, lat):
        return tool.region_of(*self.r, lon, lat)

    def test_the_lines_alone(self):
        self.assertEqual(self.at(0, 50), 1)        # Europe
        self.assertEqual(self.at(-100, 40), 2)     # North America
        self.assertEqual(self.at(140, -30), 3)     # Australia
        self.assertEqual(self.at(-30, -50), 2)     # west of line B, far south

    def test_the_named_countries_are_region_1_wherever_they_are(self):
        self.assertEqual(self.at(115, 55), 1)
        self.assertEqual(self.at(115, 44), 1)
        self.assertEqual(self.at(115, 30), 3, "a neighbour is not swept in")

    def test_iran_is_region_3_on_both_sides_of_line_a(self):
        self.assertEqual(self.at(51.4, 35.7), 3)   # Tehran, west of line A
        self.assertEqual(self.at(61, 30), 3)

    def test_water_the_named_countries_enclose_is_theirs(self):
        """The Caspian and the White Sea, which a strict reading of the
        country outlines would leave as pockets of Region 3."""
        self.assertEqual(self.at(115, 49), 1)

    def test_the_arctic_north_of_russia(self):
        self.assertEqual(self.at(150, 80), 1)

    def test_no_line_is_drawn_twice(self):
        """Line C bounds Region 2 at -120 and Region 3 at +240: one line."""
        lines = tool.boundaries(*self.r)
        souths = [ln for ln in lines if min(p[1] for p in ln) <= -89.9]
        bottoms = sorted(((ln[0] if ln[0][1] < ln[-1][1] else ln[-1])[0] + 540) % 360 - 180
                         for ln in souths)
        self.assertEqual(bottoms, [-120, -20, 60])


if __name__ == "__main__":
    unittest.main()
