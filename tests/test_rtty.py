"""The RTTY decoder, driven with synthesised FSK.

Like the CW decoder, this prints letters whether it is right or wrong, and
on a real band you cannot tell a decoder fault from fading, QRM or a
station on the wrong shift. So it is a module that takes samples and returns
text, tested against audio whose content is known exactly: across the
passband, both polarities, down through the noise, drifting, beside a
carrier, through the shifts, and joining a transmission part-way through a
character.

SNR here is the ratio of the signal's power to the noise's power in a 2.5
kHz bandwidth, which is how decoders are usually compared.

Run with:  python -m unittest discover -s tests
"""
import sys
import unittest
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "plugins" / "decoder"))

# numpy belongs to the decoder card, not to the host, and CI installs only
# what the host needs - the same reason test_cw.py skips without it.
try:
    import numpy as np
    import rtty                                         # noqa: E402
    HAVE_NUMPY = True
except ImportError:                                     # pragma: no cover
    HAVE_NUMPY = False

RATE = 48000
MSG = "RYRYRY CQ CQ DE VA7ABC VA7ABC K"


def setUpModule():
    if not HAVE_NUMPY:
        raise unittest.SkipTest("numpy is not installed; the decoder needs it")


def decode(audio, samplerate=RATE, block=2048, **kw):
    """Fed in blocks the size the card's sound card hands over."""
    d = rtty.RttyDecoder(samplerate, **kw)
    out = [d.feed(audio[i:i + block]) for i in range(0, audio.size, block)]
    out.append(d.flush())
    return "".join(out), d


def with_snr(audio, snr_db, amplitude=0.5, seed=7, bandwidth=2500.0,
             samplerate=RATE):
    """White noise at `snr_db` relative to the tone, measured in 2.5 kHz.

    White noise of variance s^2 puts s^2 * 2B/fs of its power in a band B,
    and a tone of amplitude A has power A^2/2.
    """
    signal = amplitude ** 2 / 2
    sigma = np.sqrt(signal / 10 ** (snr_db / 10) * samplerate
                    / (2 * bandwidth))
    rng = np.random.default_rng(seed)
    return (audio + sigma * rng.standard_normal(audio.size)
            ).astype(np.float32)


def tone(seconds_or_like, hz, amplitude=0.5, samplerate=RATE):
    n = (seconds_or_like.size if hasattr(seconds_or_like, "size")
         else int(samplerate * seconds_or_like))
    t = np.arange(n) / samplerate
    return (amplitude * np.sin(2 * np.pi * hz * t)).astype(np.float32)


class TestAnywhereInThePassband(unittest.TestCase):
    def test_clean_signals_at_several_offsets(self):
        """Nobody tunes RTTY to the same audio offset: 2125/2295 is the
        old standard, and a receiver in USB with the dial set by eye puts
        the pair anywhere. Both tones have to be inside 250-2700 Hz."""
        for mark in (520, 915, 1445, 2125, 2295, 2650):
            with self.subTest(mark=mark):
                text, d = decode(rtty.rtty_audio(MSG, mark_hz=mark))
                self.assertEqual(text, MSG)
                self.assertAlmostEqual(d.mark_hz, mark, delta=4)
                self.assertAlmostEqual(d.space_hz, mark - 170, delta=4)

    def test_a_sound_card_that_is_not_48k(self):
        for rate in (8000, 11025, 22050, 44100):
            with self.subTest(rate=rate):
                audio = rtty.rtty_audio(MSG, samplerate=rate)
                text, _ = decode(audio, samplerate=rate)
                self.assertEqual(text, MSG)

    def test_it_does_not_care_how_the_audio_is_chopped_up(self):
        audio = rtty.rtty_audio(MSG, mark_hz=1700)
        for block in (997, 4096, audio.size):
            with self.subTest(block=block):
                self.assertEqual(decode(audio, block=block)[0], MSG)

    def test_the_level_does_not_matter(self):
        """Sound card gain varies by tens of dB between machines. The
        discriminator is (mark - space) / (mark + space), the same at any
        level."""
        for amp in (0.9, 0.05, 0.002):
            with self.subTest(amplitude=amp):
                audio = rtty.rtty_audio(MSG, amplitude=amp)
                self.assertEqual(decode(audio)[0], MSG)


class TestPolarity(unittest.TestCase):
    def test_reversed(self):
        """Mark is the higher RF frequency, so in LSB it is the *lower*
        audio tone. The operator says which sideband; the decoder is told."""
        audio = rtty.rtty_audio(MSG, mark_hz=1600, reverse=True)
        text, d = decode(audio, reverse=True)
        self.assertEqual(text, MSG)
        self.assertLess(d.mark_hz, d.space_hz)

    def test_the_wrong_polarity_does_not_read(self):
        """And it matters: read the wrong way up, mark is the start bit and
        nothing frames. Mostly nothing is printed, which is the honest
        answer - and why the card names Reverse when it hears a signal it
        cannot read."""
        audio = rtty.rtty_audio(MSG, mark_hz=1600, reverse=True)
        text, d = decode(audio, reverse=False)
        self.assertNotIn("CQ", text)
        self.assertLess(len(text), 5, text)
        self.assertTrue(d.hearing, "the tracker should still find the pair")


class TestNoise(unittest.TestCase):
    def test_readable_signals_are_read(self):
        for snr in (10, 0, -3):
            for seed in (1, 2, 3):
                with self.subTest(snr=snr, seed=seed):
                    audio = with_snr(rtty.rtty_audio(MSG, mark_hz=1500,
                                                     lead_seconds=2),
                                     snr, seed=seed)
                    self.assertEqual(decode(audio)[0], MSG)

    def test_minus_six_db_mostly_copies(self):
        """-6 dB in 2.5 kHz is 11 dB per bit: the over comes through with
        a character wrong or missing now and then, which is a weak signal
        rather than a broken decoder. A character there scores about the
        same as noise on its own, which is why each one is judged with its
        neighbours - judged alone, half of them were dropped."""
        for seed in (1, 2, 3):
            with self.subTest(seed=seed):
                audio = with_snr(rtty.rtty_audio(MSG, mark_hz=1500,
                                                 lead_seconds=2), -6,
                                 seed=seed)
                text, _ = decode(audio)
                self.assertIn("VA7ABC VA7ABC K", text)
                self.assertGreater(SequenceMatcher(None, text, MSG).ratio(),
                                   0.93, text)

    def test_noise_alone_prints_nothing(self):
        """The worse failure: a screen of letters off a dead band."""
        rng = np.random.default_rng(5)
        for sigma in (0.005, 0.05, 0.3):
            with self.subTest(sigma=sigma):
                noise = (sigma * rng.standard_normal(RATE * 6)).astype(
                    np.float32)
                text, d = decode(noise)
                self.assertEqual(text, "")
                self.assertFalse(d.hearing)

    def test_silence_prints_nothing(self):
        text, d = decode(np.zeros(RATE * 3, dtype=np.float32))
        self.assertEqual(text, "")
        self.assertFalse(d.hearing)

    def test_the_noise_after_an_over_prints_nothing(self):
        """Once locked the decoder keeps reading, so the noise between
        overs goes through the UART too - and passes the start and stop
        bit checks about one time in eight. A minute of it after the
        signal stops must add nothing."""
        for seed in (0, 1):
            with self.subTest(seed=seed):
                sig = rtty.rtty_audio(MSG, mark_hz=1500)
                rng = np.random.default_rng(seed)
                audio = np.concatenate((
                    sig + 0.05 * rng.standard_normal(sig.size),
                    0.05 * rng.standard_normal(RATE * 60))).astype(np.float32)
                text, d = decode(audio, block=4096)
                self.assertEqual(text, MSG)
                self.assertFalse(d.hearing)


class TestDrift(unittest.TestCase):
    def test_it_follows_a_drifting_signal(self):
        """An old rig warming up, or an unlocked VFO: 5 Hz a second is about
        90 Hz over this 19 s message, twice the matched filter's
        width, so a decoder that stayed where it locked would lose it."""
        msg = " ".join([MSG] * 3)
        for rate in (5.0, -5.0):
            with self.subTest(hz_per_s=rate):
                audio = rtty.rtty_audio(msg, mark_hz=1400,
                                        drift_hz_per_s=rate)
                text, d = decode(audio)
                self.assertEqual(text, msg)
                seconds = audio.size / RATE
                self.assertAlmostEqual(d.mark_hz, 1400 + rate * seconds,
                                       delta=25)

    def test_drift_with_noise(self):
        msg = " ".join([MSG] * 2)
        audio = with_snr(rtty.rtty_audio(msg, mark_hz=1200,
                                         drift_hz_per_s=4.0), 0)
        self.assertEqual(decode(audio)[0], msg)


class TestCarrier(unittest.TestCase):
    def test_a_louder_carrier_is_not_locked_onto(self):
        """A tuning carrier or a birdie, louder than the RTTY. It is the
        loudest thing in the passband and it never keys."""
        for carrier_hz in (450, 1800, 2500):
            with self.subTest(carrier=carrier_hz):
                sig = rtty.rtty_audio(MSG, mark_hz=1000, amplitude=0.2)
                audio = sig + tone(sig, carrier_hz, amplitude=0.6)
                text, d = decode(audio)
                self.assertEqual(text, MSG)
                self.assertAlmostEqual(d.mark_hz, 1000, delta=4)

    def test_a_carrier_with_noise(self):
        sig = rtty.rtty_audio(MSG, mark_hz=2200, amplitude=0.3,
                              lead_seconds=2)
        audio = with_snr(sig + tone(sig, 900, amplitude=0.6), 3,
                         amplitude=0.3)
        text, d = decode(audio)
        self.assertEqual(text, MSG)
        self.assertAlmostEqual(d.mark_hz, 2200, delta=4)

    def test_a_carrier_alone_prints_nothing(self):
        text, d = decode(tone(4.0, 1500))
        self.assertEqual(text, "")
        self.assertFalse(d.hearing)


class TestTheNaiveTrackerFails(unittest.TestCase):
    """The mutation check: a loudest-pair tracker, built from the decoder's
    own scores, run on the audio the carrier test passes with.

    If this ever stops failing, the carrier test above has stopped testing
    anything - the carrier would have to be quieter than the signal for
    the decoder to be choosing by keying rather than by level.
    """

    def test_the_loudest_pair_is_the_carrier(self):
        sig = rtty.rtty_audio(MSG, mark_hz=1000, amplitude=0.2)
        audio = sig + tone(sig, 1800, amplitude=0.6)
        d = rtty.RttyDecoder(RATE)
        d.feed(audio)
        rows = d._ring.shape[0]
        hist = np.roll(d._ring, -(d._ring_n % rows), axis=0)
        corr, strength = rtty.pair_scores(hist, d._shift_bins)

        def pair_hz(k):
            low = (k + d._lo) * d._hz_bin
            return low, low + 170

        loudest = pair_hz(int(np.argmax(strength)))
        self.assertTrue(any(abs(f - 1800) < 30 for f in loudest),
                        f"the loudest pair was {loudest}, not the carrier - "
                        "this check no longer shows anything")
        keyed = np.where(corr <= rtty.LOCK_CORR, strength, -np.inf)
        chosen = pair_hz(int(np.argmax(keyed)))
        self.assertAlmostEqual(chosen[0], 830, delta=15)


class TestShifts(unittest.TestCase):
    def test_figures_and_letters(self):
        msg = "UR RST 599 599 QTH 49.2N/123.1W ? (BC) TU 73"
        self.assertEqual(decode(rtty.rtty_audio(msg))[0], msg)

    def test_unshift_on_space(self):
        """A transmitter that assumes the far end unshifts on space sends
        no LTRS after one: "599 OK" is FIGS 5 9 9, space, O K. A receiver
        that does not unshift prints the figures under O and K - 9 and
        (. Each setting is right for the other end's habit."""
        codes = _codes("FIGS 5 9 9 SPACE O K", figs={"5", "9"})
        audio = rtty.rtty_audio(codes=codes)
        self.assertEqual(decode(audio, usos=True)[0], "599 OK")
        self.assertEqual(decode(audio, usos=False)[0], "599 9(")

    def test_a_transmitter_that_does_not_unshift(self):
        """The other habit: FIGS once, then figures either side of a
        space. A USOS receiver reads the second group as letters - T for
        5, O for 9 - which is the classic symptom of the setting being
        wrong for the station being read."""
        codes = _codes("FIGS 5 9 9 SPACE 5 9 9", figs={"5", "9"})
        audio = rtty.rtty_audio(codes=codes)
        self.assertEqual(decode(audio, usos=False)[0], "599 599")
        self.assertEqual(decode(audio, usos=True)[0], "599 TOO")

    def test_encode_resends_figs_after_a_space_for_a_usos_receiver(self):
        self.assertEqual(rtty.encode("5 5", usos=True).count(rtty.FIGS), 2)
        self.assertEqual(rtty.encode("5 5", usos=False).count(rtty.FIGS), 1)

    def test_a_new_line(self):
        text, _ = decode(rtty.rtty_audio("CQ CQ\nDE VA7ABC"))
        self.assertEqual(text, "CQ CQ\nDE VA7ABC")


class TestJoiningPartWay(unittest.TestCase):
    def test_starting_mid_character(self):
        """Audio that begins inside a character, with no idle in front of
        it - tuning onto a station in the middle of an over. The UART
        slides past edges that do not frame, so it is in step within a
        few characters, and the characters before that are lost rather
        than printed wrong: what comes out is the end of the message."""
        full = rtty.rtty_audio(MSG, mark_hz=1400, lead_seconds=0,
                               idle_seconds=0)
        bit = RATE / rtty.BAUD
        for cut in (8.3, 11.7, 15.2, 22.35, 24.3, 29.0):
            with self.subTest(bits_in=cut):
                audio = full[int(cut * bit):]
                rng = np.random.default_rng(int(cut * 10))
                audio = (audio + 0.02 * rng.standard_normal(audio.size)
                         ).astype(np.float32)
                text, _ = decode(audio)
                self.assertTrue(MSG.endswith(text), repr(text))
                self.assertGreaterEqual(len(text), len(MSG) - 5, repr(text))

    def test_the_stop_bit_length_is_what_keeps_it_in_step(self):
        """Why the whole 1.5-bit stop is checked, and the 1.5 bits before
        every start. Any edge has a bit of mark before it, so one bit's
        worth says nothing; the extra half bit is what a data transition
        usually lacks. Swept over 40 cut points into this message, reading
        as if the stop were one bit framed on data edges once - here,
        inside RYRY - and the full check never did."""
        full = rtty.rtty_audio(MSG, mark_hz=1400, lead_seconds=0,
                               idle_seconds=0)
        audio = full[int(22.35 * RATE / rtty.BAUD):]
        rng = np.random.default_rng(27)
        audio = (audio + 0.02 * rng.standard_normal(audio.size)
                 ).astype(np.float32)
        strict, _ = decode(audio)
        loose, _ = decode(audio, stop_bits=1.0)
        self.assertTrue(MSG.endswith(strict), strict)
        self.assertFalse(MSG.endswith(loose),
                         f"the looser check read {loose!r} correctly, so "
                         "this no longer shows what the full one is for")

    def test_a_second_station_elsewhere(self):
        """One over, then another station on another frequency, then the
        first again. Moving to a new pair replays the audio ring - but not
        from before the last character printed, or the end of the first
        over would come out twice."""
        audio = np.concatenate((
            rtty.rtty_audio("CQ DE VA7ABC K", mark_hz=1000),
            np.zeros(RATE, dtype=np.float32),
            rtty.rtty_audio("VA7ABC DE VE7XYZ K", mark_hz=2000),
            rtty.rtty_audio("TU 73", mark_hz=1000)))
        audio = with_snr(audio, 6)
        text, _ = decode(audio)
        self.assertEqual(text, "CQ DE VA7ABC KVA7ABC DE VE7XYZ KTU 73")


class TestTheReadouts(unittest.TestCase):
    def test_they_are_plain_numbers(self):
        """The card hands these straight to a JSON reply, and numpy's float32
        is not JSON: the first version's mark frequency made every poll of
        an RTTY tile a 500."""
        _, d = decode(rtty.rtty_audio(MSG))
        for name in ("mark_hz", "space_hz", "tone_hz", "snr_db", "baud"):
            with self.subTest(name=name):
                self.assertIs(type(getattr(d, name)), float)
        self.assertIs(type(d.hearing), bool)
        self.assertIs(type(d.reverse), bool)


class TestTheTable(unittest.TestCase):
    def test_every_letter_and_figure_round_trips(self):
        for ch in list(rtty.ENC_LETTERS) + list(rtty.ENC_FIGURES):
            with self.subTest(ch=ch):
                code = (rtty.ENC_LETTERS.get(ch)
                        if ch in rtty.ENC_LETTERS else rtty.ENC_FIGURES[ch])
                table = (rtty.LETTERS if ch in rtty.ENC_LETTERS
                         else rtty.FIGURES)
                self.assertEqual(table[code], ch)

    def test_spot_checks_against_the_table_everyone_knows(self):
        # ITA2 bits 1-5, first sent least significant: A is 11000, E 10000.
        self.assertEqual(rtty.LETTERS[0b00011], "A")
        self.assertEqual(rtty.LETTERS[0b00001], "E")
        self.assertEqual(rtty.LETTERS[0b10000], "T")
        self.assertEqual(rtty.FIGURES[0b10000], "5")
        self.assertEqual(rtty.LTRS, 0b11111)
        self.assertEqual(rtty.FIGS, 0b11011)


def _codes(spec: str, figs: set) -> list:
    """Hand-built code sequences, for transmitter habits encode() avoids."""
    names = {"FIGS": rtty.FIGS, "LTRS": rtty.LTRS, "SPACE": rtty.SPACE}
    out = [rtty.LTRS]
    for word in spec.split():
        if word in names:
            out.append(names[word])
        elif word in figs:
            out.append(rtty.ENC_FIGURES[word])
        else:
            out.append(rtty.ENC_LETTERS[word])
    return out


def _in_order(text: str, msg: str) -> bool:
    """Every character of `text` appears in `msg`, in that order."""
    it = iter(msg)
    return all(ch in it for ch in text)


if __name__ == "__main__":
    unittest.main()
