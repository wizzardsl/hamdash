"""The decoder's FT8/FT4/WSPR band buttons tune where everybody else is.

The FT8 and FT4 dials are checked against the spots card's own table
(`_FT8_DIALS` / `_FT4_DIALS` in `plugins/spots/sources.py`), which was
measured against 500 live cluster spots - not against a second copy typed
here - and WSPR's against `wspr.DIALS`, which sits beside the protocol.
Every button's label is checked against hamdash's band plan.

Run with:  python -m unittest discover -s tests
"""
import ast
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "plugins" / "decoder" / "index.html").read_text(encoding="utf-8")
sys.path.insert(0, str(ROOT / "plugins" / "spots"))

import sources                                          # noqa: E402

from hamdash.services import bands                      # noqa: E402


def wspr_dials() -> dict:
    """`wspr.DIALS`, read out of the source: importing the module would
    bring numpy with it, and a table of numbers does not need it."""
    tree = ast.parse((ROOT / "plugins" / "decoder" / "wspr.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "DIALS":
            return ast.literal_eval(node.value)
    raise AssertionError("wspr.DIALS not found")


def dials():
    m = re.search(r"const DIALS = (\{.*?\n\});", PAGE, re.S)
    return json.loads(m.group(1))


class TestTheDials(unittest.TestCase):
    def test_every_slot_mode(self):
        self.assertEqual(set(dials()), {"ft8", "ft4", "wspr"})

    def test_wspr_is_the_module_s_table(self):
        self.assertEqual(dials()["wspr"], wspr_dials())

    def test_wspr_has_no_60_metre_button(self):
        """Its WSPR frequency differs by country; a button that tunes
        somewhere somebody's licence does not allow is worse than none."""
        self.assertNotIn("60", wspr_dials())

    def test_they_agree_with_the_spots_card(self):
        known = {"ft8": {float(k) for k in sources._FT8_DIALS},
                 "ft4": {float(k) for k in sources._FT4_DIALS}}
        for mode, table in dials().items():
            if mode == "wspr":
                continue
            for band, hz in table.items():
                with self.subTest(mode=mode, band=band):
                    self.assertIn(hz / 1000, known[mode])

    def test_each_button_says_the_band_it_tunes(self):
        for mode, table in dials().items():
            for band, hz in table.items():
                with self.subTest(mode=mode, band=band):
                    self.assertEqual(bands.for_hz(hz), f"{band}m")

    def test_the_bands_asked_for(self):
        for band in ("10", "15", "20", "40", "80"):
            self.assertIn(band, dials()["ft8"])
            self.assertIn(band, dials()["ft4"])


class TestTuning(unittest.TestCase):
    def test_mode_before_frequency(self):
        """A radio applies its CW offset on entering a mode; the rule is
        the same for every tune in hamdash (CLAUDE.md)."""
        body = PAGE[PAGE.index("async function tuneTo(hz)"):]
        body = body[:body.index("\n}\n")]
        self.assertLess(body.index("HD.radio.mode("), body.index("HD.radio.freq("))
        self.assertIn("'PKTUSB'", body)
        self.assertIn("'USB'", body)

    def test_the_row_is_hidden_outside_ft_modes_and_guarded(self):
        self.assertIn(".tune[hidden] { display: none; }", PAGE)
        self.assertIn("box.hidden = !dials;", PAGE)


if __name__ == "__main__":
    unittest.main()
