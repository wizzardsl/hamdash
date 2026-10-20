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
        """Off FT8's and FT4's spots, so the frequency cannot supply one
        either (5.357, the fixture's own, is 60 m FT8)."""
        got = sources.dx([dict(a_dx("up 2"), frequency=24896.0)])[0]
        self.assertEqual((got["mode"], got["mode_from"]), ("", ""))

    def test_other_sources_are_never_marked(self):
        """POTA and SOTA have a mode field; theirs is not from a comment."""
        pota = sources.pota([{"activator": "W1AW", "frequency": "14062",
                              "mode": "CW", "reference": "US-1",
                              "spotTime": "2026-10-10T23:40:00",
                              "comments": "FT8"}])[0]
        self.assertEqual((pota["mode"], pota["mode_from"]), ("CW", ""))


class TestARequestFurtherBack(unittest.TestCase):
    def test_a_band_between_the_request_and_the_mode(self):
        """Off the live feed: H49A on 18.100, "PSE 18 SSB good time" - the
        one spot of 500 where the frequency rule and the comment disagreed,
        and it was the comment rule that was wrong."""
        self.assertEqual(sources.mode_in_comment("PSE 18 SSB good time"), "")

    def test_a_request_long_before_the_mode_does_not_reach_it(self):
        self.assertEqual(
            sources.mode_in_comment("pse QSL via bureau thanks, FT8"), "FT8")


class TestModeByFrequency(unittest.TestCase):
    """FT8 and FT4 by where they are - the last resort, marked as such."""

    def test_the_watering_holes(self):
        for khz, want in ((14074.0, "FT8"), (7074.0, "FT8"), (3573.0, "FT8"),
                          (10136.0, "FT8"), (18100.0, "FT8"), (21074.0, "FT8"),
                          (28074.0, "FT8"), (50313.0, "FT8"), (144174.0, "FT8"),
                          (14080.0, "FT4"), (7047.5, "FT4"), (21140.0, "FT4")):
            self.assertEqual(sources.mode_by_frequency(khz * 1000), want, khz)

    def test_the_signal_sits_up_to_three_khz_above_the_dial(self):
        self.assertEqual(sources.mode_by_frequency(14_076_900), "FT8")
        self.assertEqual(sources.mode_by_frequency(14_077_100), "")
        self.assertEqual(sources.mode_by_frequency(14_073_900), "")

    def test_off_the_usual_spot_is_left_blank_not_misread(self):
        """S9R ran FT8 on 10.133 and H49A on 18.095 - real, and outside."""
        self.assertEqual(sources.mode_by_frequency(10_133_000), "")
        self.assertEqual(sources.mode_by_frequency(18_095_500), "")

    def test_cw_and_phone_segments_are_not_guessed(self):
        for hz in (14_025_000, 7_030_000, 14_250_000, 3_790_000):
            self.assertEqual(sources.mode_by_frequency(hz), "", hz)

    def test_nothing_is_nothing(self):
        for hz in (None, "", "abc", 0):
            self.assertEqual(sources.mode_by_frequency(hz), "", hz)


class TestWhichWins(unittest.TestCase):
    def test_a_blank_cluster_spot_on_ft8_gets_it_from_the_frequency(self):
        got = sources.dx([dict(a_dx(None), frequency=14074.0)])[0]
        self.assertEqual((got["mode"], got["mode_from"]), ("FT8", "frequency"))

    def test_the_comment_beats_the_frequency(self):
        got = sources.dx([dict(a_dx("SSB 59"), frequency=14074.0)])[0]
        self.assertEqual((got["mode"], got["mode_from"]), ("SSB", "comment"))

    def test_the_source_beats_both(self):
        """POTA has a mode field; it is used, unmarked, wherever it is."""
        got = sources.pota([{"activator": "W1AW", "frequency": "14074",
                             "mode": "CW", "reference": "US-1",
                             "spotTime": "2026-10-10T23:40:00"}])[0]
        self.assertEqual((got["mode"], got["mode_from"]), ("CW", ""))

    def test_a_source_that_gave_none_falls_back_like_the_cluster(self):
        got = sources.pota([{"activator": "W1AW", "frequency": "14074",
                             "mode": "", "reference": "US-1",
                             "spotTime": "2026-10-10T23:40:00"}])[0]
        self.assertEqual((got["mode"], got["mode_from"]), ("FT8", "frequency"))

    def test_the_request_falls_through_to_the_frequency(self):
        got = sources.dx([dict(a_dx("PSE 18 SSB good time"), frequency=18100.0)])[0]
        self.assertEqual((got["mode"], got["mode_from"]), ("FT8", "frequency"))


class TestSidebandInComment(unittest.TestCase):
    def test_a_named_sideband_is_kept(self):
        for info, want in (("LSB JN54kp -> KN13nq", "LSB"), ("tnx usb qso", "USB"),
                           ("LSB, lsb 59", "LSB")):
            self.assertEqual(sources.sideband_in_comment(info), want, info)

    def test_no_sideband_or_both_is_none(self):
        for info in ("SSB 59", "USB or LSB", "PSE LSB", "FT8 -10", "", None):
            self.assertEqual(sources.sideband_in_comment(info), "", info)

    def test_a_cluster_spot_carries_it(self):
        got = sources.dx([dict(a_dx("LSB 59 tnx"), frequency=14250.0)])[0]
        self.assertEqual((got["mode"], got["mode_from"], got["sideband"]),
                         ("SSB", "comment", "LSB"))

    def test_other_sources_carry_none(self):
        got = sources.pota([{"activator": "W1IP", "frequency": "14274",
                             "mode": "SSB", "reference": "US-1",
                             "spotTime": "2026-10-10T23:40:00",
                             "comments": "LSB"}])[0]
        self.assertEqual(got["sideband"], "")


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

    def test_a_cluster_line_keeps_its_sideband(self):
        got = self.telnet.parse(
            "DX de F6JON:      7185.0  EA3CU        LSB 59 tnx                     2140Z",
            kind="cluster")
        self.assertEqual((got["mode"], got["sideband"]), ("SSB", "LSB"))

    def test_a_cluster_line_on_ft8_with_no_comment_mode(self):
        got = self.telnet.parse(
            "DX de OK1ABC:    14074.0  JA1XYZ       tnx QSO                         2140Z",
            kind="cluster")
        self.assertEqual((got["mode"], got["mode_from"]), ("FT8", "frequency"))

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

    def test_each_kind_of_guess_says_which_it_is(self):
        m = re.search(r"const GUESSED = \{(.*?)\};", PAGE, re.S)
        self.assertIsNotNone(m)
        self.assertRegex(m.group(1), r"comment:\s*\"From the spotter's comment")
        self.assertRegex(m.group(1), r"frequency:\s*\"From the frequency")

    def test_the_tune_button_asks_tunemode_what_it_may_send(self):
        """Clicking the frequency sets the mode first, then tunes. Which
        guessed modes may switch the rig - SSB from a comment, nothing
        else - is `tuneMode`, tested in tests/js/spots-sideband.test.js."""
        self.assertRegex(PAGE, r'data-mode="\$\{\s*esc\(tuneMode\(s\)\)\}"')
        self.assertRegex(PAGE, r'data-sideband="\$\{esc\(s\.sideband')

    def test_the_filter_still_keeps_spots_with_no_mode(self):
        self.assertRegex(PAGE, r"if \(filters\.mode && !s\.mode && s\.kind !== 'dx'\)"
                               r" return false;")


if __name__ == "__main__":
    unittest.main()
