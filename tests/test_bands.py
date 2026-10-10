"""One band table, and the edges it is asked for.

This exists because there were three. `radio.py`, `logbook.py` and the PSK
card each carried their own copy and they disagreed - 5.3 MHz was 60m to the
logbook and out of band to the rig, and only one of them had heard of 630m.
A band is written into the log and shown on the map, so two answers to the
same question is two answers in front of the operator.

Run with:  python -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

from hamdash.services import bands
from hamdash.services.logbook import band_for as log_band
from hamdash.services.qsl import BANDS as qsl_bands
from hamdash.services.radio import band_for as rig_band

ROOT = Path(__file__).resolve().parent.parent


class TestThereIsOnlyOne(unittest.TestCase):
    def test_everyone_asks_the_same_table(self):
        self.assertIs(qsl_bands, bands.BANDS)
        self.assertIs(log_band, bands.for_mhz)
        self.assertIs(rig_band, bands.for_hz)

    def test_nobody_declares_their_own(self):
        """A second table in the tree is the drift starting again."""
        pattern = re.compile(r"^BANDS\s*[:=]", re.M)
        for path in list(ROOT.glob("hamdash/**/*.py")) + \
                list(ROOT.glob("plugins/**/*.py")):
            if path.name == "bands.py":
                continue
            with self.subTest(file=path.name):
                self.assertFalse(pattern.search(
                    path.read_text(encoding="utf-8")),
                    f"{path} declares its own BANDS")

    def test_the_browser_gets_the_same_rows(self):
        """`/api/bands` feeds the logbook's dropdown; a fourth copy in JS is
        the same bug with a different file extension."""
        self.assertEqual(len(bands.as_json()), len(bands.BANDS))
        self.assertEqual(bands.as_json()[0], list(bands.BANDS[0]))
        self.assertEqual(bands.NAMES, [n for _, _, n in bands.BANDS])


class TestWhichBand(unittest.TestCase):
    def test_the_usual_digital_watering_holes(self):
        for mhz, want in [(14.074, "20m"), (7.074, "40m"), (10.136, "30m"),
                          (3.573, "80m"), (21.074, "15m"), (28.074, "10m"),
                          (50.313, "6m"), (144.174, "2m")]:
            with self.subTest(mhz=mhz):
                self.assertEqual(bands.for_mhz(mhz), want)

    def test_sixty_metres_covers_every_country_s_channels(self):
        """It is channelised and the channels differ; calling a legal QSO
        out of band is worse than being a little wide."""
        self.assertEqual(bands.for_mhz(5.3305), "60m")
        self.assertEqual(bands.for_mhz(5.403), "60m")

    def test_the_bands_only_one_table_had(self):
        self.assertEqual(bands.for_mhz(0.4745), "630m")
        self.assertEqual(bands.for_mhz(0.1365), "2200m")
        self.assertEqual(bands.for_mhz(70.2), "4m")

    def test_off_band_is_empty_not_the_nearest(self):
        """The logbook blanks its dropdown on this, so "" has to mean it."""
        self.assertEqual(bands.for_mhz(15.0), "")
        self.assertEqual(bands.for_mhz(0), "")

    def test_nonsense_does_not_raise(self):
        """It is fed straight from a text box."""
        for junk in (None, "", "abc", float("nan")):
            with self.subTest(junk=junk):
                self.assertEqual(bands.for_mhz(junk), "")

    def test_hz_and_mhz_agree(self):
        self.assertEqual(bands.for_hz(14074000), bands.for_mhz(14.074))
        self.assertEqual(bands.for_hz(None), "")

    def test_the_edges_belong_to_their_band(self):
        self.assertEqual(bands.for_mhz(14.0), "20m")
        self.assertEqual(bands.for_mhz(14.35), "20m")
        self.assertEqual(bands.for_mhz(14.351), "")

    def test_the_table_is_ordered_and_does_not_overlap(self):
        """`for_mhz` returns the first match, so an overlap would hide a
        band rather than report it."""
        last = 0.0
        for lo, hi, name in bands.BANDS:
            with self.subTest(band=name):
                self.assertLess(lo, hi)
                self.assertGreater(lo, last)
            last = hi


if __name__ == "__main__":
    unittest.main()
