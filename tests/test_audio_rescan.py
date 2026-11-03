"""A sound card that comes back is found again without restarting anything.

PortAudio reads the device list once, when it starts. On this station the
radio's USB input was dropped by PipeWire whenever another program (an SDR
application set to the card's raw hardware device) opened it, and after
`systemctl --user restart wireplumber` brought it back the board still
could not see it: every retry searched the list PortAudio had read at
start, and Rescan in Settings rescanned the same old list. Only restarting
hamdash helped.

Now the shared capture looks again with a fresh PortAudio when a name is
missing - only when no stream is open in that process, because starting
PortAudio again invalidates every stream it has - and the host's Rescan
starts PortAudio again before it lists.

Run with:  python -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent / "plugins" / "rx_audio"
sys.path.insert(0, str(HERE))

try:
    import numpy                                        # noqa: F401
    import capture                                      # noqa: E402
except ImportError:                                     # pragma: no cover
    # CI's host-only job runs without the audio cards' packages, on
    # purpose. The host's half below needs none of them.
    capture = None

from hamdash.services import audio                      # noqa: E402

needs_capture = unittest.skipIf(capture is None, "numpy is not installed")

USB = {"name": "alsa_input.usb-GeneralPlus_USB_Audio_Device-00.mono-fallback",
       "max_input_channels": 1, "max_output_channels": 0, "hostapi": 0,
       "default_samplerate": 48000.0}
BUILTIN = {"name": "Built-in Audio Analog Stereo", "max_input_channels": 2,
           "max_output_channels": 0, "hostapi": 0,
           "default_samplerate": 48000.0}


class FakeSd:
    """PortAudio as sounddevice presents it: a list read at start, and only
    re-read by terminating and initialising again."""

    def __init__(self, at_start, after_restart):
        self.listed = list(at_start)
        self.after_restart = list(after_restart)
        self.restarts = 0

    def query_devices(self, index=None):
        return self.listed if index is None else self.listed[index]

    def query_hostapis(self):
        return [{"name": "PulseAudio"}]

    class default:
        device = [0, 0]

    def _terminate(self):
        pass

    def _initialize(self):
        self.restarts += 1
        self.listed = list(self.after_restart)


@needs_capture
class TestTheCaptureLooksAgain(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, capture, "_open_streams", 0)
        capture._open_streams = 0

    def test_a_device_that_came_back_is_found(self):
        sd = FakeSd([BUILTIN], [BUILTIN, USB])
        self.assertEqual(capture._resolve_device(sd, USB["name"]), 1)
        self.assertEqual(sd.restarts, 1)

    def test_a_device_already_listed_costs_no_restart(self):
        sd = FakeSd([BUILTIN, USB], [BUILTIN, USB])
        capture._resolve_device(sd, USB["name"])
        self.assertEqual(sd.restarts, 0)

    def test_not_while_another_stream_is_open(self):
        """Starting PortAudio again would kill the stream that is open -
        somebody else's Listen, on another sound card."""
        capture._open_streams = 1
        sd = FakeSd([BUILTIN], [BUILTIN, USB])
        with self.assertRaises(capture.Missing):
            capture._resolve_device(sd, USB["name"])
        self.assertEqual(sd.restarts, 0)

    def test_still_missing_says_what_to_do_on_linux(self):
        with mock.patch.object(capture.sys, "platform", "linux"):
            hint = capture._missing_hint()
        self.assertIn("wireplumber", hint)
        self.assertIn("directly", hint)
        with mock.patch.object(capture.sys, "platform", "win32"):
            self.assertNotIn("wireplumber", capture._missing_hint())

    def test_a_capture_of_a_missing_input_reports_it_and_its_cause(self):
        sd = FakeSd([BUILTIN], [BUILTIN])
        cap = capture.Capture(USB["name"])
        with mock.patch.dict(sys.modules, {"sounddevice": sd}), \
                mock.patch.object(capture.sys, "platform", "linux"):
            cap._run()
        self.assertEqual(cap.status, "error")
        self.assertIn("no input called", cap.error)
        self.assertIn("wireplumber", cap.hint)
        self.assertNotIn("WSJT-X", cap.hint,
                         "that hint sends people looking for programs "
                         "that are not running")


class TestTheHostRescan(unittest.TestCase):
    def test_rescan_starts_portaudio_again(self):
        sd = FakeSd([BUILTIN], [BUILTIN, USB])
        with mock.patch.dict(sys.modules, {"sounddevice": sd}):
            got = audio._probe(fresh=True)
        self.assertEqual(sd.restarts, 1)
        self.assertIn(USB["name"], [d["id"] for d in got["inputs"]])

    def test_an_ordinary_look_does_not(self):
        sd = FakeSd([BUILTIN], [BUILTIN, USB])
        with mock.patch.dict(sys.modules, {"sounddevice": sd}):
            got = audio._probe()
        self.assertEqual(sd.restarts, 0)
        self.assertNotIn(USB["name"], [d["id"] for d in got["inputs"]])


class TestTheCardsListFromTheHost(unittest.TestCase):
    """They no longer open sound cards, so they no longer keep their own,
    stale, view of which exist."""

    def test_no_portaudio_in_the_spectrum_or_decoder(self):
        root = Path(__file__).resolve().parent.parent / "plugins"
        for card in ("spectrum", "decoder"):
            src = (root / card / "card.py").read_text(encoding="utf-8")
            with self.subTest(card=card):
                self.assertNotIn("import sounddevice", src)
                self.assertIn("card.host.audio_devices(", src)


if __name__ == "__main__":
    unittest.main()


def _decoder():
    import importlib.util
    root = Path(__file__).resolve().parent.parent / "plugins"
    for p in (root / "decoder", root / "rx_audio"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    spec = importlib.util.spec_from_file_location(
        "decoder_card_for_rescan_tests", root / "decoder" / "card.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@needs_capture
class TestTheDecoderHearsTheSimulatedRadio(unittest.TestCase):
    """Listen played the simulated radio's audio and the decoder refused
    the same radio with "no audio input set". One answer for both."""

    @classmethod
    def setUpClass(cls):
        cls.m = _decoder()

    def test_no_input_on_the_simulator_is_the_mock(self):
        self.assertEqual(self.m.resolve_source(
            {"source": "radio"}, {"mock": True, "audio_in": ""}), ("mock", ""))

    def test_a_real_radio_with_none_still_refuses(self):
        """Not the system default input, which is usually a microphone."""
        device, note = self.m.resolve_source(
            {"source": "radio"}, {"mock": False, "audio_in": ""})
        self.assertEqual(device, "")
        self.assertIn("no audio input set", note)

    def test_the_simulator_given_an_input_uses_it(self):
        self.assertEqual(self.m.resolve_source(
            {"source": "radio"}, {"mock": True, "audio_in": "USB CODEC"}),
            ("USB CODEC", ""))
