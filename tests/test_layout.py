"""Tiles that hold more than one card, and the upgrade into that shape.

This is the half of stacking where a mistake is silent and permanent: a uid
that changes takes an operator's per-tile settings with it, and a layout that
normalises wrongly is written back over the only copy of their board. The
board UI can be looked at; this cannot.

Run with:  python -m unittest discover -s tests
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from hamdash.config import Config, normalise_layout, normalise_tile

FLAT = {"uid": "t1", "card": "logbook", "x": 2, "y": 1, "w": 5, "h": 4,
        "settings": {"page_size": 30}, "popped": False}

STACK = {"uid": "s1", "x": 0, "y": 0, "w": 6, "h": 5, "active": "b",
         "cards": [{"uid": "a", "card": "spots", "settings": {"q": 1}},
                   {"uid": "b", "card": "decoder", "settings": {}}]}


def a_home(tiles):
    home = Path(tempfile.mkdtemp(prefix="hamdash-layout-"))
    (home / "config.json").write_text("{}", encoding="utf-8")
    (home / "layout.json").write_text(json.dumps(tiles), encoding="utf-8")
    return home


class TestTheUpgrade(unittest.TestCase):
    """Nobody should lose their board, or their per-tile settings, to it."""

    def test_an_old_flat_tile_becomes_a_stack_of_one(self):
        got = normalise_tile(FLAT)
        self.assertEqual([c["card"] for c in got["cards"]], ["logbook"])
        self.assertEqual(got["active"], "t1")

    def test_the_card_keeps_the_uid_it_had(self):
        """Per-instance state hangs off it - the spectrum's audio input, the
        decoder's source, a web_embed's URL. A new uid is those settings
        gone, at the moment the operator is rearranging the board."""
        got = normalise_tile(FLAT)
        self.assertEqual(got["cards"][0]["uid"], "t1")
        self.assertEqual(got["cards"][0]["settings"], {"page_size": 30})

    def test_the_tile_keeps_the_uid_too(self):
        """Which is what makes the upgrade rewrite nothing: anything that
        was keyed on t1 still finds t1."""
        self.assertEqual(normalise_tile(FLAT)["uid"], "t1")

    def test_geometry_survives(self):
        got = normalise_tile(FLAT)
        self.assertEqual((got["x"], got["y"], got["w"], got["h"]), (2, 1, 5, 4))

    def test_a_stack_passes_through_unchanged(self):
        got = normalise_tile(STACK)
        self.assertEqual([c["uid"] for c in got["cards"]], ["a", "b"])
        self.assertEqual(got["active"], "b")

    def test_normalising_twice_changes_nothing(self):
        once = normalise_layout([FLAT, STACK])
        self.assertEqual(normalise_layout(once), once)


class TestRepair(unittest.TestCase):
    """Layouts get hand-edited, and get written by older versions."""

    def test_an_active_naming_a_card_that_is_not_here_is_repaired(self):
        """Otherwise the board draws an empty box under a working tab
        strip, which reads as the card being broken."""
        got = normalise_tile({**STACK, "active": "gone"})
        self.assertEqual(got["active"], "a")

    def test_a_tile_with_no_cards_is_dropped(self):
        self.assertIsNone(normalise_tile({"uid": "x", "cards": []}))
        self.assertIsNone(normalise_tile({"uid": "x"}))
        self.assertIsNone(normalise_tile("not a tile"))

    def test_a_card_with_no_id_is_dropped_but_its_siblings_are_not(self):
        got = normalise_tile({**STACK, "cards": [
            {"uid": "a", "card": "spots"}, {"uid": "junk"}]})
        self.assertEqual([c["uid"] for c in got["cards"]], ["a"])

    def test_a_card_with_no_uid_is_given_one(self):
        got = normalise_tile({"uid": "s", "cards": [{"card": "spots"}]})
        self.assertTrue(got["cards"][0]["uid"])

    def test_two_cards_sharing_a_uid_are_made_distinct(self):
        """They would otherwise share their settings and their identity
        through the proxy: editing one changes the other."""
        got = normalise_layout([
            {"uid": "p", "cards": [{"uid": "same", "card": "spots"}]},
            {"uid": "q", "cards": [{"uid": "same", "card": "decoder"}]}])
        uids = [c["uid"] for t in got for c in t["cards"]]
        self.assertEqual(len(set(uids)), 2, uids)
        self.assertEqual(got[0]["cards"][0]["uid"], "same")   # first keeps it

    def test_a_repaired_uid_is_still_the_tiles_active_one(self):
        got = normalise_layout([
            {"uid": "p", "cards": [{"uid": "same", "card": "spots"}]},
            {"uid": "q", "active": "same",
             "cards": [{"uid": "same", "card": "decoder"}]}])
        second = got[1]
        self.assertEqual(second["active"], second["cards"][0]["uid"])

    def test_junk_geometry_falls_back_rather_than_raising(self):
        got = normalise_tile({**FLAT, "x": "left", "h": None})
        self.assertEqual(got["x"], 0)
        self.assertEqual(got["h"], 3)

    def test_fields_nobody_declared_are_dropped(self):
        """A hand-edited file must not be able to smuggle through something
        that a later version reads as geometry."""
        got = normalise_tile({**FLAT, "nonsense": {"x": 99}})
        self.assertNotIn("nonsense", got)
        self.assertNotIn("nonsense", got["cards"][0])


class TestLookups(unittest.TestCase):
    def setUp(self):
        self.home = a_home([FLAT, STACK])
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cfg = Config(self.home)

    def test_a_card_is_found_by_its_uid_wherever_it_sits(self):
        """`tile(uid)` is what every per-tile setting goes through, and a
        card on a background tab is still a tile to the card."""
        self.assertEqual(self.cfg.tile("b")["card"], "decoder")
        self.assertEqual(self.cfg.tile("t1")["card"], "logbook")
        self.assertIsNone(self.cfg.tile("nope"))

    def test_the_box_is_found_separately_from_the_card(self):
        self.assertEqual(self.cfg.stack("b")["uid"], "s1")
        self.assertEqual(self.cfg.stack("a")["uid"], "s1")

    def test_settings_are_read_and_written_through_the_stack(self):
        self.assertEqual(self.cfg.tile_settings("a"), {"q": 1})
        self.cfg.set_tile_settings("a", {"q": 2, "new": True})
        self.assertEqual(self.cfg.tile_settings("a"), {"q": 2, "new": True})
        # and its neighbour in the same tile is untouched
        self.assertEqual(self.cfg.tile_settings("b"), {})

    def test_writing_settings_for_a_card_that_is_gone_says_so(self):
        with self.assertRaises(KeyError):
            self.cfg.set_tile_settings("nope", {"x": 1})

    def test_every_card_on_the_board_counts_as_active(self):
        """A card parked on a background tab still needs its process -
        reaping it would stop the card behind a tab one click away."""
        self.assertEqual(self.cfg.active_card_ids(),
                         {"logbook", "spots", "decoder"})

    def test_entries_are_every_card_in_board_order(self):
        self.assertEqual([e["uid"] for e in self.cfg.entries()],
                         ["t1", "a", "b"])


class TestSaving(unittest.TestCase):
    def setUp(self):
        self.home = a_home([])
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cfg = Config(self.home)

    def test_a_flat_layout_put_by_an_old_client_is_stored_as_stacks(self):
        self.cfg.save_layout([FLAT])
        on_disk = json.loads((self.home / "layout.json").read_text())
        self.assertIn("cards", on_disk[0])
        self.assertEqual(on_disk[0]["cards"][0]["uid"], "t1")

    def test_junk_in_a_put_cannot_be_written_and_then_fail_on_load(self):
        """The one place a bad layout could reach the file and only break
        at the next start, with the app already shut."""
        self.cfg.save_layout([FLAT, "nonsense", {"no": "card"}, None])
        self.assertEqual(len(self.cfg.load_layout()), 1)

    def test_a_round_trip_keeps_the_stack(self):
        self.cfg.save_layout([STACK])
        got = self.cfg.load_layout()[0]
        self.assertEqual([c["card"] for c in got["cards"]],
                         ["spots", "decoder"])
        self.assertEqual(got["active"], "b")


if __name__ == "__main__":
    unittest.main()
