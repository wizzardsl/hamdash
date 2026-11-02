"""The cards that follow a radio must cope with radios that are not the FT-900.

Each rule here was a wrong answer that looked right on some radio:

* the mode select showed LSB for a radio in USB-D, because USB-D was not
  in a fixed list - and then LSB could not be chosen, since it already
  appeared to be;
* a memory whose mode the radio refused never tuned the frequency;
* a digital spot's mode (FT8) was refused by every radio and the refusal
  swallowed, so the radio tuned and stayed in CW with nothing said;
* the logbook wrote the radio's data mode (PKTUSB) into the log.

Run with:  python -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RIG = (ROOT / "plugins" / "rig_control" / "index.html").read_text(encoding="utf-8")
SPOTS = (ROOT / "plugins" / "spots" / "index.html").read_text(encoding="utf-8")
LOG = (ROOT / "plugins" / "logbook" / "index.html").read_text(encoding="utf-8")


def body(src: str, head: str) -> str:
    at = src.index(head)
    return src[at:src.index("\n}\n", at)]


class TestTheRigCard(unittest.TestCase):
    def test_the_mode_list_is_this_radio_s(self):
        self.assertNotIn("HD.radio.modes()", RIG,
                         "the selected radio's list, on a tile bound to another")
        self.assertIn("await radio.modes()", RIG)
        # Reread when the state comes from a different radio.
        self.assertIn("s.rig_id !== modesFor", body(RIG, "function paint(s)"))

    def test_a_mode_not_in_the_list_is_shown_as_itself(self):
        sync = body(RIG, "function syncMode(s)")
        self.assertIn("odd.disabled = true", sync)
        self.assertIn("(set on the radio)", sync)
        self.assertIn("syncMode(s)", body(RIG, "function paint(s)"))

    def test_a_refused_mode_puts_the_select_back(self):
        self.assertRegex(RIG, r"radio\.mode\(e\.target\.value\)[\s\S]{0,80}syncMode\(live\)")

    def test_a_memory_tunes_even_when_its_mode_is_refused(self):
        rec = body(RIG, "async function recall(m)")
        mode_at = rec.index("await radio.mode(mode)")
        # The mode call is in a try of its own, before the frequency one.
        self.assertIn("catch (e) { refused = e; }", rec[mode_at:mode_at + 80])
        self.assertIn("mode !== '?'", rec)

    def test_no_frequency_is_not_a_reading(self):
        self.assertIn("Number(s.freq) > 0", body(RIG, "function paint(s)"))

    def test_metering_follows_what_the_radio_can_do_now(self):
        self.assertIn("meterable !== !!metering", body(RIG, "function paint(s)"))


class TestTheSpotsCard(unittest.TestCase):
    def test_a_refused_mode_is_not_swallowed(self):
        self.assertNotRegex(SPOTS, r"HD\.radio\.mode\([^)]*\)\.catch\(\(\) => \{\}\)")

    def test_digital_modes_ask_for_the_data_mode(self):
        m = re.search(r"const DIGITAL = /(.+)/;", SPOTS)
        self.assertTrue(m)
        pat = re.compile(m.group(1).replace("\\\\", "\\"))
        for mode in ("FT8", "FT4", "JS8", "PSK31", "DATA"):
            self.assertTrue(pat.match(mode), mode)
        for mode in ("CW", "USB", "LSB", "FM", "AM", "RTTY"):
            self.assertFalse(pat.match(mode), mode)

    def test_one_function_per_name(self):
        """A second `function tuneMode` silently replaced the first."""
        names = re.findall(r"^(?:async )?function (\w+)\(", SPOTS, re.M)
        self.assertEqual(len(names), len(set(names)),
                         sorted(n for n in names if names.count(n) > 1))


class TestTheLogbook(unittest.TestCase):
    def test_a_data_mode_is_not_written_into_the_log(self):
        follow = body(LOG, "function follow()")
        self.assertIn("!m.startsWith('PKT')", follow)
        self.assertIn("CWR: 'CW'", follow)


if __name__ == "__main__":
    unittest.main()
