"""A click on a marker also reaches the map, on both renderers.

Leaflet fires a click on an interactive layer *and* on the map. Measured
in a real browser against the vendored build, with a throwaway map and a
synthesised click:

    canvas, default                 layer 1, map 1
    canvas, bubblingMouseEvents:F   layer 1, map 0
    SVG,    default                 layer 1, map 1
    SVG,    bubblingMouseEvents:F   layer 1, map 0

So it is not a canvas quirk and the renderer in use is no defence.

It only matters to a card that *has* a `map.on('click')` handler, and
today only `psk_reporter` does - where clicking a spot to read its
signal report also said "watch that grid square", which reloaded the
card and destroyed the popup that had just opened. It reads as the card
refusing to show you a spot.

This is a source check rather than a browser test because the behaviour
needs a real Leaflet and a real DOM, neither of which the suite has. It
catches the thing that actually recurs: somebody adds a marker to a card
that has a map click handler and does not know this rule exists.

Run with:  python -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

PLUGINS = Path(__file__).resolve().parent.parent / "plugins"

#: Leaflet constructors that make something clickable.
MARKERS = ("L.circleMarker(", "L.marker(", "L.circle(")


def call_body(text: str, start: int) -> str:
    """The source of one call, from its opening paren to its match."""
    depth = 0
    i = text.index("(", start)
    for j in range(i, min(len(text), i + 4000)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    return text[i:i + 4000]


def cards_with_a_map_click():
    for page in sorted(PLUGINS.glob("*/index.html")):
        text = page.read_text(encoding="utf-8")
        if re.search(r"map\.on\(\s*['\"][^'\"]*\bclick\b", text):
            yield page, text


class TestAMarkerDoesNotAlsoClickTheMap(unittest.TestCase):
    def test_at_least_one_card_is_covered(self):
        # If this ever finds nothing, the rule below is vacuous and the
        # file is passing by testing no code at all.
        self.assertTrue(list(cards_with_a_map_click()),
                        "no card has a map click handler any more - "
                        "delete this file or fix the search")

    def test_every_marker_declines_to_bubble(self):
        offenders = []
        for page, text in cards_with_a_map_click():
            for ctor in MARKERS:
                for m in re.finditer(re.escape(ctor), text):
                    body = call_body(text, m.start())
                    ok = ("bubblingMouseEvents: false" in body
                          or "bubblingMouseEvents:false" in body
                          or "interactive: false" in body
                          or "interactive:false" in body)
                    if not ok:
                        line = text[:m.start()].count("\n") + 1
                        offenders.append(
                            f"{page.parent.name}/index.html:{line} {ctor}")
        self.assertEqual(offenders, [], "\n".join([
            "These are clickable, and their card has a map click handler,",
            "so a click on them fires that handler too. Add",
            "`bubblingMouseEvents: false` (or `interactive: false` if the",
            "thing is decoration):", *offenders]))


class TestTheRuleWouldHaveCaughtIt(unittest.TestCase):
    """A check that the check bites, against the code as it was."""

    BEFORE = """
      map.on('click', (e) => { setMode('grid'); });
      L.circleMarker([st.at.lat, st.at.lon], {
        radius: 4,
        fillOpacity: 1,
      }).bindPopup(() => popupFor(st)).addTo(L_.spots);
    """

    def test_a_bare_marker_is_an_offender(self):
        body = call_body(self.BEFORE, self.BEFORE.index("L.circleMarker("))
        self.assertNotIn("bubblingMouseEvents", body)
        self.assertIn("radius: 4", body)      # the matcher found the right call

    def test_the_fixed_one_is_not(self):
        fixed = self.BEFORE.replace("radius: 4,",
                                    "radius: 4,\n        bubblingMouseEvents: false,")
        body = call_body(fixed, fixed.index("L.circleMarker("))
        self.assertIn("bubblingMouseEvents: false", body)


if __name__ == "__main__":
    unittest.main()
