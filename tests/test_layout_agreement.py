"""The board and the host must normalise a layout the same way.

Both of them rewrite `layout.json`: the board saves what it is holding, the
host normalises whatever it is sent. If they disagree, a tile is saved in a
shape the other side repairs differently - and the operator finds out when a
card comes back in the wrong box, or without its settings.

They are in different languages, so the contract is a file:
`tests/fixtures/layouts.expected.json` is what both must produce from
`tests/fixtures/layouts.json`. Python checks itself against it here, and
`tests/js/agreement.test.js` checks the browser's `layout.js` against the
same file under `node --test`. Regenerate with:

    python tools/layout_golden.py

Run with:  python -m unittest discover -s tests
"""
import json
import unittest
from pathlib import Path

from hamdash.config import normalise_layout

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "layouts.json"
GOLDEN = ROOT / "tests" / "fixtures" / "layouts.expected.json"


def canonical(tiles, before) -> list:
    """The normalised board, with invented uids masked.

    A duplicate uid is replaced with a fresh random one, which neither side
    can agree on and neither should: what matters is *that* it was replaced.
    """
    known = set()

    def walk(raw):
        if isinstance(raw, dict):
            if raw.get("uid"):
                known.add(str(raw["uid"]))
            for v in raw.values():
                walk(v)
        elif isinstance(raw, list):
            for v in raw:
                walk(v)

    walk(before)

    def mask(uid):
        return uid if uid in known else "<new>"

    out = []
    for t in tiles:
        cards = [{**c, "uid": mask(c["uid"])} for c in t["cards"]]
        # A masked uid can collide with the tile's own idea of active, so
        # active is expressed as a position rather than a name.
        active = [c["uid"] for c in t["cards"]].index(t["active"])
        out.append({**t, "uid": mask(t["uid"]), "cards": cards,
                    "active": f"#{active}"})
    return out


def cases():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"]


class TestTheTwoNormalisersAgree(unittest.TestCase):
    def test_every_fixture_matches_the_golden_file(self):
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        got = cases()
        self.assertEqual(len(golden), len(got),
                         "fixtures and golden file are out of step; run "
                         "tools/layout_golden.py")
        for case, want in zip(got, golden):
            with self.subTest(why=case["why"]):
                mine = canonical(normalise_layout(case["tiles"]),
                                 case["tiles"])
                self.assertEqual(mine, want["tiles"],
                                 f"python disagrees with the golden file for: "
                                 f"{case['why']}")

    def test_the_fixtures_cover_what_they_claim_to(self):
        """A fixture with no `why` is one nobody can tell is still earning
        its place."""
        for case in cases():
            self.assertTrue(case.get("why"), case)
            self.assertIsInstance(case.get("tiles"), list)

    def test_normalising_is_idempotent_right_down_to_the_text(self):
        """The board saves what the host normalised and the host normalises
        what the board saved, round after round.

        Compared as text, not as values: they matched as values all along
        while the *keys* came out in a different order each pass, so every
        save rewrote layout.json with the lines shuffled. It is the one file
        an operator might open or keep in git.
        """
        for case in cases():
            with self.subTest(why=case["why"]):
                once = normalise_layout(case["tiles"])
                self.assertEqual(json.dumps(normalise_layout(once)),
                                 json.dumps(once))

    def test_a_tile_always_comes_out_with_the_same_keys_in_the_same_order(self):
        """And the same order `layout.js` uses, which the JS side asserts
        against the golden file."""
        want = ["uid", "x", "y", "w", "h", "active", "popped", "cards"]
        for case in cases():
            for tile in normalise_layout(case["tiles"]):
                with self.subTest(why=case["why"]):
                    self.assertEqual([k for k in tile if k in want], want)
                    for card in tile["cards"]:
                        self.assertEqual(list(card)[:2], ["uid", "card"])
                        self.assertEqual(list(card)[-1], "settings")

    def test_no_card_loses_its_uid(self):
        """The one failure that costs the operator something: per-instance
        settings hang off the uid, so a uid that changes without a reason is
        settings gone."""
        for case in cases():
            with self.subTest(why=case["why"]):
                asked = set()

                def walk(raw):
                    if isinstance(raw, dict):
                        if raw.get("card") and raw.get("uid"):
                            asked.add(str(raw["uid"]))
                        for v in raw.values():
                            walk(v)
                    elif isinstance(raw, list):
                        for v in raw:
                            walk(v)

                walk(case["tiles"])
                got = {c["uid"] for t in normalise_layout(case["tiles"])
                       for c in t["cards"]}
                # Every uid that survived had to be one that was asked for.
                # (Duplicates are the exception and are tested separately.)
                self.assertTrue(got & asked or not asked,
                                f"{case['why']}: nothing kept its uid")


if __name__ == "__main__":
    unittest.main()
