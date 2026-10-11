"""Callsign prefix to country and flag.

A prefix table is always approximate, but the cases below are ones it must not
get wrong: a flag beside a callsign is read as fact, and the awkward prefixes
are exactly the ones an operator notices.

Run with:  python -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

from hamdash.services import dxcc

FLAGS = Path(__file__).resolve().parent.parent / "hamdash" / "static" / "flags"


class TestPrefixes(unittest.TestCase):
    def test_plain_callsigns(self):
        for call, iso in [("W1AW", "us"), ("VE7CC", "ca"), ("G3TXF", "gb"),
                          ("JA1XYZ", "jp"), ("VK3ABC", "au"), ("LU1ABC", "ar"),
                          ("ZL1ABC", "nz"), ("9A1AA", "hr"), ("EA3ABC", "es")]:
            with self.subTest(call=call):
                self.assertEqual(dxcc.iso_for(call), iso)

    def test_longer_prefixes_win(self):
        """KH6 is Hawaii, not K for the United States; UA9 is Asiatic Russia."""
        self.assertEqual(dxcc.for_call("KH6XX")["country"], "Hawaii")
        self.assertEqual(dxcc.for_call("KL7AA")["country"], "Alaska")
        self.assertEqual(dxcc.for_call("UA9ABC")["country"], "Asiatic Russia")
        self.assertEqual(dxcc.for_call("EA8XX")["country"], "Canary Islands")

    def test_a_dxcc_entity_is_not_always_a_country(self):
        """Scotland, Wales and England are three entities and one flag; Alaska
        and Hawaii are the United States. The flag answers where somebody is,
        not what counts separately for an award."""
        for call in ("G3TXF", "GM4XYZ", "GW1ABC", "GI4ABC"):
            with self.subTest(call=call):
                self.assertEqual(dxcc.iso_for(call), "gb")
        self.assertEqual(dxcc.iso_for("KH6XX"), "us")
        self.assertEqual(dxcc.iso_for("KL7AA"), "us")

    def test_places_with_their_own_flag_keep_it(self):
        self.assertEqual(dxcc.iso_for("KP4ABC"), "pr")
        self.assertEqual(dxcc.iso_for("VP9XX"), "bm")

    def test_a_portable_prefix_changes_the_country(self):
        """W1AW/VE7 is operating in Canada; the prefix is the point of it."""
        self.assertEqual(dxcc.iso_for("W1AW/VE7"), "ca")
        self.assertEqual(dxcc.iso_for("VE7XYZ/W1"), "us")

    def test_a_portable_suffix_does_not(self):
        for call in ("G0ABC/P", "VE7XYZ/M", "W1AW/QRP", "ZL1ABC/MM"):
            with self.subTest(call=call):
                self.assertEqual(dxcc.iso_for(call), dxcc.iso_for(call.split("/")[0]))

    def test_an_unknown_prefix_says_nothing_rather_than_guessing(self):
        # Not `ZZ9ZZZ`, which this used to use: ZZ turned out to be one of
        # Brazil's real blocks, which is the sort of thing a short prefix
        # table gets wrong until something is measured against it. `0A` and
        # `QQ` are not allocated to anybody.
        for call in ("0A0AAA", "QQ1QQQ"):
            with self.subTest(call=call):
                d = dxcc.for_call(call)
                self.assertEqual(d["iso"], "")
                self.assertEqual(d["country"], "")

    def test_empty(self):
        self.assertEqual(dxcc.iso_for(""), "")

    def test_everything_is_flagged_approximate(self):
        """It is a prefix table. Nothing from it should be presented as fact."""
        for call in ("W1AW", "ZZ9ZZZ", "VK3ABC"):
            self.assertTrue(dxcc.for_call(call)["approximate"])


class TestFlagFiles(unittest.TestCase):
    def test_every_iso_code_has_a_flag(self):
        """A missing file is a broken image beside somebody's callsign."""
        missing = sorted({v[1] for v in dxcc.PREFIXES.values()}
                         - {p.stem for p in FLAGS.glob("*.svg")})
        self.assertEqual(missing, [], f"no flag file for {missing}")

    def test_codes_are_lowercase_two_letters(self):
        for _, iso, _ in dxcc.PREFIXES.values():
            self.assertRegex(iso, r"^[a-z]{2}$")

    def test_the_files_are_actually_svg(self):
        for name in ("us", "ca", "gb", "jp"):
            head = (FLAGS / f"{name}.svg").read_text(encoding="utf-8")[:200]
            self.assertIn("<svg", head)

    def test_locators_are_valid(self):
        from hamdash.services import grid
        for country, iso, loc in dxcc.PREFIXES.values():
            with self.subTest(country=country):
                self.assertTrue(grid.valid(loc), f"{country}: {loc!r}")


if __name__ == "__main__":
    unittest.main()
