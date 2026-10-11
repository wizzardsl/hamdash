"""Nothing else will take hamdash's transmission off the air.

A card that keys the rig and then crashes, a tile closed mid transmission, a
browser that dies - the radio goes on transmitting into an antenna with
nobody watching. It is the one failure in this program with consequences
outside the computer, so the watchdog runs on the host's polling thread,
which is still there when whatever keyed the rig is not.

The clock is driven by hand here rather than by sleeping: a test that waits
six seconds to find out is a test nobody runs.

Run with:  python -m unittest discover -s tests
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hamdash.config import Config
from hamdash.services import rigs


class Recording(rigs.MockBackend):
    """A mock rig that writes down every key and unkey."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = []
        self.fail_unkey = False

    def set_ptt(self, on: bool) -> None:
        self.calls.append(bool(on))
        if not on and self.fail_unkey:
            raise rigs.RigError("the radio did not answer")
        super().set_ptt(on)


def a_manager(ptt_timeout=120):
    home = Path(tempfile.mkdtemp(prefix="hamdash-ptt-"))
    (home / "config.json").write_text(json.dumps({
        "radio": {"ptt_timeout": ptt_timeout}}), encoding="utf-8")
    (home / "layout.json").write_text("[]", encoding="utf-8")
    cfg = Config(home)
    rigs.ensure_rigs(cfg)
    man = rigs.RigManager(cfg)
    man._backend = Recording()
    man._error = ""
    return man, home


class TestTheWatchdog(unittest.TestCase):
    def setUp(self):
        self.man, home = a_manager()
        self.addCleanup(shutil.rmtree, home, True)
        self.addCleanup(self.man.stop)
        self.rig = self.man._backend
        self.now = 1000.0
        patch = mock.patch.object(rigs.time, "time", lambda: self.now)
        patch.start()
        self.addCleanup(patch.stop)

    def tick(self, seconds):
        """Move the clock on and let the watchdog look."""
        self.now += seconds
        return self.man._watch_ptt(self.man.state())

    def test_a_held_transmission_is_left_alone(self):
        """While the card keeps saying it wants it, the rig stays keyed."""
        self.man.set_ptt(True)
        for _ in range(20):                     # 20 seconds of keep-alives
            self.now += 1.0
            self.man.set_ptt(True)              # the ping
            self.man._watch_ptt(self.man.state())
        self.assertEqual(self.rig.calls, [True], "it was re-keyed or unkeyed")
        self.assertTrue(self.man.state()["ptt"])

    def test_a_ping_does_not_touch_the_radio(self):
        """The keep-alive is a refresh, not a second key-down."""
        self.man.set_ptt(True)
        self.man.set_ptt(True)
        self.man.set_ptt(True)
        self.assertEqual(self.rig.calls, [True])

    def test_when_the_pings_stop_the_rig_is_unkeyed(self):
        """The card crashed, the tile was closed, the browser died."""
        self.man.set_ptt(True)
        self.tick(rigs.PTT_HOLD + 1)
        self.assertEqual(self.rig.calls, [True, False])
        self.assertFalse(self.man.state()["ptt"])

    def test_it_says_why(self):
        self.man.set_ptt(True)
        st = self.tick(rigs.PTT_HOLD + 1)
        self.assertIn("holding", st["ptt_dropped"])
        self.assertFalse(st["ptt"])

    def test_a_transmission_that_runs_too_long_is_ended(self):
        """The pings keep coming but nobody is there: a board left open on
        a keyed rig."""
        self.man.set_ptt(True)
        for _ in range(200):                    # a ping a second, patiently
            self.now += 1.0
            self.man.set_ptt(True)
            st = self.man._watch_ptt(self.man.state())
            if not st.get("ptt"):
                break
        self.assertIn(False, self.rig.calls, "it transmitted for ever")
        self.assertIn("limit", st["ptt_dropped"])
        self.assertLessEqual(self.now - 1000.0, 125.0, "it ran over the limit")

    def test_a_ceiling_of_zero_means_no_ceiling(self):
        """But the keep-alive still applies - that is the one that catches
        a card which has stopped running."""
        man, home = a_manager(ptt_timeout=0)
        self.addCleanup(shutil.rmtree, home, True)
        self.addCleanup(man.stop)
        man._backend, man._error = Recording(), ""
        with mock.patch.object(rigs.time, "time", lambda: self.now):
            man.set_ptt(True)
            for _ in range(600):
                self.now += 1.0
                man.set_ptt(True)
                man._watch_ptt(man.state())
            self.assertEqual(man._backend.calls, [True], "a ceiling applied")
            self.now += rigs.PTT_HOLD + 1
            man._watch_ptt(man.state())
            self.assertEqual(man._backend.calls, [True, False])

    def test_a_transmission_nobody_here_started_is_not_touched(self):
        """The operator on the mic, a footswitch, another program. Cutting
        somebody off mid-over because a dashboard thought it knew better is
        worse than the thing being guarded against."""
        self.rig._f["ptt"] = True               # keyed from outside hamdash
        for _ in range(60):
            self.tick(5.0)
        self.assertEqual(self.rig.calls, [], "it unkeyed somebody else")
        self.assertTrue(self.man.state()["ptt"])

    def test_unkeying_by_hand_stops_the_watchdog_caring(self):
        self.man.set_ptt(True)
        self.man.set_ptt(False)
        self.assertEqual(self.rig.calls, [True, False])
        self.tick(rigs.PTT_HOLD + 1)
        self.assertEqual(self.rig.calls, [True, False], "it unkeyed twice")

    def test_a_rig_that_unkeys_itself_is_noticed(self):
        """Its own timeout, or the operator's hand. Holding a transmission
        that is not happening would send a pointless unkey later."""
        self.man.set_ptt(True)
        self.rig._f["ptt"] = False              # the rig dropped it
        self.tick(1.0)
        self.tick(rigs.PTT_HOLD + 1)
        self.assertEqual(self.rig.calls, [True], "it unkeyed an idle rig")

    def test_a_failed_unkey_is_shouted_about(self):
        """The radio may still be transmitting, which is the worst case and
        must not be swallowed."""
        self.man.set_ptt(True)
        self.rig.fail_unkey = True
        st = self.tick(rigs.PTT_HOLD + 1)
        self.assertIn("unkeying it failed", st["ptt_dropped"])

    def test_the_reason_stops_being_shown_eventually(self):
        """A message about something that happened ten minutes ago, still on
        screen, reads as a fault happening now."""
        self.man.set_ptt(True)
        self.tick(rigs.PTT_HOLD + 1)
        self.assertIn("ptt_dropped", self.man.state())
        self.now += rigs.PTT_DROP_NOTICE + 1
        self.assertNotIn("ptt_dropped", self.man.state())

    def test_keying_again_clears_the_last_reason(self):
        self.man.set_ptt(True)
        self.tick(rigs.PTT_HOLD + 1)
        self.man.set_ptt(True)
        self.assertNotIn("ptt_dropped", self.man.state())


    def test_shutting_down_takes_the_transmitter_off_the_air(self):
        """The watchdog is going away with everything else, so stop() is the
        last thing that can."""
        self.man.set_ptt(True)
        self.man.stop()
        self.assertEqual(self.rig.calls, [True, False])

    def test_shutting_down_while_receiving_does_not_touch_the_rig(self):
        self.man.stop()
        self.assertEqual(self.rig.calls, [])

    def test_a_failed_unkey_on_shutdown_does_not_stop_the_shutdown(self):
        self.man.set_ptt(True)
        self.rig.fail_unkey = True
        self.man.stop()                 # must not raise
        self.assertIsNone(self.man._backend)


class TestTheLimits(unittest.TestCase):
    def test_the_ceiling_comes_from_config(self):
        man, home = a_manager(ptt_timeout=45)
        self.addCleanup(shutil.rmtree, home, True)
        self.addCleanup(man.stop)
        self.assertEqual(man.ptt_limits(), (rigs.PTT_HOLD, 45.0))

    def test_junk_in_config_falls_back_rather_than_removing_the_ceiling(self):
        """A missing ceiling is a transmitter with no limit, so the
        forgiving reading is the dangerous one."""
        for junk in ("soon", None, {}):
            man, home = a_manager(ptt_timeout=junk)
            self.addCleanup(shutil.rmtree, home, True)
            self.addCleanup(man.stop)
            with self.subTest(junk=junk):
                self.assertEqual(man.ptt_limits()[1], rigs.PTT_TIMEOUT)

    def test_a_negative_ceiling_is_not_an_instant_cut_off(self):
        man, home = a_manager(ptt_timeout=-5)
        self.addCleanup(shutil.rmtree, home, True)
        self.addCleanup(man.stop)
        self.assertEqual(man.ptt_limits()[1], 0.0)

    def test_the_defaults_are_sane(self):
        self.assertLessEqual(rigs.PTT_HOLD, 10.0, "too long to notice a crash")
        self.assertGreaterEqual(rigs.PTT_HOLD, 3.0, "a slow poll would trip it")
        self.assertGreaterEqual(rigs.PTT_TIMEOUT, 60.0, "cuts off a real over")


if __name__ == "__main__":
    unittest.main()
