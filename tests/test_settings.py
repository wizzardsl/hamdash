"""Settings and layout robustness.

Both guards here exist because of bugs the spectrum card walked into: a card
asking for settings outside a real tile got an empty dict rather than its
manifest defaults, and a layout.json of the wrong shape got all the way to an
unrelated 500.

Run with:  python -m unittest discover -s tests
"""
import json
import tempfile
import unittest
from pathlib import Path

from hamdash.config import Config
from hamdash.sdk import Card

PLUGINS = Path(__file__).resolve().parent.parent / "plugins"


def manifest_of(card_id: str) -> dict:
    return json.loads((PLUGINS / card_id / "card.json").read_text(encoding="utf-8"))


def card_with(manifest: dict) -> Card:
    """A Card without running __init__, which would try to reach the host."""
    c = Card.__new__(Card)
    c.manifest = manifest
    return c


class TestManifestDefaults(unittest.TestCase):
    """`settings()` used to fall back to manifest['settings_defaults'], a key
    no card.json has ever had, so the fallback silently produced {}."""

    def test_reads_defaults_from_the_settings_block(self):
        d = card_with(manifest_of("web_embed")).defaults()
        self.assertEqual(d["refresh_s"], 0)
        self.assertTrue(d["url"].startswith("http"))

    def test_every_bundled_card_yields_its_declared_defaults(self):
        for path in sorted(PLUGINS.iterdir()):
            if not (path / "card.json").exists():
                continue
            m = manifest_of(path.name)
            with self.subTest(card=path.name):
                self.assertEqual(set(card_with(m).defaults()),
                                 set(m.get("settings") or {}))

    def test_missing_default_falls_back_by_type(self):
        c = card_with({"settings": {"flag": {"type": "bool"},
                                    "name": {"type": "string"}}})
        self.assertEqual(c.defaults(), {"flag": False, "name": ""})

    def test_bare_value_is_taken_as_the_default(self):
        self.assertEqual(card_with({"settings": {"n": 7}}).defaults(), {"n": 7})

    def test_no_settings_block(self):
        self.assertEqual(card_with({}).defaults(), {})


class TestLayoutLoading(unittest.TestCase):
    def layout(self, text: str):
        home = Path(tempfile.mkdtemp(prefix="hamdash-test-"))
        (home / "layout.json").write_text(text, encoding="utf-8")
        return Config(home).load_layout()

    def test_a_list_of_tiles(self):
        """Loaded in the stack shape: a tile is a box of cards, and a plain
        one is a box of one, so there is a single code path above this.
        tests/test_layout.py covers the upgrade itself."""
        got = self.layout('[{"uid":"a","card":"x"}]')
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["uid"], "a")
        self.assertEqual([c["card"] for c in got[0]["cards"]], ["x"])

    def test_a_hand_edited_wrapper_is_unwrapped(self):
        """{"tiles": [...]} is the obvious thing to write by hand."""
        got = self.layout('{"tiles":[{"uid":"a","card":"x"}]}')
        self.assertEqual([c["card"] for t in got for c in t["cards"]], ["x"])

    def test_wrong_shape_gives_a_blank_board_not_a_500(self):
        """This used to reach /api/tiles/<uid>/settings and fail there with
        "'str' object has no attribute 'get'", which names nothing useful."""
        self.assertEqual(self.layout('{"nope": 1}'), [])
        self.assertEqual(self.layout('"just a string"'), [])
        self.assertEqual(self.layout('[1, 2, 3]'), [])

    def test_bad_json(self):
        self.assertEqual(self.layout("not json at all"), [])

    def test_no_file_gives_the_starter_board(self):
        home = Path(tempfile.mkdtemp(prefix="hamdash-test-"))
        tiles = Config(home).load_layout()
        self.assertTrue(tiles)
        self.assertTrue(all("uid" in t and t.get("cards") for t in tiles))
        self.assertTrue(all(c.get("card") and c.get("uid")
                            for t in tiles for c in t["cards"]))


class TestDefaultsAreNotShared(unittest.TestCase):
    """A default that is a list or a dict must not be the same object every
    time. `dict(base)` is shallow, so `radio.rigs` came back as one list
    shared by every Config in the process - appending a radio to one added it
    to the others and to DEFAULTS itself, where it outlived them all. One
    Config hides it; the tests and `--run-card` do not."""

    def test_two_configs_do_not_share_a_list(self):
        from hamdash.config import DEFAULTS, _merge
        a, b = _merge(DEFAULTS, {}), _merge(DEFAULTS, {})
        a["radio"]["rigs"].append({"id": "x"})
        self.assertEqual(b["radio"]["rigs"], [])

    def test_merging_does_not_edit_the_defaults(self):
        from hamdash.config import DEFAULTS, _merge
        got = _merge(DEFAULTS, {})
        got["radio"]["rigs"].append({"id": "x"})
        got["station"]["callsign"] = "CHANGED"
        self.assertEqual(DEFAULTS["radio"]["rigs"], [])
        self.assertNotEqual(DEFAULTS["station"].get("callsign"), "CHANGED")

    def test_a_value_from_the_file_is_copied_too(self):
        from hamdash.config import DEFAULTS, _merge
        mine = {"radio": {"rigs": [{"id": "a"}]}}
        got = _merge(DEFAULTS, mine)
        got["radio"]["rigs"].append({"id": "b"})
        self.assertEqual(len(mine["radio"]["rigs"]), 1)


if __name__ == "__main__":
    unittest.main()
