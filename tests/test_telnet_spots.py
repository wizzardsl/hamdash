"""Spots that arrive on a socket, and the line format they arrive in.

Every line below was copied off the live service. Nothing here opens a
socket: what is worth guarding is the parsing, because a line that fails to
parse is a spot that silently never appears, and a line parsed wrongly is a
station on a frequency it is not on.

Run with:  python -m unittest discover -s tests
"""
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "plugins" / "spots"))

import telnet                                           # noqa: E402

# Real lines, from telnet.reversebeacon.net ports 7000 and 7001.
RBN_CW = "DX de KM3T-3-#:  3531.00  KN3A           CW    22 dB  21 WPM  CQ      0336Z"
RBN_UHF = "DX de JI1HFJ-#:430051.30  7K1PEO/2       CW    22 dB  28 WPM  CQ      0336Z"
RBN_FT8 = "DX de F4BPO-#:   3573.00  SP1MGM         FT8  -10 dB          CQ      0336Z"
RBN_GRID = "DX de KM3T-3-#: 14074.00  N5XZ           FT8   -7 dB  EL29    CQ      0336Z"
HUMAN = "DX de W3LPL:     14025.0  JA1XYZ       CW 599 TNX QSO              1234Z"

NOISE = [
    "Please enter your call: Hello, VA7SLL! Connected.",
    "Local users: 503",
    "Spot rate: 5/s (17,087/h)",
    "VA7SLL de RELAY 11-Oct-2026 03:36Z >",
    "Greetings from the VE7CC-1 cluster.",
    "To see FT8 spots you MUST enter SET/FT8",
    "",
    "   ",
]

# 03:40Z on an arbitrary day, so the HHMM anchoring can be reasoned about.
NOW = 1760000000.0

# The fixtures are stamped 0336Z, and `recent()` measures against the real
# clock - so a window of an hour only passes while the machine's clock
# happens to be near 03:36 UTC, which is exactly when CI first ran these.
# Where the window is not what is being tested, it is taken out of the way.
ANY_AGE = 10 ** 9


class TestTheLineFormat(unittest.TestCase):
    def test_an_rbn_cw_spot(self):
        s = telnet.parse(RBN_CW, "rbn", NOW)
        self.assertEqual(s["call"], "KN3A")
        self.assertEqual(s["hz"], 3_531_000)
        self.assertEqual(s["band"], "80m")
        self.assertEqual(s["mode"], "CW")
        self.assertEqual(s["spotter"], "KM3T-3-#")
        self.assertEqual(s["snr_db"], 22)
        self.assertEqual(s["wpm"], 21)

    def test_a_frequency_that_fills_its_column(self):
        """`JI1HFJ-#:430051.30` has no space after the colon, because the
        field is fixed width and 430 MHz fills it. Splitting on whitespace
        puts the frequency in the spotter's name and drops the spot."""
        s = telnet.parse(RBN_UHF, "rbn", NOW)
        self.assertIsNotNone(s, "a 70cm spot was dropped")
        self.assertEqual(s["spotter"], "JI1HFJ-#")
        self.assertEqual(s["hz"], 430_051_300)
        self.assertEqual(s["band"], "70cm")
        self.assertEqual(s["call"], "7K1PEO/2")

    def test_a_negative_signal_report(self):
        """FT8 reports are mostly negative, and a parser that only takes
        digits reads -10 as 10 - a strong signal where there is a weak one."""
        s = telnet.parse(RBN_FT8, "rbn", NOW)
        self.assertEqual(s["snr_db"], -10)
        self.assertEqual(s["mode"], "FT8")
        self.assertIsNone(s["wpm"])

    def test_a_grid_when_the_skimmer_gives_one(self):
        s = telnet.parse(RBN_GRID, "rbn", NOW)
        self.assertEqual(s["grid"], "EL29")
        self.assertEqual(s["call"], "N5XZ")

    def test_a_spot_typed_by_a_person(self):
        """A cluster spot has no dB or WPM and its comment is free text."""
        s = telnet.parse(HUMAN, "cluster", NOW)
        self.assertEqual(s["call"], "JA1XYZ")
        self.assertEqual(s["hz"], 14_025_000)
        self.assertEqual(s["mode"], "CW")
        self.assertIsNone(s["snr_db"])
        self.assertIn("TNX QSO", s["comment"])

    def test_banner_and_chatter_are_not_spots(self):
        """A login banner drawn as a spot is a station called Please on no
        band, which reads as the card being broken."""
        for line in NOISE:
            with self.subTest(line=line[:40]):
                self.assertIsNone(telnet.parse(line, "rbn", NOW))

    def test_every_spot_lands_on_a_band(self):
        for line in (RBN_CW, RBN_UHF, RBN_FT8, RBN_GRID, HUMAN):
            with self.subTest(line=line[:30]):
                self.assertTrue(telnet.parse(line, "rbn", NOW)["band"])

    def test_the_kind_is_carried_through(self):
        self.assertEqual(telnet.parse(RBN_CW, "rbn", NOW)["kind"], "rbn")
        self.assertEqual(telnet.parse(HUMAN, "cluster", NOW)["kind"], "cluster")

    def test_a_spot_has_the_same_shape_as_the_polled_ones(self):
        """One list on screen, so one record shape."""
        import sources
        http_row = sources.pota([{
            "activator": "W1AW", "frequency": "14074.0", "mode": "FT8",
            "reference": "US-0001", "spotTime": "2026-10-10T23:20:15"}])[0]
        live = telnet.parse(RBN_CW, "rbn", NOW)
        missing = set(http_row) - set(live)
        self.assertFalse(missing, f"the live feed is missing {missing}")


class TestTheClock(unittest.TestCase):
    """Spots carry HHMM and no date."""

    def test_a_time_earlier_today(self):
        at = telnet._when("0336", NOW)
        self.assertLessEqual(at, NOW)
        self.assertGreater(at, NOW - 86400)

    def test_a_time_just_after_midnight_is_today(self):
        just_after = NOW - (NOW % 86400) + 60      # the clock says 00:01Z
        self.assertAlmostEqual(telnet._when("0001", just_after), just_after,
                               delta=1)

    def test_a_spot_from_before_midnight_is_not_dated_into_the_future(self):
        """2359 arriving at 0001 belongs to yesterday. Dated today it is
        nearly a day ahead and sits at the top of the list for ever."""
        midnight = NOW - (NOW % 86400) + 60        # 00:01Z
        at = telnet._when("2359", midnight)
        self.assertLess(at, midnight, "it was dated into the future")
        self.assertGreater(at, midnight - 7200)

    def test_rubbish_is_now_rather_than_1970(self):
        self.assertAlmostEqual(telnet._when("", NOW), NOW, delta=1)
        self.assertAlmostEqual(telnet._when("ZZZZ", NOW), NOW, delta=1)


class TestWhatItKeeps(unittest.TestCase):
    def a_stream(self):
        st = telnet.SpotStream.__new__(telnet.SpotStream)
        st.kind = "rbn"
        st.spots = __import__("collections").deque(maxlen=telnet.KEEP)
        st.status, st.error = "running", ""
        st.connected_at = st.last_line = time.time()
        st.seq = 0
        st._lock = __import__("threading").Lock()
        st.on_spot = None
        return st

    def test_it_is_bounded(self):
        """RBN announced five spots a second while this was written. A feed
        that keeps everything is a feed that eats the machine."""
        st = self.a_stream()
        for i in range(telnet.KEEP * 3):
            st._line(RBN_CW.replace("KN3A", f"K{i % 9000}AA"))
        self.assertEqual(len(st.spots), telnet.KEEP)

    def test_watching_one_callsign_is_what_rbn_is_for(self):
        """Every skimmer that can hear one station, which is the question
        nothing else here answers."""
        st = self.a_stream()
        st._line(RBN_CW)                               # KN3A
        st._line(RBN_CW.replace("KN3A", "VA7SLL"))
        st._line(RBN_CW.replace("KN3A", "VA7SLL").replace("KM3T-3", "W3OA"))
        mine = st.recent(ANY_AGE, "VA7SLL")
        self.assertEqual(len(mine), 2)
        self.assertEqual({s["spotter"] for s in mine},
                         {"KM3T-3-#", "W3OA-#"})

    def test_watching_nothing_shows_everything(self):
        st = self.a_stream()
        st._line(RBN_CW)
        st._line(RBN_CW.replace("KN3A", "VA7SLL"))
        self.assertEqual(len(st.recent(ANY_AGE, "")), 2)

    def test_the_case_of_the_callsign_does_not_matter(self):
        st = self.a_stream()
        st._line(RBN_CW.replace("KN3A", "VA7SLL"))
        self.assertEqual(len(st.recent(ANY_AGE, "va7sll")), 1)

    def test_old_spots_fall_out_of_the_window(self):
        st = self.a_stream()
        st._line(RBN_CW)
        st.spots[0]["at"] = time.time() - 7200
        self.assertEqual(st.recent(600, ""), [])

    def test_newest_first(self):
        st = self.a_stream()
        st._line(RBN_CW)
        st._line(RBN_CW.replace("KN3A", "W1AW"))
        # Both lines carry the same HHMM, so say which is which rather than
        # relying on the order they went in.
        now = time.time()
        st.spots[0]["at"] = now - 600               # KN3A, ten minutes ago
        st.spots[1]["at"] = now - 10                # W1AW, just now
        self.assertEqual([s["call"] for s in st.recent(ANY_AGE, "")],
                         ["W1AW", "KN3A"])

    def test_spots_in_the_same_minute_come_out_newest_first(self):
        """The wire carries HHMM and nothing finer, so most spots on a live
        feed tie. Sorting on that alone falls back to insertion order and
        puts the oldest of them on top, which is the wrong way round."""
        st = self.a_stream()
        for call in ("FIRST", "SECOND", "THIRD"):
            st._line(RBN_CW.replace("KN3A", call))
        same = {s["at"] for s in st.spots}
        self.assertEqual(len(same), 1, "the fixture should tie on time")
        self.assertEqual([s["call"] for s in st.recent(ANY_AGE, "")],
                         ["THIRD", "SECOND", "FIRST"])

    def test_the_tie_break_does_not_read_a_clock(self):
        """It used to stamp `time.time()`, which ticks about every 15.6 ms on
        Windows - so a burst of spots all carried the same stamp, the sort
        stayed stable, and the oldest came out on top again. CI caught it;
        a live feed did not, because five spots a second are further apart
        than the tick. Here the clock is stopped dead."""
        st = self.a_stream()
        with unittest.mock.patch("telnet.time.time", lambda: NOW):
            for call in ("FIRST", "SECOND", "THIRD"):
                st._line(RBN_CW.replace("KN3A", call))
            self.assertEqual(len({s["arrived"] for s in st.spots}), 3,
                             "two spots were given the same arrival order")
            self.assertEqual([s["call"] for s in st.recent(ANY_AGE, "")],
                             ["THIRD", "SECOND", "FIRST"])

    def test_arrival_order_is_shared_across_feeds(self):
        """One list on screen merges them, so RBN and a cluster have to be
        orderable against each other, not just each against itself."""
        rbn, cluster = self.a_stream(), self.a_stream()
        rbn._line(RBN_CW.replace("KN3A", "EARLY"))
        cluster._line(RBN_CW.replace("KN3A", "MIDDLE"))
        rbn._line(RBN_CW.replace("KN3A", "LATE"))
        merged = rbn.recent(ANY_AGE, "") + cluster.recent(ANY_AGE, "")
        merged.sort(key=lambda s: (s["at"], s["arrived"]), reverse=True)
        self.assertEqual([s["call"] for s in merged],
                         ["LATE", "MIDDLE", "EARLY"])

    def test_chatter_does_not_become_a_spot(self):
        st = self.a_stream()
        for line in NOISE:
            st._line(line)
        self.assertEqual(len(st.spots), 0)


class TestBeingAGoodGuest(unittest.TestCase):
    """These are logins on somebody else's public network."""

    def test_it_will_not_connect_without_a_callsign(self):
        """Every one of these networks identifies you by callsign.
        Connecting without one, or with somebody else's, is not something to
        do on the operator's behalf."""
        st = telnet.SpotStream("rbn", telnet.RBN_HOST, telnet.RBN_CW_PORT,
                               "", ())
        self.addCleanup(st.stop)
        for _ in range(50):
            if st.status == "error":
                break
            time.sleep(0.02)
        self.assertEqual(st.status, "error")
        self.assertIn("callsign", st.error)

    def test_reconnecting_backs_off(self):
        self.assertGreaterEqual(telnet.RETRY_MIN, 5.0)
        self.assertGreaterEqual(telnet.RETRY_MAX, 60.0)

    def test_a_silent_connection_is_treated_as_dead(self):
        """A dropped TCP session looks exactly like a quiet one until
        something is written to it."""
        self.assertGreater(telnet.QUIET_DEATH, 60.0)
        self.assertLess(telnet.QUIET_DEATH, 3600.0)


if __name__ == "__main__":
    unittest.main()
