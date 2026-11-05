"""Clicking an FT8/FT4 decode picks that station for the logbook and the map.

The Decoder publishes `decoder.picked` and looks the station up (the host
publishes `lookup.result`, which the map cards already draw); the logbook
takes `decoder.picked` and `spots.picked`. Verified in a browser on a
scratch board with the mock decoder: a click filled call, grid, sent report
and mode and opened the peek; a typed call survived a click. These pin the
parts that would break quietly - an undeclared topic is never relayed to a
card page (invariant 3a), so the pick would simply not arrive.

Run with:  python -m unittest discover -s tests
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEC = ROOT / "plugins" / "decoder"
LOG = ROOT / "plugins" / "logbook"


def manifest(d):
    return json.loads((d / "card.json").read_text(encoding="utf-8"))


class TestTheTopics(unittest.TestCase):
    def test_the_decoder_declares_what_it_publishes(self):
        self.assertIn("decoder.picked", manifest(DEC)["publishes"])

    def test_the_logbook_hears_both_kinds_of_pick(self):
        subs = manifest(LOG)["subscribes"]
        self.assertIn("decoder.picked", subs)
        self.assertIn("spots.picked", subs)

    def test_the_decoder_may_look_stations_up(self):
        self.assertIn("lookup", manifest(DEC)["permissions"])


class TestTheLogbookRules(unittest.TestCase):
    PAGE = (LOG / "index.html").read_text(encoding="utf-8")

    def body(self):
        s = self.PAGE[self.PAGE.index("function takePick("):]
        return s[:s.index("\n}\n")]

    def test_a_typed_call_is_never_overwritten(self):
        self.assertIn("if (cur && cur !== picked && cur !== call)", self.body())

    def test_typing_takes_the_box_back(self):
        handler = self.PAGE[self.PAGE.index("$('#f-call').addEventListener('input'"):]
        handler = handler[:handler.index("});")]
        self.assertIn("picked = ''", handler)

    def test_only_the_decoder_sets_the_report(self):
        """A spot's snr is a skimmer's, not a report we would send."""
        self.assertIn("if (report && typeof p.snr", self.body())
        self.assertRegex(self.PAGE, r"HD\.on\('decoder\.picked'.*true\)\);")
        self.assertNotRegex(self.PAGE, r"HD\.on\('spots\.picked'.*true\)\);")

    def test_a_guessed_mode_is_not_taken(self):
        self.assertIn("if (p.mode && !p.mode_from)", self.body())


class TestTheDecoderPage(unittest.TestCase):
    PAGE = (DEC / "index.html").read_text(encoding="utf-8")

    def test_a_hashed_call_is_not_pickable(self):
        m = re.search(r"const PICK_CALL = /(.*)/;", self.PAGE)
        rx = re.compile(m.group(1).replace("\\/", "/"))
        for call, ok in (("VA7HDX", True), ("EA8/G4ABC/P", True),
                         ("<...>", False), ("CQ", False), ("", False)):
            with self.subTest(call=call):
                self.assertEqual(bool(rx.match(call)), ok)

    def test_it_publishes_and_looks_up(self):
        s = self.PAGE[self.PAGE.index("async function pick("):]
        s = s[:s.index("\n}\n")]
        self.assertIn("HD.publish('decoder.picked'", s)
        self.assertIn("/api/lookup/", s)


if __name__ == "__main__":
    unittest.main()
