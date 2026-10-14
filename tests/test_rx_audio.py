"""The receive-audio card: the DSP, and the ring it serves.

`dsp.py` imports nothing from the card, like `decoder/cw.py`, so it can be
driven with synthesised tones at known frequencies. That matters here for
the same reason it matters there: a decimator that is folding the hiss
above its cut back into the passband does not sound broken, it sounds like
a noisier radio, and no amount of listening will tell you which it is.

Run with:  python -m unittest discover -s tests
"""
import importlib.util
import unittest
from pathlib import Path

CARD = Path(__file__).resolve().parent.parent / "plugins" / "rx_audio"

# numpy belongs to this card, not to the host, and CI installs only what
# the host needs - the same reason `test_cw.py` guards its import. Only
# the DSP half is skipped: the tests that read the card's source are about
# which route may open the sound card, and they must keep running
# everywhere, because that is the half that was actually wrong.
try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:                                     # pragma: no cover
    HAVE_NUMPY = False

needs_numpy = unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")


def _load(name):
    """By path under a name of its own: every plugins/*/card.py shares the
    module name `card`, so a plain import gets whichever test ran first."""
    spec = importlib.util.spec_from_file_location(
        f"rx_audio_{name}", CARD / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dsp = _load("dsp") if HAVE_NUMPY else None


def tone(hz, rate, secs=0.5):
    t = np.arange(int(rate * secs), dtype=np.float32) / rate
    return np.sin(2 * np.pi * hz * t).astype(np.float32)


def power_at(sig, hz, rate):
    """How much of `sig` is at `hz`, by correlating with that frequency."""
    t = np.arange(len(sig), dtype=np.float64) / rate
    i = np.dot(sig, np.cos(2 * np.pi * hz * t)) / len(sig)
    q = np.dot(sig, np.sin(2 * np.pi * hz * t)) / len(sig)
    return float(np.hypot(i, q))


def db(x, ref):
    return 20 * np.log10(max(x, 1e-12) / max(ref, 1e-12))


@needs_numpy
class TestItDoesNotFold(unittest.TestCase):
    """The one thing a decimator must get right."""

    def setUp(self):
        self.rate = 48000
        self.d = dsp.Decimator(self.rate, 12000)
        self.assertEqual(self.d.factor, 4)
        self.assertEqual(self.d.out_rate, 12000)

    def run_through(self, sig, block=960):
        out = []
        for i in range(0, len(sig) - block, block):
            out.append(self.d(sig[i:i + block]))
        return np.concatenate(out)

    def test_audio_in_the_passband_survives(self):
        """1 kHz is where a CW note or a voice lives."""
        out = self.run_through(tone(1000, self.rate))
        kept = power_at(out, 1000, self.d.out_rate)
        self.assertGreater(db(kept, 0.5), -2.0,
                           "the passband is being attenuated")

    def test_a_tone_above_the_new_nyquist_does_not_come_back(self):
        """9 kHz decimated to 12 kHz would mirror to 3 kHz - right in the
        middle of the speech band, indistinguishable from a real signal.
        A box average leaves it about 13 dB down, which is plainly
        audible."""
        out = self.run_through(tone(9000, self.rate))
        folded = power_at(out, 3000, self.d.out_rate)
        self.assertLess(db(folded, 0.5), -50.0,
                        f"9 kHz folded back to 3 kHz at "
                        f"{db(folded, 0.5):.1f} dB")

    def test_the_box_average_the_spike_used_really_does_fold(self):
        """Proves the test above can fail - otherwise it is only asserting
        that something happened."""
        sig = tone(9000, self.rate)
        n = (len(sig) // 4) * 4
        boxed = sig[:n].reshape(-1, 4).mean(axis=1)
        folded = power_at(boxed, 3000, 12000)
        self.assertGreater(db(folded, 0.5), -20.0,
                           "the naive version should fold badly; if it no "
                           "longer does, this test has stopped testing")

    def test_a_tone_just_above_the_edge_does_not_come_back_either(self):
        """7 kHz mirrors to 5 kHz. The first filter here passed the 9 kHz
        case and failed this one, because its stopband did not begin
        until 6.7 kHz - above the 6 kHz Nyquist, which is the whole thing
        the cutoff and the tap count have to agree about."""
        out = self.run_through(tone(7000, self.rate))
        folded = power_at(out, 5000, self.d.out_rate)
        self.assertLess(db(folded, 0.5), -50.0,
                        f"7 kHz folded back to 5 kHz at "
                        f"{db(folded, 0.5):.1f} dB")

    def test_the_whole_speech_band_is_flat(self):
        """A receiver's audio is about 3 kHz wide. Rolling off inside it
        to buy a cheaper filter would be audible as a dull radio."""
        for hz in (300, 1000, 2000, 3000):
            with self.subTest(hz=hz):
                out = self.run_through(tone(hz, self.rate))
                kept = db(power_at(out, hz, self.d.out_rate), 0.5)
                self.assertGreater(kept, -1.0, f"{hz} Hz down {kept:.1f} dB")


@needs_numpy
class TestItDoesNotClick(unittest.TestCase):
    def test_the_filter_carries_its_tail_between_blocks(self):
        """Filtering each block from zeros clicks every boundary - at 50
        blocks a second that is a 50 Hz buzz over the audio."""
        rate = 48000
        sig = tone(1000, rate, 1.0)
        d = dsp.Decimator(rate, 12000)
        blocked = np.concatenate([d(sig[i:i + 960])
                                  for i in range(0, len(sig) - 960, 960)])
        whole = dsp.Decimator(rate, 12000)(sig)[:len(blocked)]
        # Same signal either way, bar the filter's start-up transient.
        self.assertLess(float(np.abs(blocked[200:] - whole[200:]).max()),
                        1e-3, "block boundaries are not continuous")

    def test_the_output_rate_does_not_wobble(self):
        """The decimation phase has to carry over too, or blocks come out
        a sample long or short and the player drifts.

        The first block is genuinely short - the filter has no history
        yet, so it is still filling - and that is start-up, not wobble.
        What must not vary is the steady state.
        """
        d = dsp.Decimator(48000, 12000)
        lens = [len(d(np.zeros(960, dtype=np.float32))) for _ in range(50)]
        self.assertEqual(set(lens[2:]), {240},
                         f"steady-state block lengths varied: "
                         f"{sorted(set(lens[2:]))}")

    def test_it_produces_exactly_real_time_in_the_long_run(self):
        """A rate that is a hair off is a player that drifts and resyncs
        every few minutes - which presents as an occasional glitch with
        nothing in the logs."""
        d = dsp.Decimator(48000, 12000)
        total = sum(len(d(np.zeros(960, dtype=np.float32)))
                    for _ in range(500))
        expected = 500 * 240
        self.assertLess(abs(total - expected), 240,
                        f"{total} samples out for {expected} expected")


@needs_numpy
class TestRatesItCannotDivideEvenly(unittest.TestCase):
    def test_44100_gets_a_whole_number_factor(self):
        """Resampling to hit a round number would cost quality and code.
        The card reports what it actually produces instead."""
        d = dsp.Decimator(44100, 12000)
        self.assertEqual(d.factor, 3)
        self.assertEqual(d.out_rate, 14700)

    def test_it_never_upsamples(self):
        d = dsp.Decimator(8000, 12000)
        self.assertEqual(d.factor, 1)
        self.assertEqual(d.out_rate, 8000)

    def test_passthrough_is_untouched(self):
        d = dsp.Decimator(8000, 12000)
        sig = tone(1000, 8000, 0.1)
        self.assertTrue(np.array_equal(d(sig), sig))


class TestAskingIsNotWanting(unittest.TestCase):
    """The status poll must not hold the radio's sound card.

    Found by pressing Stop and watching the input come straight back: the
    page polls for a status line whether or not anybody is listening, that
    poll went through the same "open it if it is not open" helper as the
    audio itself, and so the card took the sound card back about two
    seconds after handing it over. Nothing looks wrong - the tile says
    what it should and the audio stops - but the only reason to press
    Stop is to give the device to another program, and it had not been
    given.

    The card is not imported here (it calls card.run(), which blocks and
    binds a port), so this reads the source: /info must use the helper
    that does not create, and /pcm must use the one that does.
    """

    SRC = (CARD / "card.py").read_text(encoding="utf-8")

    def route(self, name):
        i = self.SRC.index(f'@card.get("/{name}")')
        rest = self.SRC[i + 1:]
        j = rest.find("@card.")
        return rest[:j if j >= 0 else len(rest)]

    def test_the_status_route_does_not_open_the_input(self):
        body = self.route("info")
        self.assertNotIn("tap_for(", body,
                         "/info opens the input again, so Stop only lets "
                         "go of the sound card until the next status poll")
        self.assertIn("look(", body)

    def test_the_audio_route_does_open_it(self):
        self.assertIn("tap_for(", self.route("pcm"))

    def test_look_really_does_not_create(self):
        src = self.SRC[self.SRC.index("def look("):]
        src = src[:src.index("def tap_for(")]
        self.assertNotIn("Tap(", src)

    def test_stop_releases_rather_than_waiting_for_the_reaper(self):
        """Twenty seconds is not "now", and now is what Stop means."""
        self.assertIn("def release(", self.SRC)
        self.assertIn("release(card.tile_uid())", self.SRC)

    def test_wants_are_counted_per_tile(self):
        """Two tiles can listen to one radio; one stopping must not take
        the audio away from the other."""
        src = self.SRC[self.SRC.index("def release("):]
        src = src[:src.index("def _reap_idle")]
        self.assertIn("if tiles.pop(uid, None) is not None and not tiles:",
                      src)

if __name__ == "__main__":
    unittest.main()


class TestItSaysWhyThereIsNoSound(unittest.TestCase):
    """"Listening." is a lie when nothing reaches the speakers.

    Reported as "the button works but I get no audio" - and the card
    went on saying it was listening while the radio, the card and the
    browser were all measurably fine: peak 0.07 at the capture, seq
    advancing, 51 buffers scheduled in three seconds. The silence was at
    the operator's end, and the card had everything it needed to say so
    and said nothing.

    None of the three is a failure. Each is a thing to put right in a
    second, once you are told which it is. Verified live in a browser:
    zeroing the slider and suspending the context each produce their own
    line, and each reverts to "Listening." when undone.
    """

    def setUp(self):
        self.page = (CARD / "index.html").read_text(encoding="utf-8")

    def test_the_status_is_repainted_while_playing(self):
        self.assertIn("sayWhy()", self.page,
                      "nothing re-examines the status once it says Listening")

    def test_a_suspended_context_is_named(self):
        self.assertIn("ctx.state !== 'running'", self.page)
        self.assertIn("paused audio", self.page)

    def test_a_zeroed_volume_is_named(self):
        # Stored per browser, so reloading the page - the first thing
        # anybody tries - cannot clear it.
        self.assertIn("gain.gain.value === 0", self.page)
        self.assertIn("volume here is at zero", self.page)

    def test_silence_on_the_wire_is_named(self):
        self.assertIn("peakWindow === 0", self.page)

    def test_the_peak_is_a_window_not_one_block(self):
        # A single block can be silent between syllables; the claim is
        # about the half-second the status line covers.
        self.assertIn("peakWindow = Math.max(peakWindow, peak)", self.page)
        self.assertIn("peakWindow = 0", self.page)
