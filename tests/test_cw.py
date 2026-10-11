"""The CW decoder, driven with synthesised Morse.

A decoder is the kind of thing that can be wrong in ways nobody notices: it
prints letters either way, and on a real band you cannot tell a decoder fault
from a fist, QRM or fading. So it is a module that takes samples and returns
text, and it is tested against audio whose content is known exactly - across
the speeds people actually send at, across tones, and down through the
signal-to-noise ratio until it stops.

Run with:  python -m unittest discover -s tests
"""
import sys
import unittest
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "plugins" / "decoder"))

# numpy belongs to the decoder card, not to the host, and CI installs only
# what the host needs - the same reason it installs no pywebview. Skipping
# says "not checked here" where an ImportError said "the suite is broken".
try:
    import numpy as np
    import cw                                           # noqa: E402
    HAVE_NUMPY = True
except ImportError:                                     # pragma: no cover
    HAVE_NUMPY = False

CQ = "CQ CQ DE VA7ABC K"

pytestmark = unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")


def setUpModule():
    if not HAVE_NUMPY:
        raise unittest.SkipTest("numpy is not installed; the decoder needs it")


def decode(audio, samplerate=48000):
    d = cw.CwDecoder(samplerate)
    text = d.feed(audio) + d.flush()
    return text.strip(), d


def noisy(audio, sigma, seed=11):
    rng = np.random.default_rng(seed)
    return audio + sigma * rng.standard_normal(audio.size).astype(np.float32)


class TestSpeeds(unittest.TestCase):
    """6 to 45 WPM. Below that nobody sends; above it the 8 ms analysis
    frame starts to quantise a 25 ms dot, which is documented rather than
    pretended about."""

    def test_the_speeds_people_send_at(self):
        for wpm in (6, 8, 10, 13, 15, 18, 20, 25, 30, 35, 40, 45):
            with self.subTest(wpm=wpm):
                text, d = decode(cw.keyed_audio(CQ, wpm=wpm))
                self.assertEqual(text, CQ)
                self.assertAlmostEqual(d.wpm, wpm, delta=max(2.0, wpm * 0.18))

    def test_the_opening_letters_survive_an_unexpected_speed(self):
        """The decoder starts out assuming 20 WPM. At 8 the gaps inside the
        first letter are longer than its idea of a letter gap, and at 40
        every dash is shorter than its idea of a dash - so the first letters
        are held until the speed has been measured. Without that, "CQ" comes
        out as "T R" or as "HH"."""
        for wpm in (8, 40):
            with self.subTest(wpm=wpm):
                text, _ = decode(cw.keyed_audio("CQ TEST", wpm=wpm))
                self.assertEqual(text, "CQ TEST")

    def test_speed_changes_part_way_through(self):
        """One operator slowing down for another, which is ordinary.

        The letters all arrive. The word spacing for the first few of them
        does not, because a gap is only long or short relative to a dot and
        the dot estimate takes a couple of characters to halve - the same
        couple of characters it takes a human. Asserted as it actually
        behaves rather than as one would like it to.
        """
        audio = np.concatenate([cw.keyed_audio("QRS PSE", wpm=28),
                                cw.keyed_audio("TNX OM", wpm=14)])
        text, d = decode(audio)
        self.assertEqual(text.replace(" ", ""), "QRSPSETNXOM")
        self.assertTrue(text.startswith("QRS PSE "), text)
        self.assertLess(d.wpm, 22, "the speed estimate never came down")


class TestTones(unittest.TestCase):
    def test_any_sidetone_in_the_passband(self):
        """Nobody knows their sidetone in hertz and the clarifier moves it,
        so it is found rather than configured."""
        for hz in (350, 500, 700, 900, 1200, 1400):
            with self.subTest(hz=hz):
                text, d = decode(cw.keyed_audio(CQ, wpm=20, tone_hz=hz))
                self.assertEqual(text, CQ)
                self.assertAlmostEqual(d.tone_hz, hz, delta=120)

    def test_a_second_tone_does_not_steal_the_tracker(self):
        """An SSB station or a second CW signal elsewhere in the passband.
        It is quieter, so the decoder should stay on the one it is reading."""
        audio = cw.keyed_audio(CQ, wpm=20, tone_hz=700)
        t = np.arange(audio.size, dtype=np.float32) / 48000
        audio = audio + (0.1 * np.sin(2 * np.pi * 1300 * t)).astype(np.float32)
        text, d = decode(audio)
        self.assertEqual(text, CQ)
        self.assertLess(abs(d.tone_hz - 700), 120)


class TestNoise(unittest.TestCase):
    def test_readable_signals_are_read(self):
        for sigma in (0.01, 0.05, 0.15, 0.3):
            for wpm in (12, 25):
                with self.subTest(sigma=sigma, wpm=wpm):
                    audio = noisy(cw.keyed_audio(CQ, wpm=wpm), sigma)
                    self.assertEqual(decode(audio)[0], CQ)

    def test_a_weak_signal_still_mostly_copies(self):
        """At the edge it degrades the way a human does - some letters
        wrong - rather than collapsing into nothing. Compared with a ratio
        rather than letter by letter, because one dropped character shifts
        everything after it and a shifted copy is still a copy."""
        text, _ = decode(noisy(cw.keyed_audio(CQ, wpm=20, amplitude=0.01),
                               0.02))
        like = SequenceMatcher(None, text, CQ).ratio()
        self.assertGreater(like, 0.5, f"copied {text!r}")
        self.assertIn("CQ", text)

    def test_noise_alone_prints_nothing(self):
        """The opposite failure, and the worse one: a decoder that fills the
        screen with letters off a dead band teaches you to ignore it."""
        rng = np.random.default_rng(5)
        for sigma in (0.005, 0.05, 0.2):
            with self.subTest(sigma=sigma):
                noise = (sigma * rng.standard_normal(48000 * 4)).astype(
                    np.float32)
                text, d = decode(noise)
                self.assertEqual(text, "")
                self.assertFalse(d.hearing)

    def test_silence_prints_nothing(self):
        text, d = decode(np.zeros(48000 * 2, dtype=np.float32))
        self.assertEqual(text, "")
        self.assertFalse(d.hearing)


class TestTheAwkwardParts(unittest.TestCase):
    def test_a_sound_card_that_is_not_48k(self):
        for rate in (8000, 16000, 22050, 44100):
            with self.subTest(rate=rate):
                audio = cw.keyed_audio(CQ, wpm=18, samplerate=rate)
                text, _ = decode(audio, samplerate=rate)
                self.assertEqual(text, CQ)

    def test_it_does_not_care_how_the_audio_is_chopped_up(self):
        """Capture hands over whatever the sound card had, in blocks that
        have nothing to do with element boundaries."""
        audio = cw.keyed_audio(CQ, wpm=22)
        d = cw.CwDecoder(48000)
        out = []
        for i in range(0, audio.size, 997):             # a deliberately odd size
            out.append(d.feed(audio[i:i + 997]))
        out.append(d.flush())
        self.assertEqual("".join(out).strip(), CQ)

    def test_a_level_a_hundredth_of_full_scale_is_the_same_text(self):
        """Sound card gain varies by tens of dB between a USB codec and
        whatever the mixer was left at, so a fixed threshold is wrong on
        nearly every machine."""
        for amp in (0.9, 0.3, 0.05, 0.01):
            with self.subTest(amplitude=amp):
                text, _ = decode(cw.keyed_audio(CQ, wpm=20, amplitude=amp))
                self.assertEqual(text, CQ)

    def test_numbers_and_punctuation(self):
        msg = "599 5NN TU 73 DE VE7ABC/P"
        text, _ = decode(cw.keyed_audio(msg, wpm=20))
        self.assertEqual(text, msg)

    def test_an_unknown_pattern_is_shown_not_swallowed(self):
        """A silent drop looks exactly like a quiet band. Printing what was
        heard is what tells the operator the speed estimate has gone wrong."""
        self.assertEqual(cw.MORSE.get("........"), "<HH>")
        d = cw.CwDecoder(48000)
        d.dot_ms = 60.0
        d._seen = 99                        # past the point of holding back
        d._pending = [(60.0, True)] * 9     # nine dots: not a letter
        self.assertEqual(d._drain(), "<.........>")

    def test_a_tuning_carrier_is_not_an_element(self):
        """Somebody holding the key down while they tune up is not a dash
        however long it is, and it says nothing about their speed."""
        audio = np.concatenate([
            cw.keyed_audio("E", wpm=20)[:0],            # nothing, for shape
            _carrier(seconds=3.0),
            cw.keyed_audio(CQ, wpm=20),
        ])
        text, _ = decode(audio)
        self.assertIn("VA7ABC", text)

    def test_the_last_letter_of_an_over_appears(self):
        """A letter is only finished by the silence after it, so without a
        flush the end of a transmission never arrives."""
        d = cw.CwDecoder(48000)
        first = d.feed(cw.keyed_audio("TEST", wpm=20))
        self.assertEqual((first + d.flush()).strip(), "TEST")


def _carrier(seconds: float, hz: float = 700.0, samplerate: int = 48000):
    t = np.arange(int(samplerate * seconds), dtype=np.float32) / samplerate
    return (0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


class TestTheMorseTable(unittest.TestCase):
    def test_every_letter_and_digit_round_trips(self):
        for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
            with self.subTest(ch=ch):
                pattern = cw.morse_for(ch)
                self.assertEqual(len(pattern), 1)
                self.assertEqual(cw.MORSE[pattern[0]], ch)

    def test_the_two_tables_cannot_disagree(self):
        """PATTERNS is built from MORSE for exactly this reason."""
        for pattern, text in cw.MORSE.items():
            with self.subTest(text=text):
                self.assertEqual(cw.PATTERNS[text], pattern)

    def test_a_few_spot_checks_against_the_table_everyone_knows(self):
        self.assertEqual(cw.MORSE[".-"], "A")
        self.assertEqual(cw.PATTERNS["?"], "..--..")
        self.assertEqual(cw.PATTERNS["/"], "-..-.")
        self.assertEqual(cw.PATTERNS["0"], "-----")


if __name__ == "__main__":
    unittest.main()
