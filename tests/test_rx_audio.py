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
# The player, which moved out of this card's page into one shared script
# mounted at the foot of the rig card.
LISTEN = CARD.parent.parent / "hamdash" / "static" / "js" / "listen.js"

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
        self.assertIn("release(who())", self.SRC)

    def test_wants_are_counted_per_listener(self):
        """One listener stopping must not take the audio from another.

        This said *per tile* and asserted it, which was the bug rather
        than the guard: every browser showing a tile sends the same uid,
        so two of them were one entry and the first Stop closed the
        device for both. `tests/test_rx_listeners.py` holds the rule now
        and drives it rather than grepping for it.
        """
        src = self.SRC[self.SRC.index("def release("):]
        src = src[:src.index("def _reap_idle")]
        self.assertIn("tiles.pop(who, None) is not None and not tiles", src)



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
        self.page = LISTEN.read_text(encoding="utf-8")

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


class TestBeingHiddenIsNotAReasonToStop(unittest.TestCase):
    """Audio is heard, not seen.

    This card used to `stop()` the moment its tile went to the
    background, on the invariant-5b reasoning that a hidden card holding
    the radio's sound card has nobody listening. That is right for the
    spectrum and the decoder - nobody reads a waterfall they cannot see -
    and backwards for this one: listening *while looking at something
    else* is the whole of what it is for.

    The phone shell is where it showed, because there every card except
    the one on screen is hidden. Reported as: press Listen, go to the rig
    card to change frequency, and the audio stops. It was just as wrong
    on the board, where it silenced a stacked tile the moment you looked
    at one of its neighbours.

    Verified live against a real USB input, phone shell, audio playing:
    switching to the rig card left the device open with its sequence
    advancing 202 blocks in 4 s - the full ~50/s - peak 0.16, the page
    still fetching `/api/pcm` at the head, and no `last listener left` in
    the card's log. Stop then closed it with exactly one.
    """

    def setUp(self):
        self.page = LISTEN.read_text(encoding="utf-8")

    def test_it_does_not_stop_itself_when_hidden(self):
        # Matched against the handler alone rather than the whole page,
        # so a failure names the line instead of printing the card.
        bad = [ln.strip() for ln in self.page.splitlines()
               if "onVisible(" in ln and "stop(" in ln]
        self.assertEqual(bad, [],
                         "hiding the tile stops the audio, so on a phone - "
                         "where every other card is hidden - you cannot "
                         "listen and do anything else at the same time")

    def test_a_page_that_is_going_away_still_stops(self):
        """It cannot press its own Stop button once it is gone, and the
        reaper is twenty seconds away."""
        self.assertRegex(self.page,
                         r"pagehide[^\n]*\n?[^\n]*stop\(true\)")

    def test_stop_is_still_what_hands_the_device_back(self):
        self.assertIn("$('stop').onclick", self.page)
        self.assertRegex(self.page, r"/api/stop\?who=\$\{WHO\}")


class TestTheDeviceIsActuallyGivenBack(unittest.TestCase):
    """Reported as "something holding listen open again, seems broken".

    Measured at the time: a standalone process could open the USB input
    on MME, DirectSound and WASAPI, so the device was free as far as
    Windows was concerned - but the card answered
    `DSERR_ALLOCATED`, and restarting that one card process fixed it.
    The holder was the card itself: a stream it had lost the handle to.

    `release()` pops the Tap out of `_taps` before stopping it, so a
    stream that is not closed on the way out can never be closed by
    anything again - it is held for the life of the process, and every
    later open fails with the message that blames WSJT-X. Three ways in,
    all of them shut here:

      * `stream.start()` raising, with the stream already constructed
        and the `finally` that closes it not yet entered;
      * `stream.stop()` or `.close()` raising, propagating to the outer
        handler, which calls `_fail()` and never retries the close;
      * `stop()` returning before the thread had let go, so the next
        open raced the close.

    The race could not be made to bite in twelve tight attempts - the
    PortAudio lock serialises it well - so the join is correctness
    rather than a reproduced fault, and is written down as such.
    """

    SRC = (CARD / "card.py").read_text(encoding="utf-8")

    def body(self):
        i = self.SRC.index("    def _run(")
        return self.SRC[i:self.SRC.index("    def _fail(")]

    def test_the_stream_is_bound_before_the_try_that_closes_it(self):
        body = self.body()
        self.assertIn("stream = None", body,
                      "a stream that fails to start is not covered by the "
                      "close, so it holds the device with nothing left to "
                      "close it")
        self.assertLess(body.index("stream = None"),
                        body.index("sd.InputStream("))

    def test_closing_cannot_fail_its_way_out_of_closing(self):
        body = self.body()
        tail = body[body.index("finally:"):]
        self.assertIn("for shut in (stream.stop, stream.close)", tail)
        self.assertIn("except Exception", tail,
                      "a raise from stop() skips close() and leaks the "
                      "device for the life of the process")

    def test_stopping_waits_for_the_thread_to_let_go(self):
        i = self.SRC.index("    def stop(self")
        body = self.SRC[i:i + 1600]
        self.assertIn("self._thread.join(", body,
                      "release() returns before the device is closed, so "
                      "an immediate re-open races it")
        self.assertIn("threading.current_thread() is not self._thread", body,
                      "joining from the capture thread is a deadlock")

    def test_the_wait_is_bounded(self):
        """It blocks a request thread (invariant 4), so it cannot be
        open-ended however badly PortAudio is behaving."""
        self.assertRegex(self.SRC, r"CLOSE_WAIT = \d")
        i = self.SRC.index("    def stop(self")
        self.assertIn("wait: float = CLOSE_WAIT", self.SRC[i:i + 60])


class TestTheOtherAudioCardsStillLetGo(unittest.TestCase):
    """The distinction is deliberate, so it is written down as a test.

    Copying the rule above across to them is the easy mistake, and it
    was offered and turned down. The operator's reason, which beats the
    one reasoned from here: **you would not be scanning and decoding at
    the same time.** Listening is what you do while doing something
    else; reading decoded text is the thing you are doing. So a decoder
    nobody is looking at has no job, and holding the radio's input
    against WSJT-X or fldigi for the length of a board session to do it
    is the wrong trade.

    The spectrum is the same: a waterfall you cannot see is not being
    watched.
    """

    def test_the_decoder_releases_its_input_when_hidden(self):
        page = (CARD.parent / "decoder" / "index.html").read_text(encoding="utf-8")
        i = page.index("HD.onVisible(")
        self.assertIn("/api/stop", page[i:i + 400],
                      "a decoder parked behind a tab holds the sound card")

    def test_the_spectrum_stops_asking_when_hidden(self):
        page = (CARD.parent / "spectrum" / "index.html").read_text(encoding="utf-8")
        self.assertIn("HD.hidden", page,
                      "a hidden waterfall keeps polling for frames nobody "
                      "is looking at, and its want keeps the device open")


if __name__ == "__main__":
    unittest.main()
