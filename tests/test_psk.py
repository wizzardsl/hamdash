"""PSK31, driven with synthesised BPSK31 whose content is known exactly.

`plugins/decoder/psk.py` imports nothing from the card, for the reason
`cw.py` and `rtty.py` do not: a decoder prints letters whether it is right
or wrong, and on a real band nobody can tell a decoder fault from QSB.

The Varicode table was checked entry by entry against fldigi's when it was
written - not here, because a table checked against itself checks nothing
("tests that assert against a guess test the guess"). What this file pins
is a handful of values everybody knows, the shape every code must have,
and the decoder's behaviour on signals that each broke a version of it:

* a squared PSK signal has weaker lines either side of the real one, which
  won during text and locked the decoder 15.6 Hz off, printing rubbish;
* a loud carrier beside the signal makes a mixing product that squares
  stronger than the PSK itself;
* noise, tried often enough, clears any single bar now and then.

SNR is signal power over noise power in 2.5 kHz, as PSK31 is usually
quoted.

Run with:  python -m unittest discover -s tests
"""
import difflib
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "plugins" / "decoder"))

import psk                                              # noqa: E402

TEXT = "the quick brown fox jumps over the lazy dog 1234567890 CQ DE VA7SLL K"
RATE = 12000


def decode(audio, rate=RATE):
    d = psk.PskDecoder(rate)
    out = []
    step = rate // 50
    for i in range(0, audio.size, step):
        out.append(d.feed(audio[i:i + step]))
    return "".join(out), d


def noisy(audio, snr_db, rate=RATE, seed=1):
    rng = np.random.default_rng(seed)
    ps = float(np.mean(audio[audio != 0] ** 2))
    sigma = np.sqrt(ps / 10 ** (snr_db / 10) * rate / 5000)
    return (audio + rng.normal(0, sigma, audio.size)).astype(np.float32)


def errors(got, want):
    return 1 - difflib.SequenceMatcher(None, got, want).ratio()


class TestVaricode(unittest.TestCase):
    def test_the_values_everybody_knows(self):
        known = {" ": "1", "e": "11", "t": "101", "o": "111", "a": "1011",
                 "i": "1101", "n": "1111", "E": "1110111", "A": "1111101",
                 "\n": "11101", "\r": "11111", "0": "10110111"}
        for ch, code in known.items():
            with self.subTest(ch=ch):
                self.assertEqual(psk.VARICODE[ord(ch)], code)

    def test_every_code_has_the_shape_that_makes_00_a_separator(self):
        self.assertEqual(len(psk.VARICODE), 128)
        self.assertEqual(len(set(psk.VARICODE)), 128)
        for i, code in enumerate(psk.VARICODE):
            with self.subTest(code=i):
                self.assertTrue(code.startswith("1") and code.endswith("1"))
                self.assertNotIn("00", code)
                self.assertLessEqual(len(code), 10)


class TestCleanSignals(unittest.TestCase):
    def test_anywhere_in_the_passband(self):
        for hz in (600.0, 1003.7, 2200.0):
            with self.subTest(hz=hz):
                got, d = decode(psk.psk_audio(TEXT, hz, RATE, lead_seconds=2.5))
                self.assertEqual(got, TEXT)
                self.assertAlmostEqual(d.carrier_hz, hz, delta=0.5)

    def test_at_the_mock_s_rate_and_an_odd_one(self):
        for rate in (48000, 44100):
            with self.subTest(rate=rate):
                got, _ = decode(psk.psk_audio(TEXT, 1500.0, rate,
                                              lead_seconds=2.5), rate)
                self.assertEqual(got, TEXT)

    def test_no_stray_character_ahead_of_the_text(self):
        """Read straight after lock, the first symbols were taken at
        whatever phase the clock started on - a "-" ahead of every over."""
        got, _ = decode(psk.psk_audio("CQ", 1000.0, RATE, lead_seconds=2.5))
        self.assertEqual(got, "CQ")

    def test_what_the_card_reports_is_plain_json(self):
        """np.bool_ in `hearing` made the card's /text reply a 500."""
        import json
        _, d = decode(psk.psk_audio("cq", 1000.0, RATE, lead_seconds=2.5))
        json.dumps({"hearing": d.hearing, "quality": d.quality,
                    "tone_hz": d.tone_hz, "snr_db": d.snr_db})

    def test_control_characters(self):
        got, _ = decode(psk.psk_audio("a\r\nb\tc", 1000.0, RATE,
                                      lead_seconds=2.5))
        self.assertEqual(got, "a\nb c")


class TestRealConditions(unittest.TestCase):
    def test_noise(self):
        base = psk.psk_audio(TEXT, 1234.0, RATE, lead_seconds=2.5)
        got, _ = decode(noisy(base, 0))
        self.assertEqual(got, TEXT)
        got, _ = decode(noisy(base, -5))
        self.assertLess(errors(got, TEXT), 0.05, got)

    def test_noise_alone_prints_nothing(self):
        for seed in (5, 6, 7):
            with self.subTest(seed=seed):
                rng = np.random.default_rng(seed)
                got, d = decode(rng.normal(0, 0.1, RATE * 30).astype(np.float32))
                self.assertEqual(got, "")

    def test_drift(self):
        audio = psk.psk_audio(TEXT, 1000.0, RATE, lead_seconds=2.5,
                              drift_hz_per_s=0.5)
        got, d = decode(noisy(audio, 0))
        self.assertEqual(got, TEXT)
        self.assertGreater(d.carrier_hz, 1005, "it followed the drift")

    def test_joining_an_over_part_way(self):
        audio = psk.psk_audio(TEXT, 1500.0, RATE, lead_seconds=0.0)
        got, _ = decode(noisy(audio[int(RATE * 1.3):], 5))
        self.assertIn("fox jumps over the lazy dog 1234567890", got)

    def test_a_weak_signal_lost_and_found_is_not_printed_twice(self):
        base = psk.psk_audio(TEXT, 1234.0, RATE, lead_seconds=2.5)
        got, _ = decode(noisy(base, -10))
        self.assertLessEqual(got.count("lazy"), 1, got)


class TestALoudCarrierBeside_It(unittest.TestCase):
    """A carrier 15 dB above the PSK, 466 Hz away."""

    @classmethod
    def setUpClass(cls):
        base = psk.psk_audio(TEXT, 1234.0, RATE, lead_seconds=2.5)
        t = np.arange(base.size) / RATE
        cls.audio = (noisy(base, 0)
                     + 3.0 * np.cos(2 * np.pi * 1700 * t)).astype(np.float32)

    def test_it_reads_the_psk(self):
        got, d = decode(self.audio)
        self.assertAlmostEqual(d.carrier_hz, 1234.0, delta=1.0)
        self.assertLess(errors(got, TEXT), 0.1, got)

    def test_the_loudest_line_is_the_carrier(self):
        """So 'the loudest bin' is the wrong answer - which is why the
        decoder squares, and why it does not stop there."""
        x = self.audio[-RATE * 3:].astype(np.float64)
        plain = np.abs(np.fft.rfft(x * np.hanning(x.size)))
        self.assertAlmostEqual(np.argmax(plain) * RATE / x.size, 1700, delta=2)

    def test_the_strongest_squared_line_is_not_the_psk_either(self):
        """The mixing product halfway between them squares stronger than
        the PSK. Only demodulating the candidates tells them apart."""
        x = self.audio[-RATE * 3:].astype(np.float64)
        first = psk.carriers(x, RATE)[0][0]
        self.assertGreater(abs(first - 1234.0), 100)
        self.assertGreater(psk.trial_quality(x[-RATE * 3:], RATE, 1234.0),
                           psk.TRIAL_QUALITY)
        self.assertLess(psk.trial_quality(x[-RATE * 3:], RATE, first),
                        psk.TRIAL_QUALITY)


if __name__ == "__main__":
    unittest.main()
