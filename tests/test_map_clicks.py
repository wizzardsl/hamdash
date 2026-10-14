"""What a click on a map may and may not do.

Two rules, both found by a click doing something nobody asked for.

**A click on a marker also reaches the map, on both renderers.** Leaflet
fires the layer's handler and then the map's. Measured in a real browser
against the vendored build, with a throwaway map and a synthesised click:

    canvas, default                 layer 1, map 1
    canvas, bubblingMouseEvents:F   layer 1, map 0
    SVG,    default                 layer 1, map 1
    SVG,    bubblingMouseEvents:F   layer 1, map 0

So it is not a canvas quirk and the renderer in use is no defence.

**A click on the map may re-aim a card, never re-purpose it.** The PSK
card has two modes - watching a callsign ("who is hearing me") and
watching a grid square ("what is heard over there") - and a map click
used to switch the first into the second, rewriting the box with a grid.
A click anywhere, including a near miss on a spot you were trying to
read, silently stopped answering the question you had asked.

Both only matter to a card that *has* a `map.on('click')` handler, which
today is `psk_reporter` alone. These are source checks rather than
browser tests because the behaviour needs a real Leaflet and a real DOM,
neither of which the suite has; what they catch is the thing that
actually recurs, which is somebody not knowing the rule exists.

Run with:  python -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

PLUGINS = Path(__file__).resolve().parent.parent / "plugins"

#: Leaflet constructors that make something clickable.
MARKERS = ("L.circleMarker(", "L.marker(", "L.circle(")

MAP_CLICK = re.compile(r"map\.on\(\s*['\"][^'\"]*\bclick\b")


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
        if MAP_CLICK.search(text):
            yield page, text


def at_line(text: str, pos: int) -> int:
    return text[:pos].count("\n") + 1


class TestAMarkerDoesNotAlsoClickTheMap(unittest.TestCase):
    def test_at_least_one_card_is_covered(self):
        # If this ever finds nothing, the rules below are vacuous and the
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
                        offenders.append(
                            f"{page.parent.name}/index.html:"
                            f"{at_line(text, m.start())} {ctor}")
        self.assertEqual(offenders, [], "\n".join([
            "These are clickable, and their card has a map click handler,",
            "so a click on them fires that handler too. Add",
            "`bubblingMouseEvents: false` (or `interactive: false` if the",
            "thing is decoration):", *offenders]))


class TestAMapClickDoesNotChangeWhatTheCardIsAbout(unittest.TestCase):
    """Re-aiming within the mode is the point of clicking the map.

    Changing the mode is a thing to do on purpose, and the mode buttons
    are how.
    """

    def test_no_handler_switches_mode(self):
        offenders = []
        for page, text in cards_with_a_map_click():
            for m in MAP_CLICK.finditer(text):
                if "setMode(" in call_body(text, m.start()):
                    offenders.append(
                        f"{page.parent.name}/index.html:"
                        f"{at_line(text, m.start())}")
        self.assertEqual(offenders, [], "\n".join([
            "A click on the map changes the card's mode in these handlers.",
            "Re-aim within the mode and leave changing it to the mode",
            "control:", *offenders]))


class TestTheSpaceCanvasIsInert(unittest.TestCase):
    """The shared space-weather renderer must not take the mouse.

    `spaceLayers` adds its own `L.canvas` to the map, and it lands in
    `overlayPane` *after* whatever renderer the calling card already
    made. A Leaflet canvas does not pass events through to the canvas
    beneath it, so this one sat on top of the card's own markers and
    swallowed every hover and click.

    It comes and goes, which is what made it hard to see: `psk_reporter`
    only puts its spots on a canvas once there are enough of them, so a
    quiet band works and a busy one does not. Measured on a live board -
    786 stations, 2358 markers all on canvas, and `elementFromPoint` over
    one of them returning the space canvas every time; with the fix, the
    same dot gives `KH6DC - -18 dB - 40m FT8`.

    Nothing drawn on that renderer is interactive, so there is nothing to
    lose by making it inert - the aurora and D-RAP dots pass
    `interactive: false`, and the ionosondes, which do have tooltips, use
    the map's default SVG renderer.
    """

    HDMAP = (Path(__file__).resolve().parent.parent
             / "hamdash" / "static" / "js" / "hdmap.js")

    def test_the_renderer_is_made_non_interactive(self):
        text = self.HDMAP.read_text(encoding="utf-8")
        start = text.index("function spaceLayers")
        body = text[start:start + 4000]
        self.assertIn("L.canvas(", body, "spaceLayers no longer makes a "
                                         "renderer - this rule may be stale")
        self.assertRegex(
            body, r"pointerEvents\s*=\s*.none.",
            "the space-weather canvas can take mouse events again, which "
            "kills hover and click on any card drawing markers to a canvas")

    def test_the_ionosondes_are_not_on_that_renderer(self):
        # They are the one space layer with a tooltip and a popup. If they
        # were moved onto the inert canvas they would go dead silently.
        text = self.HDMAP.read_text(encoding="utf-8")
        at = text.index("bindTooltip(muf === null")
        before = text[max(0, at - 700):at]
        self.assertNotIn("renderer: canvas", before,
                         "the ionosonde markers are on the inert renderer, "
                         "so their tooltip and popup cannot fire")


class TestTheRulesBite(unittest.TestCase):
    """Checks that the checks fail against the code as it was."""

    MARKER_BEFORE = """
      map.on('click', (e) => { load(); });
      L.circleMarker([st.at.lat, st.at.lon], {
        radius: 4,
        fillOpacity: 1,
      }).bindPopup(() => popupFor(st)).addTo(L_.spots);
    """

    HANDLER_BEFORE = "\n".join([
        "map.on('click', (e) => {",
        "  const grid = HDMap.toGrid(e.latlng.lat, e.latlng.lng, 4);",
        "  if (watchMode !== 'grid') setMode('grid', { reload: false });",
        "  load();",
        "});",
    ])

    def test_a_bare_marker_is_an_offender(self):
        body = call_body(self.MARKER_BEFORE,
                         self.MARKER_BEFORE.index("L.circleMarker("))
        self.assertNotIn("bubblingMouseEvents", body)
        self.assertIn("radius: 4", body)     # the matcher found the right call

    def test_the_fixed_marker_is_not(self):
        fixed = self.MARKER_BEFORE.replace(
            "radius: 4,", "radius: 4,\n        bubblingMouseEvents: false,")
        body = call_body(fixed, fixed.index("L.circleMarker("))
        self.assertIn("bubblingMouseEvents: false", body)

    def test_a_handler_that_switches_mode_is_found(self):
        was = self.HANDLER_BEFORE
        self.assertIn("setMode(", call_body(was, was.index("map.on(")))

    def test_the_fixed_handler_is_not(self):
        now = self.HANDLER_BEFORE.replace(
            "  if (watchMode !== 'grid') setMode('grid', { reload: false });",
            "  if (watchMode !== 'grid') return;")
        self.assertNotIn("setMode(", call_body(now, now.index("map.on(")))


if __name__ == "__main__":
    unittest.main()
