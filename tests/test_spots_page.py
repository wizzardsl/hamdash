"""Every spot source has a mark, and the chip and the column agree on it.

The four sources were four coloured dots. They are small drawings now, which
is the part that survives being printed in grey or read by somebody who
cannot separate the hues - but a drawing only helps if there is one. A fifth
source added without an icon is a blank cell: not an error, just a row that
says nothing about where it came from, in the one column whose whole job is
to say that.

The chip at the top is the legend for the column, so it carries the same
mark. Adding one to either side alone is the regression this guards.

Run with:  python -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

CARD = Path(__file__).resolve().parent.parent / "plugins" / "spots"
PAGE = (CARD / "index.html").read_text(encoding="utf-8")


def kinds():
    """What the python side can stamp on a spot."""
    out = set(re.findall(r'kind=["\'](\w+)["\']', (CARD / "sources.py")
                         .read_text(encoding="utf-8")))
    out |= set(re.findall(r'want\.append\(\(["\'](\w+)["\']',
                          (CARD / "card.py").read_text(encoding="utf-8")))
    return out


def block(name):
    """The object literal assigned to `name`, up to its closing brace."""
    m = re.search(name + r"\s*=\s*\{(.*?)\};", PAGE, re.S)
    return m.group(1) if m else ""


def keys(name):
    out = set(re.findall(r"(?:^|[{,])\s*(\w+)\s*:", block(name), re.M))
    # `ICON.cluster = ICON.dx` and friends are assigned after the literal.
    out |= set(re.findall(name + r"\.(\w+)\s*=", PAGE))
    return out


class TestEverySourceHasAMark(unittest.TestCase):
    def test_the_kinds_are_the_five_we_know_about(self):
        """If this fails a source was added - which is fine, but the rest of
        this file is then telling you what else it needs."""
        self.assertEqual(kinds(), {"pota", "sota", "dx", "rbn", "cluster"})

    def test_each_kind_has_an_icon(self):
        missing = sorted(kinds() - keys("ICON"))
        self.assertEqual(missing, [], f"sources drawn as nothing: {missing}")

    def test_each_kind_has_a_name_to_hover(self):
        missing = sorted(kinds() - keys("SOURCE_NAME"))
        self.assertEqual(missing, [], f"sources with no name: {missing}")

    def test_each_kind_still_has_a_colour(self):
        """The drawing is added to the colour, not instead of it."""
        missing = sorted(kinds() - keys("COLOUR"))
        self.assertEqual(missing, [], f"sources with no colour: {missing}")

    def test_the_icons_take_the_source_colour(self):
        """They are drawn in `currentColor` so one rule colours both the
        chip and the cell. A hard-coded fill would make every source the
        same shade and lose the colour half of the encoding."""
        self.assertNotIn("fill=\"#", block("ICON"))

    def test_the_chip_and_the_cell_draw_the_same_thing(self):
        """The chip is the legend. Two sets of artwork would drift."""
        self.assertEqual(len(re.findall(r"\bicon\(", PAGE)), 3,
                         "icon() should be defined once and called from the "
                         "row and from the chip")


if __name__ == "__main__":
    unittest.main()
