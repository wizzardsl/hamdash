"""Several radios, and the one that is live.

Nothing here touches a serial port or OmniRig: the backends are stood in for.
What is worth guarding is the shape - that an upgrade keeps the radio it had,
that a rig entry carries only the fields its backend uses, and that the
constants read off OmniRig's type library are the ones in the code.

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


def a_config(radio=None):
    home = Path(tempfile.mkdtemp(prefix="hamdash-rigs-"))
    (home / "config.json").write_text(json.dumps({
        "station": {"callsign": "VA7SLL"},
        "radio": radio if radio is not None else {},
    }), encoding="utf-8")
    (home / "layout.json").write_text("[]", encoding="utf-8")
    return Config(home), home


class TestTheUpgrade(unittest.TestCase):
    """Nobody should lose their working radio to an upgrade."""

    def setUp(self):
        self.homes = []

    def tearDown(self):
        for h in self.homes:
            shutil.rmtree(h, ignore_errors=True)

    def build(self, radio):
        cfg, home = a_config(radio)
        self.homes.append(home)
        rigs.ensure_rigs(cfg)
        return cfg.data["radio"]

    def test_a_mock_rig_always_exists(self):
        """The board has to be usable with the shack powered down."""
        got = self.build({})
        self.assertTrue(any(r["backend"] == "mock" for r in got["rigs"]))

    def test_the_old_rigctld_becomes_an_entry(self):
        got = self.build({"rigctld_host": "10.0.0.5", "rigctld_port": 4533,
                          "mock": False})
        daemon = next(r for r in got["rigs"] if r["backend"] == "rigctld")
        self.assertEqual(daemon["host"], "10.0.0.5")
        self.assertEqual(daemon["port"], 4533)

    def test_a_config_that_was_driving_a_radio_comes_back_on_it(self):
        got = self.build({"rigctld_host": "127.0.0.1", "rigctld_port": 4532,
                          "mock": False})
        active = next(r for r in got["rigs"] if r["id"] == got["active"])
        self.assertEqual(active["backend"], "rigctld")

    def test_a_config_that_was_mocked_stays_mocked(self):
        got = self.build({"rigctld_host": "127.0.0.1", "rigctld_port": 4532,
                          "mock": True})
        active = next(r for r in got["rigs"] if r["id"] == got["active"])
        self.assertEqual(active["backend"], "mock")

    def test_running_it_twice_does_not_duplicate(self):
        cfg, home = a_config({"rigctld_host": "h", "rigctld_port": 1, "mock": True})
        self.homes.append(home)
        rigs.ensure_rigs(cfg)
        rigs.ensure_rigs(cfg)
        kinds = [r["backend"] for r in cfg.data["radio"]["rigs"]]
        self.assertEqual(sorted(kinds), ["mock", "rigctld"])

    def test_every_rig_gets_an_id(self):
        got = self.build({"rigs": [{"name": "no id", "backend": "mock"}]})
        self.assertTrue(all(r.get("id") for r in got["rigs"]))

    def test_an_active_pointing_at_nothing_is_repaired(self):
        got = self.build({"active": "gone-away"})
        self.assertTrue(any(r["id"] == got["active"] for r in got["rigs"]))


class TestCleaningAnEntry(unittest.TestCase):
    """A field the form hides must not survive in the file: a stale COM port
    on an OmniRig entry is something the operator still believes in."""

    def test_omnirig_keeps_only_its_slot(self):
        got = rigs.clean_rig({"id": "x", "name": "FT-900", "backend": "omnirig",
                              "slot": 2, "port": "COM3", "baud": 9600})
        self.assertEqual(got, {"id": "x", "name": "FT-900",
                               "backend": "omnirig", "slot": 2,
                               "audio_in": "", "audio_out": ""})

    def test_the_sound_card_survives_every_backend(self):
        """Which sound card a radio is wired to has nothing to do with how
        its CAT is reached, and changing backend must not throw it away."""
        for kind in rigs.BACKENDS:
            with self.subTest(backend=kind):
                got = rigs.clean_rig({"backend": kind,
                                      "audio_in": "USB Audio CODEC",
                                      "audio_out": "Speakers (USB)"})
                self.assertEqual(got["audio_in"], "USB Audio CODEC")
                self.assertEqual(got["audio_out"], "Speakers (USB)")

    def test_a_device_is_trimmed_and_bounded(self):
        got = rigs.clean_rig({"backend": "mock", "audio_in": "  mic  ",
                              "audio_out": "x" * 400})
        self.assertEqual(got["audio_in"], "mic")
        self.assertEqual(len(got["audio_out"]), 120)

    def test_a_bad_slot_falls_back_to_one(self):
        self.assertEqual(rigs.clean_rig({"backend": "omnirig", "slot": 9})["slot"], 1)

    def test_hamlib_keeps_the_port_settings(self):
        got = rigs.clean_rig({"backend": "hamlib", "model": 1016,
                              "port": "COM3", "baud": 4800, "stop_bits": 2,
                              "slot": 2})
        self.assertEqual(got["model"], 1016)
        self.assertEqual(got["port"], "COM3")
        self.assertEqual(got["stop_bits"], 2)
        self.assertNotIn("slot", got)

    def test_an_unknown_backend_becomes_the_mock(self):
        """Rather than a rig that cannot be opened and says nothing useful."""
        self.assertEqual(rigs.clean_rig({"backend": "telepathy"})["backend"],
                         "mock")

    def test_a_nameless_rig_still_has_a_name(self):
        self.assertTrue(rigs.clean_rig({"backend": "mock"})["name"])

    def test_it_always_has_an_id(self):
        self.assertTrue(rigs.clean_rig({"backend": "mock"})["id"])


class TestOmniRigConstants(unittest.TestCase):
    """Read off OmniRig's own type library, not assumed.

    ST_ONLINE is 4 and Status 3 is "not responding"; the obvious guess of
    3-for-online would report a silent radio as working.
    """

    def test_online_is_four(self):
        self.assertEqual(rigs.ST_ONLINE, 4)
        self.assertEqual(rigs.ST_NOTRESPONDING, 3)

    def test_the_modes_round_trip(self):
        for flag, name in rigs.OMNI_MODES.items():
            with self.subTest(mode=name):
                self.assertEqual(rigs.MODE_TO_OMNI[name], flag)

    def test_sidebands_are_not_swapped(self):
        """OmniRig thinks in sidebands and ADIF thinks in names; getting
        these the wrong way round would log every QSO on the wrong one."""
        self.assertEqual(rigs.OMNI_MODES[33554432], "USB")
        self.assertEqual(rigs.OMNI_MODES[67108864], "LSB")

    def test_tx_and_rx_are_distinct(self):
        self.assertNotEqual(rigs.PM_TX, rigs.PM_RX)


class TestTheManager(unittest.TestCase):
    def setUp(self):
        self.cfg, self.home = a_config({})
        self.addCleanup(shutil.rmtree, self.home, True)
        rigs.ensure_rigs(self.cfg)
        self.man = rigs.RigManager(self.cfg)
        self.addCleanup(self.man.stop)

    def test_it_comes_up_on_the_mock(self):
        st = self.man.state()
        self.assertTrue(st["ok"])
        self.assertTrue(st["mock"])

    def test_the_mock_can_be_tuned(self):
        self.man.set_freq(14074000)
        self.assertEqual(self.man.state()["freq"], 14074000)
        self.assertEqual(self.man.state()["band"], "20m")

    def test_the_state_says_which_radio_it_is(self):
        st = self.man.state()
        self.assertEqual(st["rig_id"], self.cfg.data["radio"]["active"])
        self.assertTrue(st["rig"])

    def test_the_state_carries_the_sound_cards(self):
        """A decoder card asks one question - what is the radio doing - and
        the answer has to include which sound card is its audio, or it would
        have to track the active rig itself and could disagree about it."""
        rig = next(r for r in self.cfg.data["radio"]["rigs"]
                   if r["id"] == self.cfg.data["radio"]["active"])
        rig["audio_in"] = "Microphone (USB Audio CODEC)"
        rig["audio_out"] = "Speakers (USB Audio CODEC)"
        st = self.man.state()
        self.assertEqual(st["audio_in"], "Microphone (USB Audio CODEC)")
        self.assertEqual(st["audio_out"], "Speakers (USB Audio CODEC)")

    def test_a_rig_with_no_audio_says_so_rather_than_omitting_it(self):
        """A missing key reads as "the host is too old to know", an empty
        one as "nothing chosen"; a card has to be able to tell them apart."""
        st = self.man.state()
        self.assertEqual(st["audio_in"], "")
        self.assertEqual(st["audio_out"], "")

    def test_selecting_a_rig_that_cannot_open_reports_it(self):
        """Rather than leaving the old one live and looking like it worked."""
        self.cfg.data["radio"]["rigs"].append(
            {"id": "bad", "name": "No Hamlib", "backend": "hamlib",
             "model": 1, "port": "COM99"})
        with mock.patch.object(rigs, "find_rigctld", return_value=""):
            st = self.man.select("bad")
        self.assertFalse(st["ok"])
        self.assertIn("rigctld", st["error"])

    def test_selecting_an_unknown_id_falls_back_rather_than_breaking(self):
        st = self.man.select("nonsense")
        self.assertTrue(st["ok"])

    def test_selecting_closes_the_one_before_it(self):
        """Two programs cannot share a serial port and neither can two of
        ours."""
        closed = []
        before = self.man.backend()
        with mock.patch.object(type(before), "close",
                               side_effect=lambda: closed.append(1)):
            self.man.select(self.cfg.data["radio"]["rigs"][0]["id"])
        self.assertTrue(closed)

    def test_testing_a_rig_does_not_change_the_live_one(self):
        was = self.man.state()["rig_id"]
        self.man.test({"id": "other", "name": "x", "backend": "mock"})
        self.assertEqual(self.man.state()["rig_id"], was)

    def test_a_test_says_what_it_heard(self):
        got = self.man.test({"name": "x", "backend": "mock"})
        self.assertTrue(got["ok"])
        self.assertIn("MHz", got["message"])


class TestHamlibWithoutHamlib(unittest.TestCase):
    """Hamlib is not installed on every machine, and the card has to say so
    rather than offering an empty dropdown that looks broken."""

    def test_the_model_list_explains_itself(self):
        with mock.patch.object(rigs, "find_rigctld", return_value=""):
            got = rigs.hamlib_models()
        self.assertFalse(got["ok"])
        self.assertEqual(got["models"], [])
        self.assertIn("Hamlib", got["error"])

    def test_starting_a_rig_without_it_names_the_fix(self):
        with mock.patch.object(rigs, "find_rigctld", return_value=""):
            with self.assertRaises(rigs.RigError) as got:
                rigs.HamlibBackend(model=1, port="COM3")
        # The message names what to do, not just what failed: the fetch
        # script first, since that is the one that needs no decisions.
        said = str(got.exception)
        self.assertIn("tools/get_hamlib.py", said)
        self.assertIn("Settings -> Radio", said)

    def test_a_rig_with_no_port_is_refused_before_spawning_anything(self):
        with mock.patch.object(rigs, "find_rigctld", return_value="rigctld"):
            with self.assertRaises(rigs.RigError) as got:
                rigs.HamlibBackend(model=1, port="")
        self.assertIn("serial port", str(got.exception))

    def test_a_rig_with_no_model_is_refused_too(self):
        with mock.patch.object(rigs, "find_rigctld", return_value="rigctld"):
            with self.assertRaises(rigs.RigError) as got:
                rigs.HamlibBackend(model=0, port="COM3")
        self.assertIn("model", str(got.exception))


if __name__ == "__main__":
    unittest.main()


class TestRigctldModeWidth(unittest.TestCase):
    """Measured against an FT-900 on COM13 through rigctld 4.7.2, and the
    same on Hamlib's FT-890 backend: `M USB 0` is refused. 0 is the
    documented "use the rig's default passband", so a card that does not
    care about the width could not change mode at all on that radio."""

    def a_rig(self, refuse):
        b = rigs.RigctldBackend()
        b.sent = []

        def cmd(line):
            b.sent.append(line)
            if line in refuse:
                raise rigs.RigError(f"rig returned error -1 for {line!r}")
            return []
        b._cmd = cmd
        return b

    def test_a_refused_default_width_falls_back_to_no_change(self):
        b = self.a_rig({"M USB 0"})
        b.set_mode("USB")
        self.assertEqual(b.sent, ["M USB 0", "M USB -1"])

    def test_a_rig_that_takes_it_is_asked_once(self):
        b = self.a_rig(set())
        b.set_mode("USB")
        self.assertEqual(b.sent, ["M USB 0"])

    def test_a_width_we_were_given_is_not_second_guessed(self):
        """A refused *explicit* width is a real error - the operator asked
        for 500 Hz and the radio said no, which they need to hear."""
        b = self.a_rig({"M CW 500"})
        with self.assertRaises(rigs.RigError):
            b.set_mode("CW", 500)
        self.assertEqual(b.sent, ["M CW 500"])


class TestRigctldSecondVfo(unittest.TestCase):
    def a_rig(self, answers):
        b = rigs.RigctldBackend()
        b._cmd = lambda line: answers[line]
        return b

    def test_the_other_vfo_comes_back_as_bare_values(self):
        """rigctld 4.7.2 answers `\get_vfo_info VFOB` with one value per
        line and no labels: ['14300000', 'USB', '2200', '0', '0']. Looking
        for a "Freq:" label found nothing and dropped the second VFO with
        the number sitting right there."""
        b = self.a_rig({
            "f": ["7041000"], "m": ["USB", "2200"], "t": ["0"],
            "v": ["VFOA"], "s": ["0", "VFOA"],
            "\get_vfo_info VFOB": ["14300000", "USB", "2200", "0", "0"],
        })
        st = b.state()
        self.assertEqual(st["freq_a"], 7_041_000)
        self.assertEqual(st["freq_b"], 14_300_000)
        self.assertEqual(st["vfo"], "A")

    def test_a_labelled_answer_still_works(self):
        b = self.a_rig({
            "f": ["7041000"], "m": ["USB", "2200"], "t": ["0"],
            "v": ["VFOA"], "s": ["0", "VFOA"],
            "\get_vfo_info VFOB": ["Freq: 14300000", "Mode: USB"],
        })
        self.assertEqual(b.state()["freq_b"], 14_300_000)
