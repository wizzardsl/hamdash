"""A cluster spot whose comment names its mode outright gets that mode.

DX Summit's records have no mode field - checked against the live feed:
call, frequency, time, country, coordinates, spotter and a free-text
`info`. Every cluster spot on the card therefore showed no mode, 42 of 42
at the time it was reported, while the comment often said it plainly:
"FT8 -14 dB 1028 Hz", "FT4 +04dB from JO83 2015Hz". A spotter typing FT8
is saying what they heard, no less than POTA's own mode field, which is
also typed by a spotter. So an outright mode is taken - marked as coming
from the comment, shown as such, filtered on, and **not** handed to the
radio when the frequency is clicked, because that would switch the rig on
the strength of a comment.

Run with:  python -m unittest discover -s tests
"""
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "plugins" / "spots"
sys.path.insert(0, str(HERE))

import sources                                          # noqa: E402

PAGE = (HERE / "index.html").read_text(encoding="utf-8")


def a_dx(info):
    return {"info": info, "dx_country": "Spain", "de_call": "F6JON",
            "frequency": 5357.0, "time": "2026-10-18T21:40:00",
            "dx_call": "EA3CU", "id": 1}


class TestModeInComment(unittest.TestCase):
    def test_comments_off_the_live_feed(self):
        for info, want in (("FT8 -14 dB 1028 Hz", "FT8"),
                           ("FT4 +04dB from JO83 2015Hz", "FT4"),
                           ("FT8 2216 Hz", "FT8"),
                           ("Harry kalimera on SV net LP", ""),
                           ("up", ""),
                           ("PAQP BER", "")):
            self.assertEqual(sources.mode_in_comment(info), want, info)

    def test_case_does_not_matter_for_a_mode_name(self):
        self.assertEqual(sources.mode_in_comment("cw 25 wpm"), "CW")
        self.assertEqual(sources.mode_in_comment("ft8 -10"), "FT8")

    def test_sideband_is_ssb(self):
        """POTA says SSB; a spotter writes USB or LSB. One filter entry."""
        for info in ("USB 59", "lsb 5/9", "SSB"):
            self.assertEqual(sources.mode_in_comment(info), "SSB", info)

    def test_am_and_fm_only_in_capitals(self):
        """They are also English."""
        self.assertEqual(sources.mode_in_comment("AM on 3885"), "AM")
        self.assertEqual(sources.mode_in_comment("I am QRV"), "")
        self.assertEqual(sources.mode_in_comment("fm here"), "")

    def test_a_whole_word_only(self):
        """CWT is a CWops contest, not a mode; "via PSK" is PSK Reporter."""
        for info in ("CWT", "CWops", "via PSK reporter", "SSBN", "FT8A"):
            self.assertEqual(sources.mode_in_comment(info), "", info)

    def test_two_modes_is_no_mode(self):
        """A question, not an answer."""
        for info in ("CW or SSB", "FT8 then CW", "SSB/CW"):
            self.assertEqual(sources.mode_in_comment(info), "", info)

    def test_a_mode_asked_for_is_not_the_mode_heard(self):
        """Off the live feed: H49A on 12 m, comment "PSE SSB" - a spotter
        asking it to go to sideband, which it was not on."""
        for info in ("PSE SSB", "pls cw", "QSY FT8", "please, SSB", "try FT4"):
            self.assertEqual(sources.mode_in_comment(info), "", info)
        self.assertEqual(sources.mode_in_comment("CW pse QRS"), "CW")

    def test_the_same_mode_twice_is_still_that_mode(self):
        self.assertEqual(sources.mode_in_comment("FT8 ... FT8"), "FT8")
        self.assertEqual(sources.mode_in_comment("USB, lsb"), "SSB")


class TestTheClusterSpot(unittest.TestCase):
    def test_takes_the_mode_and_says_where_it_came_from(self):
        got = sources.dx([a_dx("FT8 -14 dB 1028 Hz")])[0]
        self.assertEqual((got["mode"], got["mode_from"]), ("FT8", "comment"))
        self.assertEqual(got["comment"], "FT8 -14 dB 1028 Hz")

    def test_no_named_mode_stays_empty_and_unmarked(self):
        got = sources.dx([a_dx("up 2")])[0]
        self.assertEqual((got["mode"], got["mode_from"]), ("", ""))

    def test_other_sources_are_never_marked(self):
        """POTA and SOTA have a mode field; theirs is not from a comment."""
        pota = sources.pota([{"activator": "W1AW", "frequency": "14062",
                              "mode": "CW", "reference": "US-1",
                              "spotTime": "2026-10-10T23:40:00",
                              "comments": "FT8"}])[0]
        self.assertEqual((pota["mode"], pota["mode_from"]), ("CW", ""))


class TestTheTelnetFeeds(unittest.TestCase):
    """The same two kinds of mode arrive over telnet, and must not be
    confused: RBN's is a skimmer's measurement, a cluster's is a person's
    comment - which the telnet cluster used to take, unmarked, from its
    first word."""

    def setUp(self):
        import telnet
        self.telnet = telnet

    def test_rbn_mode_is_a_measurement(self):
        got = self.telnet.parse(
            "DX de K9LC-#:     14025.0  JA1ABC       CW    18 dB  25 WPM  CQ      0336Z",
            kind="rbn")
        self.assertEqual((got["mode"], got["mode_from"]), ("CW", ""))

    def test_a_cluster_line_is_a_comment(self):
        got = self.telnet.parse(
            "DX de F6JON:      5357.0  EA3CU        FT8 -14 dB 1028 Hz             2140Z",
            kind="cluster")
        self.assertEqual((got["mode"], got["mode_from"]), ("FT8", "comment"))

    def test_a_cluster_line_naming_no_mode_has_none(self):
        got = self.telnet.parse(
            "DX de IK0PHY:    24896.0  H49A         up                              2140Z",
            kind="cluster")
        self.assertEqual((got["mode"], got["mode_from"]), ("", ""))


class TestThePage(unittest.TestCase):
    def test_a_comment_mode_is_marked_in_the_table(self):
        self.assertRegex(PAGE, r"s\.mode_from \? ' guessed' : ''")
        self.assertRegex(PAGE, r"td\.guessed\s*\{[^}]*font-style:\s*italic")
        self.assertIn("From the spotter's comment", PAGE)

    def test_a_comment_mode_is_not_sent_to_the_radio(self):
        """Clicking the frequency sets the mode first, then tunes. A mode
        read from a comment must not switch the rig."""
        m = re.search(r'data-mode="\$\{([^}]*)\}"', PAGE, re.S)
        self.assertIsNotNone(m, "the tune button's data-mode")
        self.assertIn("mode_from", m.group(1))

    def test_the_filter_still_keeps_spots_with_no_mode(self):
        self.assertRegex(PAGE, r"if \(filters\.mode && !s\.mode && s\.kind !== 'dx'\)"
                               r" return false;")


if __name__ == "__main__":
    unittest.main()
