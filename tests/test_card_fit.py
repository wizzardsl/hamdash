"""`fit` in a card's manifest: the pixels its page needs without scrolling.

The board does arithmetic with these numbers, so a string or a negative
in a hand-written card.json would come out as a tile NaN columns wide
rather than as an error anybody sees. They are checked when the manifest
is read and anything wrong is a warning, which the board shows on the
tile's status dot.

Run with:  python -m unittest discover -s tests
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from hamdash import registry


def a_card(**manifest):
    root = Path(tempfile.mkdtemp(prefix="hamdash-fit-"))
    d = root / "my_card"
    d.mkdir()
    (d / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    (d / "card.json").write_text(json.dumps(
        {"id": "my_card", "kind": "html", **manifest}), encoding="utf-8")
    return registry.discover([root])["my_card"], root


class TestFit(unittest.TestCase):
    def card(self, **manifest):
        card, root = a_card(**manifest)
        self.addCleanup(shutil.rmtree, root, True)
        return card

    def test_pixels_are_kept_as_whole_numbers(self):
        self.assertEqual(self.card(fit={"w": 460.4, "h": 410}).fit,
                         {"w": 460, "h": 410})

    def test_width_alone_is_allowed(self):
        """A list is meant to grow and scroll: it asks for width only."""
        c = self.card(fit={"w": 590})
        self.assertEqual(c.fit, {"w": 590})
        self.assertEqual(c.warnings, [])

    def test_no_fit_is_nothing(self):
        self.assertEqual(self.card().fit, {})

    def test_junk_is_dropped_and_said(self):
        for bad in ({"w": "wide"}, {"w": -5}, {"h": True}, {"w": 99999}):
            with self.subTest(fit=bad):
                c = self.card(fit=bad)
                self.assertEqual(c.fit, {})
                self.assertTrue(any("fit" in w for w in c.warnings), c.warnings)

    def test_not_a_dict_is_said(self):
        c = self.card(fit=[460, 410])
        self.assertEqual(c.fit, {})
        self.assertTrue(any("fit" in w for w in c.warnings))

    def test_it_reaches_the_board(self):
        self.assertEqual(self.card(fit={"w": 300}).public()["fit"], {"w": 300})

    def test_the_bundled_cards_are_clean(self):
        root = Path(registry.__file__).resolve().parents[1] / "plugins"
        for cid, card in registry.discover([root]).items():
            with self.subTest(card=cid):
                self.assertFalse([w for w in card.warnings if "fit" in w])


if __name__ == "__main__":
    unittest.main()
