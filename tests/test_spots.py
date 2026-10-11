"""Spots from four services, and the units they disagree about.

Every record below is a real one, copied from the live service, because the
thing that goes wrong here goes wrong quietly: a frequency read in the wrong
unit does not raise, it puts a 7 MHz spot on 7 kHz. That shows up as a row
with no band against it, or - worse - on a real band a thousand times away
from where the station actually is, which is a wrong answer that looks
exactly like a right one.

Nothing here touches the network.

Run with:  python -m unittest discover -s tests
"""
import datetime as dt
import importlib.util
import sys
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "plugins" / "spots"
sys.path.insert(0, str(HERE))

import sources                                          # noqa: E402


def _load(name, path):
    """Load a card's entry under a name of its own.

    Every card's entry is called `card.py`, so `import card` in two test
    files gives the second one whichever card the first imported - and the
    failure is an AttributeError about a constant that is plainly there.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


spots_card = _load("spots_card", HERE / "card.py")

# --- real records, as each service actually sends them ---------------------

POTA_ROW = {
    "spotId": 58482622, "activator": "KD7QOW", "frequency": "28051.0",
    "mode": "CW", "reference": "US-4529", "parkName": None,
    "spotTime": "2026-10-10T23:20:15", "spotter": "KG6NFJ-#",
    "comments": "RBN 6 dB 22 WPM via KG6NFJ-#", "source": "RBN",
    "invalid": None, "name": "Gifford Pinchot National Forest",
    "locationDesc": "US-WA", "grid4": "CN95", "grid6": "CN95cu",
    "latitude": 45.8674, "longitude": -121.826, "count": 11, "expire": 137,
}

SOTA_ROW = {
    "id": 408054, "timeStamp": "2026-10-10T23:38:02.560161Z",
    "activatorCallsign": "7M4QZE/7", "activatorName": "Tak",
    "callsign": "RBNHOLE", "comments": "[RBNHole] at JA1CCA 24 WPM 18 dB SNR",
    "frequency": 7.004, "mode": "CW", "summitCode": "JA/MG-001",
    "summitName": "Zaouzan (Byoubudake)", "AltM": 1825, "points": 10,
    "latitude": 38.0957, "longitude": 140.476,
}

DX_ROW = {
    "info": "PAQP BER", "dx_country": "United States", "de_latitude": 39.95,
    "dx_latitude": 39.95, "dx_longitude": 76.85, "de_call": "AA3B",
    "frequency": 7029.4, "time": "2026-10-10T23:47:26", "dx_call": "K3CT",
    "de_longitude": 76.85, "id": 68511830,
}

CONTEST_ROW = {
    "id": "e4f214ee", "name": "Makrothen RTTY Contest", "type": "Contest",
    "start_at": "2026-10-10T00:00:00+00:00",
    "end_at": "2026-10-11T15:59:00+00:00", "recurrence": "annual",
    "bands": ["HF"], "modes": ["RTTY"], "website": "",
}


def at(text):
    return dt.datetime.fromisoformat(text).timestamp()


class TestTheUnits(unittest.TestCase):
    """Three sources, three units. This is the whole reason the module
    exists separately from the card."""

    def test_pota_sends_kilohertz_as_a_string(self):
        got = sources.pota([POTA_ROW])[0]
        self.assertEqual(got["hz"], 28_051_000)
        self.assertEqual(got["band"], "10m")

    def test_sota_sends_megahertz_as_a_float(self):
        got = sources.sota([SOTA_ROW])[0]
        self.assertEqual(got["hz"], 7_004_000)
        self.assertEqual(got["band"], "40m")

    def test_the_cluster_sends_kilohertz_as_a_float(self):
        got = sources.dx([DX_ROW])[0]
        self.assertEqual(got["hz"], 7_029_400)
        self.assertEqual(got["band"], "40m")

    def test_every_one_of_them_lands_on_a_band(self):
        """The symptom of getting a unit wrong, stated as a test: a real
        spot always has a band, so one without a band is a bug here."""
        rows = (sources.pota([POTA_ROW]) + sources.sota([SOTA_ROW])
                + sources.dx([DX_ROW]))
        for r in rows:
            with self.subTest(kind=r["kind"]):
                self.assertTrue(r["band"], f"{r['call']} at {r['hz']} Hz")

    def test_a_missing_or_junk_frequency_is_zero_not_a_crash(self):
        for bad in (None, "", "n/a", {}):
            with self.subTest(value=bad):
                row = dict(POTA_ROW, frequency=bad)
                self.assertEqual(sources.pota([row])[0]["hz"], 0)


class TestTheClocks(unittest.TestCase):
    def test_a_naive_timestamp_is_read_as_utc(self):
        """POTA and the cluster send a naive string that is UTC. Read as
        local time it puts every spot hours into the future, and the list
        sorts newest-last."""
        self.assertEqual(sources.pota([POTA_ROW])[0]["at"],
                         at("2026-10-10T23:20:15+00:00"))

    def test_a_z_suffix_and_microseconds_are_understood(self):
        self.assertAlmostEqual(sources.sota([SOTA_ROW])[0]["at"],
                               at("2026-10-10T23:38:02.560161+00:00"),
                               places=3)

    def test_the_two_agree_about_the_same_instant(self):
        """The one that matters: three sources on one list, sorted by time.
        If they disagree about what 23:40Z means the list is shuffled."""
        pota = sources.pota([dict(POTA_ROW, spotTime="2026-10-10T23:40:00")])
        sota = sources.sota([dict(SOTA_ROW,
                                  timeStamp="2026-10-10T23:40:00.000000Z")])
        self.assertEqual(pota[0]["at"], sota[0]["at"])

    def test_an_unparseable_time_is_zero_rather_than_now(self):
        """Zero sorts to the bottom; "now" would pin a broken record to the
        top of the list for ever."""
        self.assertEqual(sources.pota([dict(POTA_ROW, spotTime="soon")])[0]["at"], 0)


class TestWhatIsKept(unittest.TestCase):
    def test_a_park_keeps_its_reference_and_a_link(self):
        got = sources.pota([POTA_ROW])[0]
        self.assertEqual(got["ref"], "US-4529")
        self.assertEqual(got["place"], "Gifford Pinchot National Forest")
        self.assertEqual(got["where"], "US-WA")
        self.assertIn("US-4529", got["url"])

    def test_a_summit_keeps_its_reference_and_association(self):
        got = sources.sota([SOTA_ROW])[0]
        self.assertEqual(got["ref"], "JA/MG-001")
        self.assertEqual(got["where"], "JA")
        self.assertEqual(got["call"], "7M4QZE/7")

    def test_a_cluster_spot_has_no_mode_and_none_is_invented(self):
        """The network has no mode field. Inventing one from the comment
        would be filtered on as though it were known."""
        got = sources.dx([DX_ROW])[0]
        self.assertEqual(got["mode"], "")
        self.assertEqual(got["comment"], "PAQP BER")
        self.assertEqual(got["spotter"], "AA3B")
        self.assertEqual(got["call"], "K3CT")

    def test_pota_invalid_spots_are_dropped(self):
        self.assertEqual(sources.pota([dict(POTA_ROW, invalid=True)]), [])

    def test_the_sota_deprecation_notice_is_not_a_spot(self):
        """Their old endpoint answers with one row carrying the word
        DEPRECATED in every field. Drawn, it is a station called None on no
        band, which reads as the card being broken."""
        notice = {"id": None, "activatorCallsign": "DEPRECATED",
                  "summitCode": "DEPRECATED", "frequency": "", "mode": "",
                  "timeStamp": "2026-10-10T23:47:59",
                  "comments": "This API endpoint is deprecated."}
        self.assertEqual(sources.sota([notice]), [])
        self.assertEqual(sources.sota([dict(SOTA_ROW,
                                            activatorCallsign=None)]), [])

    def test_every_row_has_the_same_shape(self):
        """One list on screen, so one record shape - otherwise the page
        sorts and filters three different things."""
        rows = (sources.pota([POTA_ROW]) + sources.sota([SOTA_ROW])
                + sources.dx([DX_ROW]))
        keys = {frozenset(r) for r in rows}
        self.assertEqual(len(keys), 1, "sources disagree about the fields")

    def test_a_callsign_is_upper_case_whatever_arrived(self):
        got = sources.pota([dict(POTA_ROW, activator=" kd7qow ")])[0]
        self.assertEqual(got["call"], "KD7QOW")


class TestContests(unittest.TestCase):
    def setUp(self):
        self.rows = sources.contests({"activities": [CONTEST_ROW]})

    def test_it_reads_the_bulk_export(self):
        self.assertEqual(len(self.rows), 1)
        c = self.rows[0]
        self.assertEqual(c["name"], "Makrothen RTTY Contest")
        self.assertEqual(c["modes"], ["RTTY"])
        self.assertEqual(c["start"], at("2026-10-10T00:00:00+00:00"))

    def test_a_bare_list_works_too(self):
        self.assertEqual(len(sources.contests([CONTEST_ROW])), 1)

    def test_running_now_is_answered_separately(self):
        """"Is there a contest on" is why the band is full. A contest that
        started yesterday has already scrolled off a list sorted by start
        time, which is exactly when it matters most."""
        during = at("2026-10-10T18:00:00+00:00")
        got = sources.live(self.rows, during)
        self.assertEqual([c["name"] for c in got["on"]],
                         ["Makrothen RTTY Contest"])
        self.assertEqual(got["soon"], [])

    def test_before_it_starts_it_is_upcoming(self):
        got = sources.live(self.rows, at("2026-10-09T12:00:00+00:00"))
        self.assertEqual(got["on"], [])
        self.assertEqual(len(got["soon"]), 1)

    def test_after_it_ends_it_is_neither(self):
        got = sources.live(self.rows, at("2026-10-12T00:00:00+00:00"))
        self.assertEqual(got["on"], [])
        self.assertEqual(got["soon"], [])

    def test_a_contest_with_no_start_is_dropped(self):
        self.assertEqual(sources.contests([dict(CONTEST_ROW, start_at="")]), [])

    def test_one_without_an_end_still_shows_while_it_starts(self):
        rows = sources.contests([dict(CONTEST_ROW, end_at=None)])
        self.assertEqual(rows[0]["end"], rows[0]["start"])


class TestBeingAGoodCitizen(unittest.TestCase):
    """Every one of these is somebody's hobby server."""

    def test_the_user_agent_says_who_we_are_and_where_to_complain(self):
        self.assertIn("hamdash", sources.UA)
        self.assertIn("github.com", sources.UA)

    def test_the_card_polls_no_faster_than_the_services_own_pages(self):
        self.assertGreaterEqual(spots_card.FLOOR, 60.0)
        # Contests are a year's calendar and move about as often.
        self.assertGreaterEqual(spots_card.CONTEST_FLOOR, 3600.0)

    def test_refresh_cannot_buy_its_way_past_the_floor(self):
        feed = self._a_feed(fetched_ago=1.0)
        feed.get(force=True)
        self.assertEqual(feed.calls, [], "a forced refresh jumped the floor")

    def test_refresh_does_work_once_the_floor_has_passed(self):
        """It is not a no-op button: it means "do not wait for the poll"."""
        feed = self._a_feed(fetched_ago=spots_card.FLOOR + 1)
        feed.get(force=True)
        self.assertEqual(len(feed.calls), 1)

    def test_a_source_that_is_down_keeps_serving_what_it_had(self):
        """An outage is a note beside the rows, not an empty card."""
        feed = self._a_feed(fetched_ago=9999)
        feed.fetch = lambda: (_ for _ in ()).throw(RuntimeError("502"))
        feed.get()
        self.assertEqual(feed.rows, ["kept"])
        self.assertIn("502", feed.error)
        self.assertGreaterEqual(feed.wait, spots_card.FLOOR)
        self.assertTrue(feed.status()["ok"], "stale beats empty")

    def test_a_failure_waits_its_turn(self):
        """Without this the page's own poll retries every few seconds into
        whatever just failed."""
        feed = self._a_feed(fetched_ago=9999)
        feed.fetch = lambda: (_ for _ in ()).throw(RuntimeError("nope"))
        feed.get()
        first = feed.wait
        self.assertGreater(first, spots_card.FLOOR, "it did not back off")
        # Not due again until the back-off has passed, not merely the floor.
        feed.at = time.time() - (spots_card.FLOOR + 1)
        feed.get()
        self.assertEqual(feed.wait, first, "it retried inside its own back-off")
        feed.at = time.time() - (first + 1)
        feed.get()
        self.assertGreater(feed.wait, first, "the back-off did not grow")

    def _a_feed(self, fetched_ago: float):
        feed = spots_card.Feed.__new__(spots_card.Feed)
        feed.name, feed.floor = "demo", spots_card.FLOOR
        feed.wait, feed.error = spots_card.FLOOR, ""
        feed.rows, feed.at = ["kept"], time.time() - fetched_ago
        feed._lock = threading.Lock()
        feed.calls = []
        feed.fetch = lambda: feed.calls.append(1) or ["fresh"]
        feed._save = lambda: None
        return feed


if __name__ == "__main__":
    unittest.main()


class TestWhereInTheWorld(unittest.TestCase):
    """A flag at the start of the Where column, worked out from the
    callsign.

    From the callsign and never from whatever the source calls the country:
    POTA, SOTA and the cluster spell them differently and the cluster sends
    none at all. The same rule the log already follows.
    """

    def test_a_callsign_becomes_a_flag(self):
        from hamdash.services import dxcc
        for call, iso in (("W1AW", "us"), ("VA7SLL", "ca"), ("G0ABC", "gb"),
                          ("JA1XYZ", "jp"), ("VK2DEF", "au")):
            with self.subTest(call=call):
                self.assertEqual(dxcc.iso_for(call), iso)

    def test_a_portable_callsign_still_finds_its_country(self):
        """Spots are full of them - /P, /M, and a prefix in front."""
        from hamdash.services import dxcc
        self.assertEqual(dxcc.iso_for("W1AW/P"), "us")
        self.assertEqual(dxcc.iso_for("VE3/W1AW"), "ca")

    def test_the_prefixes_a_live_feed_actually_sends(self):
        """Measured against 90 callsigns taken from this session's own
        fixtures and the prefixes heard daily. Six had no flag, and they
        were real: Japan is allocated 7J-7N and 8J-8N and the table had
        three of the ten, so an FT-890 watching JA spots saw blanks.
        CS3B is an NCDXF beacon - it is on the band every three minutes."""
        from hamdash.services import dxcc
        for call, iso in (("7M4QZE/7", "jp"), ("7J1AAA", "jp"),
                          ("7N2XYZ", "jp"), ("8N1ABC", "jp"),
                          ("CS3B", "pt")):
            with self.subTest(call=call):
                self.assertEqual(dxcc.iso_for(call), iso)

    def test_a_longer_prefix_wins(self):
        """CS3 is Madeira and CS is Portugal; the table is matched longest
        first or a Madeira beacon flies the wrong flag."""
        from hamdash.services import dxcc
        self.assertEqual(dxcc.for_call("CS3B")["country"], "Madeira")
        self.assertEqual(dxcc.for_call("CS7ABC")["country"], "Portugal")

    def test_an_unknown_prefix_asks_for_no_flag(self):
        """`HD.flag` draws nothing for an empty code, which is what should
        happen rather than a broken image."""
        from hamdash.services import dxcc
        self.assertEqual(dxcc.iso_for("1ZZZ9"), "")
        self.assertEqual(dxcc.iso_for(""), "")

    def test_every_source_gives_a_callsign_a_flag_can_come_from(self):
        """The column is only useful if the rows actually carry a call."""
        from hamdash.services import dxcc
        for name, fn, fixture in (("pota", sources.pota, POTA_ROW),
                                  ("sota", sources.sota, SOTA_ROW),
                                  ("dx", sources.dx, DX_ROW)):
            with self.subTest(source=name):
                for row in fn([fixture]):
                    self.assertTrue(row["call"], f"{name} row with no call")
                    # These three are real rows off the live services, so
                    # the prefix table should know all of them.
                    self.assertTrue(dxcc.iso_for(row["call"]),
                                    f"{name}: no flag for {row['call']!r}")
