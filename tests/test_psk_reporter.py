"""PSK Reporter card: the live feed, the rows it makes, and the store.

The card listens to PSK Reporter's MQTT feed rather than polling their
query API. The polling is gone and so are the tests of it - the five
minute floor, the `lastseqno` marker, the back-off after a rate limit -
because there is nothing left to poll. What replaced them is the
conversion from a spot to a row, which is where the two sources differ
in words rather than in meaning, and the manager that owns the
connections.

`mqtt.py` is tested separately in `test_psk_mqtt.py`, over bytes.
Nothing here touches the network.

Run with:  python -m unittest discover -s tests
"""
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "psk_reporter"))

import card as psk                                            # noqa: E402
import store as pskstore                                      # noqa: E402

# A spot exactly as the live feed sends one.
SPOT = {"sq": 73665028629, "f": 14076550, "md": "FT8", "rp": -17,
        "t": 1791866549, "t_tx": 1791866535, "sc": "VA7ZT",
        "sl": "DN19di41", "rc": "BG2MGR", "rl": "PN23pv",
        "sa": 1, "ra": 318, "b": "20m",
        "topic": "pskr/filter/v2/20m/FT8/VA7ZT/BG2MGR/DN19/PN23/1/318"}


class TestASpotBecomesARow(unittest.TestCase):
    """The feed and the old query say the same things in different words."""

    def test_the_two_callsigns_and_their_squares(self):
        r = psk.as_row("VA7ZT", SPOT)
        self.assertEqual(r["tx"], "VA7ZT")
        self.assertEqual(r["rx"], "BG2MGR")
        self.assertEqual(r["tx_grid"], "DN19di41")
        self.assertEqual(r["rx_grid"], "PN23pv")

    def test_frequency_signal_and_mode(self):
        r = psk.as_row("VA7ZT", SPOT)
        self.assertEqual(r["hz"], 14076550)
        self.assertEqual(r["snr"], -17)
        self.assertEqual(r["mode"], "FT8")

    def test_the_band_is_derived_from_the_one_band_table(self):
        """Not `b` off the wire: three band tables had already drifted
        apart in this tree once, and 5.3 MHz was 60m to one of them and
        out of band to another."""
        self.assertEqual(psk.as_row("VA7ZT", SPOT)["band"], "20m")

    def test_the_time_is_when_it_was_transmitted(self):
        """`t_tx`, not `t`. The query API gave `flowStartSeconds`, which
        is the transmit time, and `at` is part of the primary key - two
        sources disagreeing about when a spot happened would show up as
        duplicate rows seconds apart."""
        self.assertEqual(psk.as_row("VA7ZT", SPOT)["at"], 1791866535)

    def test_which_side_the_watched_station_was_on(self):
        """The feed does not say, so it is read off the callsigns."""
        tx = psk.as_row("VA7ZT", SPOT)
        self.assertTrue(tx["is_tx"])
        self.assertFalse(tx["is_rx"])
        rx = psk.as_row("BG2MGR", SPOT)
        self.assertFalse(rx["is_tx"])
        self.assertTrue(rx["is_rx"])

    def test_a_grid_watch_is_neither_side(self):
        """A square is not a station, and claiming it transmitted would
        put it on the wrong end of every path drawn."""
        r = psk.as_row("DN19", SPOT)
        self.assertFalse(r["is_tx"])
        self.assertFalse(r["is_rx"])

    def test_a_lower_case_callsign_still_matches(self):
        self.assertTrue(psk.as_row("va7zt", SPOT)["is_tx"])

    def test_a_spot_with_no_callsign_is_dropped(self):
        for missing in ("sc", "rc"):
            with self.subTest(missing=missing):
                self.assertIsNone(psk.as_row("VA7ZT", dict(SPOT, **{missing: ""})))

    def test_rubbish_numbers_drop_the_spot_rather_than_raising(self):
        self.assertIsNone(psk.as_row("VA7ZT", dict(SPOT, f="not a frequency")))
        self.assertIsNone(psk.as_row("VA7ZT", dict(SPOT, f=0)))

    def test_no_time_at_all_is_dropped(self):
        """`at` is in the primary key, so a zero would collapse every
        spot for a pair onto one row."""
        bad = dict(SPOT)
        bad.pop("t_tx")
        bad.pop("t")
        self.assertIsNone(psk.as_row("VA7ZT", bad))

    def test_it_falls_back_to_the_received_time(self):
        bad = dict(SPOT)
        bad.pop("t_tx")
        self.assertEqual(psk.as_row("VA7ZT", bad)["at"], 1791866549)

    def test_a_missing_signal_report_is_none_not_zero(self):
        """0 dB is a real report, and a strong one."""
        bad = dict(SPOT)
        bad.pop("rp")
        self.assertIsNone(psk.as_row("VA7ZT", bad)["snr"])


class TestTheFeedManager(unittest.TestCase):
    """One connection per target, buffered writes, and letting go."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="hamdash-psklive-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.store = pskstore.Store(self.home / "s.sqlite3")
        self.live = psk.Live(self.store)

    def test_spots_reach_the_store(self):
        self.live._spot("VA7ZT", SPOT)
        self.live.flush()
        rows = self.store.since("VA7ZT", 10 ** 9)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rx"], "BG2MGR")

    def test_they_are_buffered_rather_than_written_one_at_a_time(self):
        """A busy band delivers 56 spots in four seconds, measured; a
        transaction each is a write each."""
        self.live._buf.clear()
        self.live._flushed = time.time()            # not due yet
        self.live._spot("VA7ZT", SPOT)
        self.assertEqual(self.store.count("VA7ZT"), 0, "wrote immediately")
        self.live.flush()
        self.assertEqual(self.store.count("VA7ZT"), 1)

    def test_the_same_spot_twice_is_one_row(self):
        self.live._spot("VA7ZT", SPOT)
        self.live._spot("VA7ZT", SPOT)
        self.live.flush()
        self.assertEqual(self.store.count("VA7ZT"), 1)

    def test_a_spot_that_will_not_convert_is_dropped_quietly(self):
        self.live._spot("VA7ZT", {"nonsense": True})
        self.live.flush()
        self.assertEqual(self.store.count("VA7ZT"), 0)

    def test_an_unwatchable_target_starts_no_connection(self):
        """Empty is not a subscription to the whole world."""
        self.assertIsNone(self.live.ensure("", as_grid=False))

    def test_asking_twice_is_one_feed(self):
        a = self.live.ensure("VA7SLL", as_grid=False)
        self.addCleanup(a.stop)
        b = self.live.ensure("VA7SLL", as_grid=False)
        self.assertIs(a, b, "opened a second connection to the same broker")

    def test_a_grid_and_a_callsign_subscribe_differently(self):
        g = self.live.ensure("CN89", as_grid=True)
        self.addCleanup(g.stop)
        self.assertIn("CN89", "".join(g.topics))
        self.assertEqual(len(g.topics), 1)
        c = self.live.ensure("VA7SLL", as_grid=False)
        self.addCleanup(c.stop)
        self.assertEqual(len(c.topics), 2, "a callsign is both directions")

    def test_status_for_something_never_asked_about(self):
        self.assertEqual(self.live.status("NOBODY")["status"], "idle")


class TestTheStore(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="hamdash-pskstore-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.st = pskstore.Store(self.home / "s.sqlite3")
        self.now = int(time.time())

    def a_row(self, **over):
        row = {"tx": "JA1AA", "rx": "VE7BB", "tx_grid": "PM95",
               "rx_grid": "CN89", "hz": 14074000, "mode": "FT8", "snr": -9,
               "at": self.now - 60, "is_tx": False, "is_rx": True,
               "tx_dxcc": "Japan", "rx_dxcc": "Canada",
               "tx_region": "", "rx_region": ""}
        row.update(over)
        return row

    def test_a_report_round_trips(self):
        self.st.add("CN89", [self.a_row()])
        got = self.st.since("CN89", 3600)[0]
        self.assertEqual(got["tx"], "JA1AA")
        self.assertEqual(got["band"], "20m")
        self.assertEqual(got["snr"], -9)
        self.assertTrue(got["is_rx"])
        self.assertFalse(got["is_tx"])

    def test_the_same_report_twice_is_one_row(self):
        self.st.add("CN89", [self.a_row()])
        self.assertEqual(self.st.add("CN89", [self.a_row()]), 0)
        self.assertEqual(self.st.count("CN89"), 1)

    def test_two_targets_do_not_mix(self):
        self.st.add("CN89", [self.a_row()])
        self.st.add("PM95", [self.a_row(tx="W1AW")])
        self.assertEqual(self.st.count("CN89"), 1)
        self.assertEqual([r["tx"] for r in self.st.since("PM95", 3600)],
                         ["W1AW"])

    def test_band_is_derived_not_stored(self):
        """A stored copy is only a chance to disagree with the frequency."""
        self.st.add("CN89", [self.a_row(hz=7074000)])
        self.assertEqual(self.st.since("CN89", 3600)[0]["band"], "40m")

    def test_pruning_drops_what_their_api_could_not_replace(self):
        self.st.add("CN89", [self.a_row(),
                             self.a_row(tx="OLD", at=self.now - 90000)])
        self.assertEqual(self.st.prune(), 1)
        self.assertEqual([r["tx"] for r in self.st.since("CN89", 86400 * 2)],
                         ["JA1AA"])

    def test_the_window_is_a_window(self):
        self.st.add("CN89", [self.a_row(tx="RECENT", at=self.now - 60),
                             self.a_row(tx="EARLIER", at=self.now - 7200)])
        self.assertEqual([r["tx"] for r in self.st.since("CN89", 900)],
                         ["RECENT"])

    def test_newest_first(self):
        self.st.add("CN89", [self.a_row(tx="OLDER", at=self.now - 600),
                             self.a_row(tx="NEWER", at=self.now - 60)])
        self.assertEqual([r["tx"] for r in self.st.since("CN89", 3600)],
                         ["NEWER", "OLDER"])

    def test_oldest_says_how_far_back_it_reaches(self):
        self.st.add("CN89", [self.a_row(at=self.now - 3600)])
        self.assertAlmostEqual(self.st.oldest("CN89"), self.now - 3600, delta=2)

    def test_the_marker_survives_and_is_per_target(self):
        self.st.mark("CN89", last_seq="123", at=1.0)
        self.st.mark("PM95", last_seq="456", at=2.0)
        fresh = pskstore.Store(self.home / "s.sqlite3")
        self.assertEqual(fresh.state("CN89")["last_seq"], "123")
        self.assertEqual(fresh.state("PM95")["last_seq"], "456")

    def test_marking_one_field_leaves_the_others(self):
        self.st.mark("CN89", last_seq="123", at=1.0)
        self.st.mark("CN89", at=2.0)
        self.assertEqual(self.st.state("CN89")["last_seq"], "123")

    def test_an_unknown_target_is_empty_not_an_error(self):
        self.assertEqual(self.st.state("NOWHERE"), {})
        self.assertEqual(self.st.since("NOWHERE", 3600), [])


class TestGridOrCallsign(unittest.TestCase):
    """Getting this wrong asks the wrong question, so it is strict: anything
    not clearly a locator is treated as a callsign."""

    def test_grids(self):
        for s in ("CN89", "cn89", "FN42hn", "JJ00aa"):
            with self.subTest(s=s):
                self.assertTrue(psk._looks_like_grid(s))

    def test_callsigns(self):
        for s in ("VA7SLL", "W1AW", "JO7KMB", "VE7", "K1A", "VA7SLL/P", ""):
            with self.subTest(s=s):
                self.assertFalse(psk._looks_like_grid(s))


class TestBands(unittest.TestCase):
    def test_the_ones_these_reports_actually_use(self):
        for mhz, band in [(14.074, "20m"), (7.074, "40m"), (18.1, "17m"),
                          (10.136, "30m"), (28.074, "10m"), (50.313, "6m"),
                          (0.4746, "630m"), (144.174, "2m")]:
            with self.subTest(mhz=mhz):
                self.assertEqual(pskstore.band_for(mhz), band)

    def test_something_off_band_is_blank_rather_than_wrong(self):
        self.assertEqual(pskstore.band_for(15.0), "")


if __name__ == "__main__":
    unittest.main()
