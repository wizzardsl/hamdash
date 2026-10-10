"""flrig, whose interface is not quite what you would guess.

Nothing here talks to flrig: the XML-RPC proxy is stood in for. What is
worth guarding is the shape of its answers, which was read off a running
flrig 2.0.12 driving an FT-890 rather than assumed - frequencies arrive as
strings, and modes are the radio's own names with the filter width welded
on.

Run with:  python -m unittest discover -s tests
"""
import unittest
from unittest import mock

from hamdash.services import rigs

# Exactly what the FT-890 offers through flrig.
FT890_MODES = ["LSB", "USB", "CW-W", "CW-N", "AM-W", "AM-N", "FM"]


class FakeFlrig:
    """Answers the handful of calls the backend makes, in flrig's types."""

    def __init__(self, freq="14199460", mode="USB", ptt=0,
                 modes=None, xcvr="FT-890"):
        self.values = {
            "rig.get_vfo": freq, "rig.get_mode": mode, "rig.get_ptt": ptt,
            "rig.get_modes": list(modes if modes is not None else FT890_MODES),
            "rig.get_xcvr": xcvr, "main.get_version": "2.0.12",
        }
        self.sent = []

    def __call__(self, name, *args):
        self.sent.append((name, args))
        if name in self.values:
            return self.values[name]
        return ""


def a_backend(fake, **kw):
    b = rigs.FlrigBackend(**kw)
    b._call = fake
    return b


class TestReadingIt(unittest.TestCase):
    def test_a_frequency_arrives_as_a_string(self):
        """`rig.get_vfo` answers '14199460', not an integer. Passing that
        through would put a string where every other backend puts a number,
        and band_for would never match."""
        st = a_backend(FakeFlrig(freq="14199460")).state()
        self.assertEqual(st["freq"], 14199460)
        self.assertIsInstance(st["freq"], int)
        self.assertEqual(st["band"], "20m")

    def test_ptt_is_an_int_and_comes_back_a_bool(self):
        self.assertFalse(a_backend(FakeFlrig(ptt=0)).state()["ptt"])
        self.assertTrue(a_backend(FakeFlrig(ptt=1)).state()["ptt"])

    def test_it_says_which_radio_flrig_is_driving(self):
        got = a_backend(FakeFlrig()).settle()
        self.assertEqual(got["rig_type"], "FT-890")

    def test_rubbish_is_reported_rather_than_raised(self):
        fake = FakeFlrig()
        fake.values["rig.get_vfo"] = "not a number"
        st = a_backend(fake).state()
        self.assertFalse(st["ok"])
        self.assertIn("not a number", st["error"])

    def test_flrig_being_absent_names_the_fix(self):
        def boom(name, *a):
            raise rigs.RigError("no flrig at 127.0.0.1:12345 (timed out)")
        st = a_backend(boom).state()
        self.assertFalse(st["ok"])
        self.assertIn("12345", st["error"])


class TestModeNames(unittest.TestCase):
    """An FT-890 has no mode called CW: it has CW-W and CW-N, which are one
    mode and two filters. The log wants CW."""

    def test_the_width_comes_off_for_the_log(self):
        for name, want in [("CW-W", "CW"), ("CW-N", "CW"), ("AM-N", "AM"),
                           ("USB", "USB"), ("LSB", "LSB"), ("FM", "FM")]:
            with self.subTest(mode=name):
                self.assertEqual(rigs._adif_mode(name), want)

    def test_a_suffix_that_is_not_a_width_is_left_alone(self):
        """PKT-U is not PKT with a filter on it."""
        self.assertEqual(rigs._adif_mode("PKT-U"), "PKT-U")
        self.assertEqual(rigs._adif_mode("DATA-U"), "DATA-U")

    def test_asking_for_cw_picks_a_filter_the_radio_has(self):
        self.assertEqual(rigs._rig_mode("CW", FT890_MODES), "CW-W")
        self.assertEqual(rigs._rig_mode("AM", FT890_MODES), "AM-W")

    def test_an_exact_name_wins_over_a_base_match(self):
        self.assertEqual(rigs._rig_mode("CW-N", FT890_MODES), "CW-N")

    def test_a_mode_the_radio_lacks_is_refused_not_sent(self):
        """`rig.set_mode` with a name the radio does not have returns success
        and changes nothing - verified against flrig 2.0.12 - so the operator
        would only find out by looking at the radio."""
        b = a_backend(FakeFlrig())
        with self.assertRaises(rigs.RigError) as got:
            b.set_mode("PKTUSB")
        self.assertIn("PKTUSB", str(got.exception))
        self.assertIn("CW-W", str(got.exception))       # what it does offer
        self.assertNotIn("rig.set_mode", [n for n, _ in b._call.sent])

    def test_a_mode_it_has_is_sent_in_the_radio_s_spelling(self):
        b = a_backend(FakeFlrig())
        b.set_mode("cw")
        self.assertIn(("rig.set_mode", ("CW-W",)), b._call.sent)


class TestWriting(unittest.TestCase):
    def test_the_frequency_goes_out_as_a_number(self):
        b = a_backend(FakeFlrig())
        b.set_freq(7030000)
        self.assertEqual(b._call.sent[-1], ("rig.set_frequency", (7030000.0,)))

    def test_ptt_is_one_or_zero(self):
        b = a_backend(FakeFlrig())
        b.set_ptt(True)
        b.set_ptt(False)
        self.assertEqual([a for n, a in b._call.sent if n == "rig.set_ptt"],
                         [(1,), (0,)])


class TestTheEntry(unittest.TestCase):
    def test_it_keeps_a_host_and_a_port(self):
        got = rigs.clean_rig({"backend": "flrig", "host": " 127.0.0.1 ",
                              "port": 12346, "model": 99, "slot": 2})
        self.assertEqual(got["host"], "127.0.0.1")
        self.assertEqual(got["port"], 12346)
        self.assertNotIn("model", got)
        self.assertNotIn("slot", got)

    def test_it_defaults_to_flrig_s_own_port(self):
        got = rigs.clean_rig({"backend": "flrig"})
        self.assertEqual(got["port"], rigs.FLRIG_PORT)
        self.assertEqual(got["host"], "127.0.0.1")

    def test_the_backend_is_offered(self):
        self.assertIn("flrig", rigs.BACKENDS)

    def test_the_manager_builds_one(self):
        made = rigs.make_backend({"backend": "flrig", "host": "127.0.0.1",
                                  "port": 12345})
        self.assertIsInstance(made, rigs.FlrigBackend)
        self.assertEqual(made.where(), "127.0.0.1:12345")

    def test_a_call_cannot_hang_a_server_thread(self):
        """flrig is local and answers in well under a millisecond, but the
        thread waiting on it is one of the server's (invariant 4)."""
        self.assertLessEqual(rigs.FLRIG_TIMEOUT, 5.0)
        self.assertEqual(rigs.FlrigBackend().timeout, rigs.FLRIG_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
