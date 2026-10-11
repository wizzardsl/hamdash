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


class TestTheFlagsCarryTheirLicence(unittest.TestCase):
    """180 MIT-licensed SVGs are redistributed here, and MIT asks that the
    notice travel with them. Leaflet and proj4 already keep theirs beside
    the files; the flags had none until somebody looked."""

    def test_the_notice_ships_with_them(self):
        notice = FLAGS / "FLAG-ICONS-LICENSE.txt"
        self.assertTrue(notice.exists(),
                        "the flags are redistributed without their licence")
        said = notice.read_text(encoding="utf-8")
        self.assertIn("MIT License", said)
        self.assertIn("Copyright", said)


class TestWholeBlocks(unittest.TestCase):
    """Each country gets its whole ITU call-sign block, not the prefixes
    somebody happened to need.

    The table used to hold twelve of Germany's eighteen letters and four of
    Sweden's thirteen. Forty-five seconds of live spots turned up SZ1A,
    DP75, SE5E, DR4W, DE1L, 9W2E and AO5C with no country between them -
    none of them unusual, all of them invisible from reading the table.
    """

    def test_the_callsigns_that_were_live_and_unflagged(self):
        for call, iso in (("SZ1A", "gr"), ("DP75AB", "de"), ("SE5E", "se"),
                          ("DR4W", "de"), ("DE1L", "de"), ("9W2E", "my"),
                          ("AO5C", "es")):
            with self.subTest(call=call):
                self.assertEqual(dxcc.iso_for(call), iso)

    def test_a_block_is_whole(self):
        """Spot checks across the letters that were missing."""
        for call, iso in (("DI2ABC", "de"), ("DN1ABC", "de"), ("DQ4ABC", "de"),
                          ("SC3ABC", "se"), ("SJ9ABC", "se"), ("SL0ABC", "se"),
                          ("LC1ABC", "no"), ("LM9ABC", "no"),
                          ("PC2ABC", "nl"), ("PH1ABC", "nl"),
                          ("OP4ABC", "be"), ("OT5ABC", "be"),
                          ("UE3ABC", "ru"), ("UZ1ABC", "ua"), ("EM5ABC", "ua"),
                          ("PR7ABC", "br"), ("XB2ABC", "mx"),
                          ("SR5ABC", "pl"), ("HF0ABC", "pl"), ("3Z9ABC", "pl")):
            with self.subTest(call=call):
                self.assertEqual(dxcc.iso_for(call), iso)


class TestTheExceptionsSurvivedTheExpansion(unittest.TestCase):
    """Filling in a block must never swallow the entity sitting inside it.

    These are the ones that would be wrong and look right: HB is
    Switzerland in the middle of nothing, CN is Morocco where Cuba's
    letters run, OX and OY are Greenland and the Faroes inside Denmark's,
    BV is Taiwan inside China's, OH0 is Aland inside Finland's. Longest
    match is what keeps them, and this is the test that says so.
    """

    def test_a_neighbour_is_not_swallowed(self):
        for call, country in (("HB9ABC", "Switzerland"),
                              ("HB0ABC", "Liechtenstein"),
                              ("CN8ABC", "Morocco"),
                              ("OX3ABC", "Greenland"),
                              ("OY1ABC", "Faroe Islands"),
                              ("BV1ABC", "Taiwan"),
                              ("OH0ABC", "Aland Islands"),
                              ("UN7ABC", "Kazakhstan"),
                              ("UK8ABC", "Uzbekistan"),
                              ("HA5ABC", "Hungary"),
                              ("EA8ABC", "Canary Islands"),
                              ("EA6ABC", "Balearic Islands"),
                              ("CT3ABC", "Madeira"),
                              ("CU2ABC", "Azores"),
                              ("KL7ABC", "Alaska"),
                              ("KH6ABC", "Hawaii"),
                              ("LZ1ABC", "Bulgaria")):
            with self.subTest(call=call):
                self.assertEqual(dxcc.for_call(call)["country"], country)

    def test_china_and_taiwan_share_a_letter_and_not_an_entity(self):
        """BA-BL and BT, BY-BZ are China; BM-BQ and BU-BX are Taiwan. Both
        are in the table now, which they were not before."""
        self.assertEqual(dxcc.iso_for("BY1ABC"), "cn")
        self.assertEqual(dxcc.iso_for("BJ1ABC"), "cn")
        self.assertEqual(dxcc.iso_for("BX1ABC"), "tw")
        self.assertEqual(dxcc.iso_for("BM2ABC"), "tw")
