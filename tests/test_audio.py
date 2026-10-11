"""Sound devices, listed for a rig entry.

Nothing here touches PortAudio: `_probe` is stood in for, because the point
of the module is what happens around the probe - the Windows name quirks, the
timeout, the cache - and because CI has no sound card.

Run with:  python -m unittest discover -s tests
"""
import threading
import time
import unittest
from unittest import mock

from hamdash.services import audio


def a_device(name, hostapi=0, ins=0, outs=0):
    return {"name": name, "hostapi": hostapi,
            "max_input_channels": ins, "max_output_channels": outs}


class FakePortAudio:
    """Just enough sounddevice to drive `_probe`."""

    def __init__(self, devices, apis=("MME", "Windows DirectSound"),
                 default=(0, 0)):
        self._devices = devices
        self._apis = [{"name": n} for n in apis]
        self.default = mock.Mock(device=list(default))

    def query_devices(self):
        return self._devices

    def query_hostapis(self):
        return self._apis


def probe_with(fake):
    with mock.patch.dict("sys.modules", {"sounddevice": fake}):
        return audio._probe()


class TestWhatComesBack(unittest.TestCase):
    def test_inputs_and_outputs_are_separated(self):
        got = probe_with(FakePortAudio([
            a_device("Mic", ins=1),
            a_device("Speakers", outs=2),
        ]))
        self.assertTrue(got["ok"])
        self.assertEqual([d["id"] for d in got["inputs"]], ["Mic"])
        self.assertEqual([d["id"] for d in got["outputs"]], ["Speakers"])

    def test_one_row_per_device_not_per_host_api(self):
        """Windows publishes the same sound card under MME, WASAPI and
        DirectSound. Since the stored value is the name, several rows would
        all select the same thing - a question with no right answer."""
        got = probe_with(FakePortAudio([
            a_device("USB Audio CODEC", hostapi=0, ins=1),
            a_device("USB Audio CODEC", hostapi=1, ins=2),
        ]))
        self.assertEqual(len(got["inputs"]), 1)
        row = got["inputs"][0]
        self.assertEqual(row["channels"], 2)      # the best any API offers
        self.assertEqual(sorted(row["hostapis"]),
                         ["MME", "Windows DirectSound"])

    def test_mme_truncation_is_folded_into_the_full_name(self):
        """MME cuts a device name at 31 characters, so one sound card is
        listed twice differing by a missing bracket. Whichever the operator
        picked it would be the same device."""
        short = "Microphone (4- USB Audio Device"
        full = "Microphone (4- USB Audio Device)"
        self.assertEqual(len(short), audio.MME_NAME_LIMIT)
        got = probe_with(FakePortAudio([
            a_device(short, hostapi=0, ins=1),
            a_device(full, hostapi=1, ins=1),
        ]))
        self.assertEqual([d["id"] for d in got["inputs"]], [full])

    def test_a_short_name_that_is_nobody_s_prefix_survives(self):
        got = probe_with(FakePortAudio([a_device("x" * 31, ins=1)]))
        self.assertEqual(len(got["inputs"]), 1)

    def test_the_default_device_is_marked_and_comes_first(self):
        got = probe_with(FakePortAudio([
            a_device("AAA first alphabetically", ins=1),
            a_device("The default one", ins=1),
        ], default=(1, 0)))
        self.assertEqual(got["inputs"][0]["id"], "The default one")
        self.assertTrue(got["inputs"][0]["default"])

    def test_a_name_with_a_newline_in_it_is_cleaned_for_display(self):
        """Windows really does put a CRLF inside Bluetooth headset names,
        which turns one dropdown option into three lines of nonsense."""
        raw = "Headset (#2;%1 Hands-Free%0\r\n;(Bluedio T))"
        got = probe_with(FakePortAudio([a_device(raw, ins=1)]))
        row = got["inputs"][0]
        self.assertNotIn("\n", row["name"])
        # The raw name is what has to match when something opens it later.
        self.assertEqual(row["id"], raw)

    def test_no_sounddevice_names_the_fix(self):
        with mock.patch.dict("sys.modules", {"sounddevice": None}):
            got = audio._probe()
        self.assertFalse(got["ok"])
        self.assertIn("pip install sounddevice", got["error"])
        self.assertEqual(got["inputs"], [])

    def test_portaudio_blowing_up_is_reported_not_raised(self):
        fake = FakePortAudio([])
        fake.query_devices = mock.Mock(side_effect=OSError("no host API"))
        got = probe_with(fake)
        self.assertFalse(got["ok"])
        self.assertIn("no host API", got["error"])


class TestNotHoldingAServerThread(unittest.TestCase):
    """Enumerating can hang on Windows, and every request handler runs on one
    of a pool of 64 waitress threads (invariant 4)."""

    def setUp(self):
        # A stuck probe from the test before is still running, and bumping
        # the generation is what stops it writing into this one.
        audio._cache, audio._cached_at = {}, 0.0
        audio._worker = None
        audio._generation += 1
        audio._done.clear()
        self.addCleanup(setattr, audio, "_cache", {})
        self.addCleanup(setattr, audio, "_cached_at", 0.0)

    def test_a_probe_that_never_returns_does_not_hold_the_caller(self):
        stuck = threading.Event()
        self.addCleanup(stuck.set)
        with mock.patch.object(audio, "ENUM_TIMEOUT", 0.2), \
                mock.patch.object(audio, "_probe",
                                  side_effect=lambda: stuck.wait()):
            began = time.time()
            got = audio.devices()
        self.assertLess(time.time() - began, 2.0)
        self.assertFalse(got["ok"])
        self.assertIn("Rescan", got["error"])

    def test_a_timeout_still_serves_what_it_had(self):
        """Stale beats empty: they are device names and they were right a
        moment ago."""
        audio._cache = {"ok": True, "inputs": [{"id": "Mic"}], "outputs": []}
        audio._cached_at = 0.0                       # too old to be fresh
        stuck = threading.Event()
        self.addCleanup(stuck.set)
        with mock.patch.object(audio, "ENUM_TIMEOUT", 0.2), \
                mock.patch.object(audio, "_probe",
                                  side_effect=lambda: stuck.wait()):
            got = audio.devices()
        self.assertTrue(got["stale"])
        self.assertEqual(got["inputs"], [{"id": "Mic"}])

    def test_a_second_wedged_probe_is_not_told_to_press_rescan(self):
        """Nothing can start while one is stuck inside PortAudio holding the
        lock, so "try Rescan" would send the operator at the wrong thing."""
        stuck = threading.Event()
        self.addCleanup(stuck.set)
        with mock.patch.object(audio, "ENUM_TIMEOUT", 0.2),                 mock.patch.object(audio, "_probe",
                                  side_effect=lambda: stuck.wait()):
            audio.devices()
            got = audio.devices()
        self.assertIn("driver", got["error"])
        self.assertNotIn("Rescan", got["error"])

    def test_the_answer_is_cached(self):
        probe = mock.Mock(return_value={"ok": True, "inputs": [],
                                        "outputs": [], "error": ""})
        with mock.patch.object(audio, "_probe", probe):
            audio.devices()
            audio.devices()
        self.assertEqual(probe.call_count, 1)

    def test_rescan_ignores_the_cache(self):
        """Devices do change while the app runs - that is the rig interface
        being plugged in."""
        probe = mock.Mock(return_value={"ok": True, "inputs": [],
                                        "outputs": [], "error": ""})
        with mock.patch.object(audio, "_probe", probe):
            audio.devices()
            self._settle()
            audio.devices(refresh=True)
        self.assertEqual(probe.call_count, 2)

    def test_a_rescan_during_a_probe_does_not_start_a_second_one(self):
        """The other half of the same rule, and the reason the test above
        has to wait: PortAudio is behind one lock, so a second probe would
        only queue behind the first."""
        probe = mock.Mock(return_value={"ok": True, "inputs": [],
                                        "outputs": [], "error": ""})
        with mock.patch.object(audio, "_probe", probe):
            audio.devices()
            audio.devices(refresh=True)         # no wait: still in flight
        self.assertLessEqual(probe.call_count, 2)

    def _settle(self):
        """Wait for the probe thread to finish.

        Without this the test is a race: `devices(refresh=True)` will not
        start a second probe while the first is still running - deliberately,
        because they share one PortAudio lock - so on a loaded machine the
        refresh was a no-op and the count came back 1. It failed on one CI
        runner out of six.
        """
        worker = audio._worker
        if worker:
            worker.join(timeout=5)


class TestStoringADevice(unittest.TestCase):
    def test_it_is_a_name_and_it_is_trimmed(self):
        self.assertEqual(audio.clean_device("  USB Audio CODEC \n"),
                         "USB Audio CODEC")

    def test_nothing_chosen_is_empty(self):
        for junk in (None, "", "   ", 0):
            with self.subTest(junk=junk):
                self.assertEqual(audio.clean_device(junk), "")

    def test_it_is_bounded(self):
        self.assertEqual(len(audio.clean_device("x" * 500)), 120)


if __name__ == "__main__":
    unittest.main()
