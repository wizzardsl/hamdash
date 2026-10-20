"""VFOs, split and the meters.

The one that matters most here is the power meter. A radio has a single
physical meter, so asking flrig for the transmit power while the radio is
receiving hands back the S-meter reading - 5 to 8 on a silent FT-890, which
drawn as watts is a transmitter that appears to be running when it is not.
That is a wrong answer that looks like a right one, which is the kind this
file exists to stop.

Run with:  python -m unittest discover -s tests
"""
import unittest
import unittest.mock

from hamdash.services import rigs
from tests.test_flrig import FakeFlrig, a_backend


class TestTheMeterFollowsThePtt(unittest.TestCase):
    def test_receiving_reports_a_signal_and_no_power(self):
        m = a_backend(FakeFlrig(ptt=0)).meters(tx=False)
        self.assertIn("s_units", m)
        self.assertNotIn("power_pct", m,
                         "power was reported for a receiving radio")
        self.assertNotIn("swr", m)

    def test_transmitting_reports_power_and_no_signal(self):
        m = a_backend(FakeFlrig(ptt=1)).meters(tx=True)
        self.assertIn("power_pct", m)
        self.assertNotIn("s_units", m,
                         "an S-meter was reported for a transmitting radio")

    def test_the_power_meter_is_never_asked_while_receiving(self):
        """Not just unreported - unasked. It is the same needle, so a
        reading taken here is the S-meter whatever it is labelled."""
        fake = FakeFlrig(ptt=0)
        a_backend(fake).meters(tx=False)
        asked = [name for name, _ in fake.sent]
        self.assertNotIn("rig.get_pwrmeter", asked)
        self.assertIn("rig.get_smeter", asked)

    def test_the_manager_asks_the_radio_which_way_round_it_is(self):
        """Not what hamdash last did - something else may be keying it."""
        class Rig(rigs.Backend):
            kind = "fake"
            features = frozenset({rigs.F_SMETER, rigs.F_POWER})

            def __init__(self, ptt):
                self.ptt, self.asked = ptt, []

            def state(self):
                return {"ok": True, "freq": 14_074_000, "ptt": self.ptt}

            def meters(self, tx):
                self.asked.append(tx)
                return {"power_pct": 80.0} if tx else {"s_pct": 20.0}

        for keyed in (False, True):
            with self.subTest(keyed=keyed):
                mgr = rigs.RigLink(None, None)
                mgr._error, mgr._backend = "", Rig(keyed)
                got = mgr.meters()
                self.assertEqual(mgr._backend.asked, [keyed])
                self.assertIs(got["tx"], keyed)


class TestTheSMeterScale(unittest.TestCase):
    """flrig's own help calls its meter "0...9...+60"."""

    def test_it_agrees_with_what_flrig_itself_reported(self):
        """A deflection of 8 on the FT-890 is flrig's own "S 1"."""
        self.assertEqual(rigs._s_units_for(8), "S 1")

    def test_flrigs_own_s_units_win_over_the_curve(self):
        """flrig has the radio's meter table; this file has a straight
        line. Where flrig answers, that answer is used."""
        m = a_backend(FakeFlrig(sunits="S 7")).meters(tx=False)
        self.assertEqual(m["s_units"], "S 7")

    def test_the_curve_is_the_fallback_not_the_first_choice(self):
        m = a_backend(FakeFlrig(smeter="50", sunits="")).meters(tx=False)
        self.assertEqual(m["s_units"], "S 9")

    def test_hamlib_strength_is_db_relative_to_s9(self):
        """A real scale, so it converts properly rather than being drawn
        as a bare number. S9 is 0 dB and every S-unit below it is 6."""
        self.assertEqual(rigs._s_units_for_db(0), "S 9")
        self.assertEqual(rigs._s_units_for_db(-6), "S 8")
        self.assertEqual(rigs._s_units_for_db(-54), "S 0")
        self.assertEqual(rigs._s_units_for_db(20), "S 9+20")

    def test_a_meter_that_did_not_answer_is_not_a_zero(self):
        """None is "it did not say". Zero is "no signal", and a card that
        cannot tell them apart draws a dead radio as a quiet band."""
        self.assertIsNone(rigs._as_float(""))
        self.assertIsNone(rigs._as_float(None))
        self.assertIsNone(rigs._as_float("n/a"))
        self.assertEqual(rigs._as_float("0"), 0.0)


class TestWattsAreNotInvented(unittest.TestCase):
    def test_no_full_scale_means_no_watts(self):
        """The FT-890 answers 0 to get_maxpwr. A bar with no number beats
        a number that is a percentage wearing a W."""
        m = a_backend(FakeFlrig(ptt=1, pwr="80", maxpwr=0)).meters(tx=True)
        self.assertEqual(m["power_pct"], 80.0)
        self.assertNotIn("power_w", m)

    def test_a_known_full_scale_gives_watts(self):
        m = a_backend(FakeFlrig(ptt=1, pwr="50", maxpwr=100)).meters(tx=True)
        self.assertEqual(m["power_w"], 50.0)


class TestSwrIsNotAlwaysMeasured(unittest.TestCase):
    """Measured on an FT-890 keyed into a dummy load: the power meter read
    78% and the SWR meter read 0 throughout. 0 is not an SWR - 1.0 is the
    floor of the scale - so the radio is saying it does not measure it."""

    def test_an_impossible_swr_is_not_reported(self):
        m = a_backend(FakeFlrig(ptt=1, pwr="78", swr="0")).meters(tx=True)
        self.assertEqual(m["power_pct"], 78.0)
        self.assertNotIn("swr", m, "0 was drawn as a perfect match")

    def test_a_real_swr_is(self):
        m = a_backend(FakeFlrig(ptt=1, swr="1.4")).meters(tx=True)
        self.assertEqual(m["swr"], 1.4)

    def test_a_radio_that_says_nothing_says_nothing(self):
        m = a_backend(FakeFlrig(ptt=1, swr="")).meters(tx=True)
        self.assertNotIn("swr", m)


class TestTwoVfos(unittest.TestCase):
    def test_the_dial_is_whichever_vfo_is_in_use(self):
        st = a_backend(FakeFlrig(freq="7030000", vfo_b="14200000",
                                 ab="B")).state()
        self.assertEqual(st["vfo"], "B")
        self.assertEqual(st["freq"], 14_200_000, "the dial followed A")
        self.assertEqual(st["freq_a"], 7_030_000)
        self.assertEqual(st["band"], "20m")

    def test_split_comes_back_as_a_bool(self):
        self.assertIs(a_backend(FakeFlrig(split=1)).state()["split"], True)
        self.assertIs(a_backend(FakeFlrig(split=0)).state()["split"], False)

    def test_a_vfo_that_does_not_exist_is_refused_by_name(self):
        for bad in ("C", "", "AB", None):
            with self.subTest(bad=bad):
                with self.assertRaises(rigs.RigError) as caught:
                    rigs._vfo_name(bad)
                self.assertIn("A or B", str(caught.exception))

    def test_it_takes_the_names_hamlib_uses_too(self):
        self.assertEqual(rigs._vfo_name("VFOB"), "B")
        self.assertEqual(rigs._vfo_name("a"), "A")

    def test_selecting_a_vfo_and_swapping_them_are_different_things(self):
        """Selecting changes which VFO is live and leaves both alone;
        swapping exchanges what they hold and leaves the live one alone.
        Writing one as the other makes the pair of buttons do one thing."""
        m = rigs.MockBackend()
        a, b = m.state()["freq_a"], m.state()["freq_b"]

        m.set_vfo("B")
        self.assertEqual(m.state()["vfo"], "B")
        self.assertEqual(m.state()["freq"], b, "the dial did not follow")
        self.assertEqual((m.state()["freq_a"], m.state()["freq_b"]), (a, b),
                         "selecting a VFO moved what they hold")

        m.set_vfo("A")
        m.swap_vfo()
        self.assertEqual((m.state()["freq_a"], m.state()["freq_b"]), (b, a))
        self.assertEqual(m.state()["vfo"], "A", "swapping changed the live VFO")
        self.assertEqual(m.state()["freq"], b)

    def test_tuning_writes_to_the_vfo_in_use(self):
        m = rigs.MockBackend()
        m.set_vfo("B")
        m.set_freq(7_030_000)
        self.assertEqual(m.state()["freq_b"], 7_030_000)
        self.assertEqual(m.state()["freq_a"], 14_074_000, "it wrote to A")

    def test_the_mock_can_do_all_of_it(self):
        """A card that needs hardware to show anything cannot be built."""
        m = rigs.MockBackend()
        self.assertIn(rigs.F_VFO, m.features)
        m.set_split(True)
        self.assertTrue(m.state()["split"])


class TestOmniRigSaysWhatItKnows(unittest.TestCase):
    """Read off an FT-900 through OmniRig: a radio can be *told* which VFO
    to use and be unable to say which it is on, and `Vfo` still answers -
    with the last thing it was told."""

    def state_from(self, **got):
        base = {"online": True, "status": "On-line", "rig_type": "FT-900",
                "freq": 7_200_340, "mode": rigs.MODE_TO_OMNI["USB"],
                "tx": rigs.PM_RX, "vfo": rigs.PM_UNKNOWN, "vfo_known": False,
                "split": rigs.PM_SPLITOFF, "can": [rigs.F_VFO, rigs.F_SPLIT],
                "freq_a": 7_200_340, "freq_b": 14_300_000}
        b = rigs.OmniRigBackend.__new__(rigs.OmniRigBackend)
        b.slot = 1
        with unittest.mock.patch.object(rigs, "com_call",
                                        lambda fn, *a, **k: {**base, **got}):
            return b.state()

    def test_a_vfo_it_cannot_read_is_not_reported(self):
        """`Vfo` came back PM_VFOBA - the echo of a set_vfo('B') - while the
        dial was plainly on A. Reported as a reading, that is a card lighting
        the wrong VFO and an operator believing it."""
        st = self.state_from(vfo=rigs.PM_VFOBA, vfo_known=False)
        self.assertNotIn("vfo", st)

    def test_a_vfo_it_can_read_is(self):
        st = self.state_from(vfo=rigs.PM_VFOB, vfo_known=True)
        self.assertEqual(st["vfo"], "B")

    def test_the_split_flag_beats_the_cross_vfo_forms(self):
        """The radio said PM_SPLITOFF and `Vfo` said PM_VFOBA. Inferring
        split from the cross form reported a radio that was not split as
        split - over the top of the radio's own explicit answer."""
        st = self.state_from(split=rigs.PM_SPLITOFF, vfo=rigs.PM_VFOBA)
        self.assertIs(st["split"], False)

    def test_a_cross_form_is_still_split_when_there_is_no_flag(self):
        """A radio whose CAT has no split flag says it this way, and that is
        the whole reason the fallback exists."""
        st = self.state_from(split=rigs.PM_UNKNOWN, vfo=rigs.PM_VFOAB)
        self.assertIs(st["split"], True)

    def test_an_unknown_split_with_no_cross_form_says_nothing(self):
        st = self.state_from(split=rigs.PM_UNKNOWN, vfo=rigs.PM_UNKNOWN)
        self.assertNotIn("split", st)

    def test_what_the_rig_file_cannot_do_is_not_offered(self):
        """The FT-900 has no VFO swap: PM_VFOSWAP is neither readable nor
        writeable, so the card must not draw the button."""
        st = self.state_from(can=[rigs.F_VFO, rigs.F_SPLIT])
        self.assertEqual(st["features"], [rigs.F_VFO, rigs.F_SPLIT])
        self.assertNotIn(rigs.F_SWAP, st["features"])


class TestWhatACardMayOffer(unittest.TestCase):
    def test_omnirig_offers_no_meters_at_all(self):
        """Its interface has no such property - it is CAT control, not a
        receiver. A card must not draw an empty S-meter for it."""
        self.assertNotIn(rigs.F_SMETER, rigs.OmniRigBackend.features)
        self.assertNotIn(rigs.F_POWER, rigs.OmniRigBackend.features)
        self.assertIn(rigs.F_VFO, rigs.OmniRigBackend.features)

    def test_a_backend_offers_nothing_it_has_not_claimed(self):
        self.assertEqual(rigs.Backend.features, frozenset())
        for name in ("set_vfo", "set_split", "swap_vfo"):
            with self.subTest(name=name):
                with self.assertRaises(rigs.RigError):
                    getattr(rigs.Backend(), name)(*(
                        ("A",) if name == "set_vfo" else
                        (True,) if name == "set_split" else ()))


if __name__ == "__main__":
    unittest.main()
