"""Radios that are not the one radio this was built against.

Everything in rigs.py was proven on a Yaesu FT-890/FT-900, through flrig,
Hamlib and OmniRig. These tests play other radios' answers at it - an
IC-7300's mode names, a Main/Sub radio, an older flrig, a Hamlib build
that prints decimals, a reply split across two TCP segments - and check
the result is either right or honestly "unknown", never an exception, a
desynchronised socket, or a wrong value that looks right.

The rigctld replies are in the extended protocol, as recorded from a real
rigctld 4.7.2 (`rigctld -m 1`, Hamlib's dummy radio).

Run with:  python -m unittest discover -s tests
"""
import json
import math
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from hamdash.config import Config
from hamdash.services import rigs
from hamdash.services.radio import MODES, canon_mode, radio_mode

IC7300_MODES = ["LSB", "USB", "AM", "CW", "RTTY", "FM", "CW-R", "RTTY-R",
                "LSB-D", "USB-D", "AM-D", "FM-D"]
FT991_MODES = ["LSB", "USB", "CW-U", "FM", "AM", "RTTY-L", "CW-L", "DATA-L",
               "RTTY-U", "DATA-FM", "FM-N", "DATA-U", "AM-N", "C4FM"]


def a_rigctld(answers: dict, default=None):
    """A RigctldBackend whose `_cmd` answers from a table, as values - what
    the extended-protocol reader hands back. A value that is an Exception
    is raised."""
    b = rigs.RigctldBackend()
    b.sent = []

    def cmd(line):
        b.sent.append(line)
        got = answers.get(line, default)
        if isinstance(got, Exception):
            raise got
        if got is None:
            raise rigs.RigError(f"rig returned error -11 for {line!r}")
        return list(got)

    b._cmd = cmd
    return b


REFUSED = rigs.RigError("rig returned error -11")
BASIC = {"f": ["14074000"], "m": ["USB", "2400"], "t": ["0"],
         "v": ["VFOA"], "\\get_vfo_info VFOB": ["14300000", "USB", "2400",
                                                "0", "0"],
         "s": ["0", "None"]}


class TestTheModeNames(unittest.TestCase):
    """One table, both directions, so reading and writing cannot disagree."""

    def test_every_radio_s_data_mode_is_one_mode(self):
        for name in ("USB-D", "DATA-U", "PKTUSB", "PKT-U", "DATA-USB"):
            with self.subTest(name=name):
                self.assertEqual(canon_mode(name), "PKTUSB")

    def test_reverse_and_filter_names(self):
        for name, want in [("CW-R", "CWR"), ("RTTY-R", "RTTYR"),
                           ("CW-W", "CW"), ("CW-N", "CW"), ("AM-N", "AM"),
                           ("FM-N", "FM"), ("FSK", "RTTY"), ("CW-U", "CW")]:
            with self.subTest(name=name):
                self.assertEqual(canon_mode(name), want)

    def test_an_unknown_name_is_kept_not_guessed(self):
        self.assertEqual(canon_mode("c4fm"), "C4FM")
        self.assertEqual(canon_mode("WFM"), "WFM")

    def test_writing_finds_the_radio_s_own_spelling(self):
        self.assertEqual(radio_mode("PKTUSB", IC7300_MODES), "USB-D")
        self.assertEqual(radio_mode("CWR", IC7300_MODES), "CW-R")
        self.assertEqual(radio_mode("PKTUSB", FT991_MODES), "DATA-U")
        self.assertEqual(radio_mode("DATA-U", ["PKTUSB", "USB"]), "PKTUSB")
        self.assertEqual(radio_mode("FT8", IC7300_MODES), "")

    def test_every_default_mode_round_trips(self):
        for m in MODES:
            self.assertEqual(canon_mode(m), m)


class TestTheRigctldReader(unittest.TestCase):
    def test_labels_come_off_and_the_echo_goes(self):
        got = rigs._extended_values(
            "get_mode:\nMode: USB\nPassband: 2400\nRPRT 0\n", "m")
        self.assertEqual(got, ["USB", "2400"])
        got = rigs._extended_values("get_level: STRENGTH\n-24\nRPRT 0\n",
                                    "l STRENGTH")
        self.assertEqual(got, ["-24"])
        got = rigs._extended_values(
            "get_vfo_info: VFOB\nFreq: 146000000\nMode: FM\nWidth: 15000\n"
            "Split: 0\nSatMode: 0\nRPRT 0\n", "\\get_vfo_info VFOB")
        self.assertEqual(got[0], "146000000")

    def test_an_error_code_raises(self):
        with self.assertRaises(rigs.RigError) as got:
            rigs._extended_values("set_mode: FOO -1\nRPRT -1\n", "M FOO -1")
        self.assertIn("-1", str(got.exception))

    def test_a_reply_without_a_newline_before_rprt(self):
        """Hamlib's own `\\get_modes` does that, measured on 4.7.2."""
        self.assertTrue(rigs._RPRT_END.search(b"...0.0 HzRPRT 0\n"))

    def test_a_reply_split_across_two_segments_is_read_whole(self):
        """The reader stopped at the first newline. A multi-line reply that
        arrived in two pieces left its tail for the next command - which
        read the passband as the PTT. Never on loopback; on a network,
        yes. Here the reply is sent in two halves with a pause between."""
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        self.addCleanup(server.close)

        def serve():
            conn, _ = server.accept()
            with conn:
                f = conn.makefile("rb")
                for raw in f:
                    line = raw.decode().strip()
                    if line == "+m":
                        conn.sendall(b"get_mode:\nMode: USB\n")
                        time.sleep(0.1)
                        conn.sendall(b"Passband: 2400\nRPRT 0\n")
                    elif line == "+t":
                        conn.sendall(b"get_ptt:\nPTT: 0\nRPRT 0\n")
                    else:
                        conn.sendall(b"RPRT -11\n")

        threading.Thread(target=serve, daemon=True).start()
        b = rigs.RigctldBackend(port=port)
        self.addCleanup(b.close)
        self.assertEqual(b._cmd("m"), ["USB", "2400"])
        self.assertEqual(b._cmd("t"), ["0"], "the next reply is its own")


class TestRigctldRadios(unittest.TestCase):
    def test_an_ordinary_radio(self):
        st = a_rigctld(BASIC).state()
        self.assertTrue(st["ok"])
        self.assertEqual((st["freq"], st["mode"], st["width"]),
                         (14074000, "USB", 2400))
        self.assertEqual((st["vfo"], st["freq_b"]), ("A", 14300000))
        self.assertIn(rigs.F_VFO, st["features"])
        self.assertIn(rigs.F_SPLIT, st["features"])

    def test_a_main_sub_radio_offers_no_a_and_b(self):
        st = a_rigctld({**BASIC, "v": ["Main"]}).state()
        self.assertTrue(st["ok"])
        self.assertNotIn("vfo", st)
        self.assertNotIn(rigs.F_VFO, st["features"],
                         "A/B buttons on a Main/Sub radio do nothing useful")

    def test_a_radio_that_will_not_say_its_ptt(self):
        st = a_rigctld({**BASIC, "t": REFUSED}).state()
        self.assertTrue(st["ok"], "a radio that cannot say is still there")
        self.assertFalse(st["ptt_known"])

    def test_no_split_answer_means_no_split_button(self):
        st = a_rigctld({**BASIC, "s": REFUSED}).state()
        self.assertNotIn("split", st)
        self.assertNotIn(rigs.F_SPLIT, st["features"])

    def test_a_decimal_frequency_for_the_other_vfo(self):
        """isdigit() rejected 14300000.000000, skipped the mode and took
        the passband, 2200, as VFO B."""
        st = a_rigctld({**BASIC, "\\get_vfo_info VFOB":
                        ["14300000.000000", "USB", "2200", "0", "0"]}).state()
        self.assertEqual(st["freq_b"], 14300000)

    def test_nonsense_frequencies_are_not_readings(self):
        for raw in (["inf"], ["nan"], [""], [], ["0"], ["-5"]):
            with self.subTest(raw=raw):
                st = a_rigctld({**BASIC, "f": raw}).state()
                self.assertFalse(st["ok"])

    def test_a_passband_that_is_not_a_number(self):
        st = a_rigctld({**BASIC, "m": ["USB", "Normal"]}).state()
        self.assertTrue(st["ok"])
        self.assertEqual(st["width"], 0)

    def test_meters_that_say_nan_or_too_much(self):
        b = a_rigctld({"l STRENGTH": ["nan"], "l RFPOWER_METER": ["37"],
                       "l SWR": ["0.4"]})
        self.assertEqual(b.meters(False), {}, "NaN is no reading")
        tx = b.meters(True)
        self.assertEqual(tx["power_pct"], 100.0, "clamped to its track")
        self.assertNotIn("swr", tx, "below 1 is not an SWR")
        json.dumps(tx)                       # and it is valid JSON

    def test_split_names_the_vfo_we_are_not_on(self):
        for now, other in (("VFOA", "VFOB"), ("VFOB", "VFOA"),
                           ("Main", "Sub")):
            with self.subTest(on=now):
                b = a_rigctld({"v": [now], f"S 1 {other}": []})
                b.set_split(True)
                self.assertEqual(b.sent[-1], f"S 1 {other}")

    def test_a_mode_the_radio_lacks_is_refused_with_its_list(self):
        b = a_rigctld({"M ?": ["USB LSB CW CWR AM FM RTTY"],
                       "M USB 0": []})
        with self.assertRaises(rigs.RigError) as got:
            b.set_mode("DATA-U")
        self.assertIn("CWR", str(got.exception))
        self.assertNotIn("M DATA-U 0", b.sent)
        b.set_mode("usb")
        self.assertIn("M USB 0", b.sent)

    def test_a_data_mode_is_sent_in_hamlib_s_name(self):
        b = a_rigctld({"M ?": ["USB LSB PKTUSB"], "M PKTUSB 0": []})
        b.set_mode("DATA-U")
        self.assertIn("M PKTUSB 0", b.sent)

    def test_the_modes_it_offers_are_its_own(self):
        b = a_rigctld({"M ?": ["AM CW USB LSB RTTY FM WFM CWR RTTYR"]})
        self.assertIn("WFM", b.offered())
        self.assertNotIn("PKTUSB", b.offered())


class FakeFlrig:
    def __init__(self, values, missing=()):
        self.values, self.missing = dict(values), set(missing)
        self.sent = []

    def __call__(self, name, *args):
        self.sent.append((name, args))
        if name in self.missing:
            raise rigs.RigError(f"flrig refused {name}: method not found")
        return self.values.get(name, "")


IC7300 = {"rig.get_vfoA": "7074000", "rig.get_vfoB": "14074000",
          "rig.get_AB": "A", "rig.get_split": 0, "rig.get_mode": "USB-D",
          "rig.get_ptt": 0, "rig.get_modes": IC7300_MODES,
          "rig.get_smeter": "30", "rig.get_Sunits": "S 5",
          "rig.get_DBM": "-97"}


def a_flrig(values, missing=()):
    b = rigs.FlrigBackend()
    b._call = FakeFlrig(values, missing)
    return b


class TestFlrigRadios(unittest.TestCase):
    def test_an_ic7300_in_its_data_mode(self):
        st = a_flrig(IC7300).state()
        self.assertEqual(st["mode"], "PKTUSB")
        self.assertEqual(st["rig_mode"], "USB-D")

    def test_writing_uses_the_radio_s_spelling(self):
        b = a_flrig(IC7300)
        b.set_mode("PKTUSB")
        b.set_mode("CWR")
        sent = [a for n, a in b._call.sent if n == "rig.set_mode"]
        self.assertEqual(sent, [("USB-D",), ("CW-R",)])

    def test_what_it_offers_is_its_list_in_our_names(self):
        got = a_flrig(IC7300).offered()
        self.assertIn("PKTUSB", got)
        self.assertIn("CWR", got)
        self.assertEqual(len(got), len(set(got)))

    def test_an_older_flrig_without_get_ab_is_not_offline(self):
        """All six reads or nothing: one missing method showed a working
        radio as offline."""
        st = a_flrig(IC7300, missing={"rig.get_AB", "rig.get_split"}).state()
        self.assertTrue(st["ok"])
        self.assertNotIn("vfo", st)
        self.assertNotIn(rigs.F_VFO, st["features"])
        self.assertNotIn(rigs.F_SWAP, st["features"])
        self.assertNotIn(rigs.F_SPLIT, st["features"])

    def test_a_split_answered_in_words(self):
        st = a_flrig({**IC7300, "rig.get_split": "false"}).state()
        self.assertTrue(st["ok"])
        self.assertFalse(st["split"])

    def test_an_ab_answered_as_vfob(self):
        st = a_flrig({**IC7300, "rig.get_AB": "VFOB"}).state()
        self.assertEqual((st["vfo"], st["freq"]), ("B", 14074000))

    def test_losing_flrig_itself_is_still_offline(self):
        b = rigs.FlrigBackend()

        def gone(name, *a):
            raise rigs.RigError("no flrig at 127.0.0.1:12345 (refused)")

        b._call = gone
        self.assertFalse(b.state()["ok"])

    def test_a_missing_meter_call_keeps_the_others(self):
        m = a_flrig(IC7300, missing={"rig.get_Sunits", "rig.get_DBM"}).meters(False)
        self.assertEqual(m["s_pct"], 30.0)
        self.assertTrue(m["s_units"].startswith("S "), "computed instead")

    def test_a_mode_list_sent_as_one_string(self):
        b = a_flrig({**IC7300, "rig.get_modes": "LSB USB CW-R USB-D"})
        self.assertEqual(b.modes(), ["LSB", "USB", "CW-R", "USB-D"])


class Raising(rigs.MockBackend):
    def state(self):
        raise ValueError("could not convert string to float: 'Main'")

    def meters(self, tx):
        raise ZeroDivisionError("float division by zero")


def a_link():
    home = Path(tempfile.mkdtemp(prefix="hamdash-var-"))
    (home / "config.json").write_text(json.dumps({"radio": {}}))
    (home / "layout.json").write_text("[]")
    cfg = Config(home)
    rigs.ensure_rigs(cfg)
    link = rigs.RigManager(cfg).link()
    return link, home


class TestNothingUnexpectedEscapes(unittest.TestCase):
    def setUp(self):
        self.link, home = a_link()
        self.addCleanup(shutil.rmtree, home, True)
        self.addCleanup(self.link.stop)
        self.link._backend = Raising()
        self.link._error = ""

    def test_a_read_that_raises_is_an_offline_radio_not_a_500(self):
        st = self.link.state()
        self.assertFalse(st["ok"])
        self.assertIn("ValueError", st["error"])

    def test_meters_that_raise_are_no_meters(self):
        m = self.link.meters()
        self.assertFalse(m["ok"])

    def test_the_modes_fall_back_to_the_usual_list(self):
        self.assertEqual(self.link.offered(), list(MODES))


class Recording(rigs.MockBackend):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = []

    def set_ptt(self, on):
        self.calls.append(bool(on))
        super().set_ptt(on)


class TestTheWatchdogRunsWhenTheReadFails(unittest.TestCase):
    """It sat in the same `try` as the read: a radio whose read kept
    raising kept the watchdog from ever running, and a transmission
    hamdash keyed stayed on the air."""

    def test_an_unheld_transmission_is_dropped_while_reads_fail(self):
        link, home = a_link()
        self.addCleanup(shutil.rmtree, home, True)
        self.addCleanup(link.stop)
        rig = Recording()
        link._backend, link._error = rig, ""
        link.set_ptt(True)
        later = time.time() + rigs.PTT_HOLD + 5
        with mock.patch.object(rigs.time, "time", lambda: later), \
                mock.patch.object(link, "state",
                                  side_effect=RuntimeError("serial port gone")):
            link.start_polling(hz=50.0)
            end = time.monotonic() + 3.0
            while False not in rig.calls and time.monotonic() < end:
                time.sleep(0.01)
            # Before stop(): closing a keyed radio unkeys it too, which
            # would pass this whether or not the watchdog ever ran.
            seen = list(rig.calls)
            link.stop()
        self.assertIn(False, seen,
                      "the watchdog never ran while the read was failing")


if __name__ == "__main__":
    unittest.main()
