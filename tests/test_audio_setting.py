"""Choosing a sound input, rather than typing one from memory.

Three cards want "which sound card" and all three asked for it as free
text - part of a device name, typed. That is how you get a card that is
silently deaf: nothing on screen is wrong, the input just never matched,
and the operator goes looking at the radio.

`audio_in` is a `select` the *host* fills, because a card cannot know
what this machine has. The card's manifest contributes the entries that
are not devices at all (`rig`, `mock`) and the host appends the real
inputs.

Run with:  python -m unittest discover -s tests
"""
import json
import unittest
from pathlib import Path

from hamdash import registry

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "hamdash" / "static" / "js" / "app.js"


def a_card(**setting):
    return registry.Card(id="x", path=ROOT, settings={"device": setting})


class TestTheHostAcceptsIt(unittest.TestCase):
    def test_the_type_survives_the_manifest(self):
        f = a_card(type="audio_in", label="Audio input",
                   options=["rig", "mock"], default="mock").schema()[0]
        self.assertEqual(f["type"], "audio_in")
        self.assertEqual(f["options"], ["rig", "mock"])
        self.assertEqual(f["default"], "mock")

    def test_an_unknown_type_still_falls_back_to_string(self):
        # The fallback is what keeps a typo in a manifest from becoming a
        # field the sheet cannot draw.
        self.assertEqual(a_card(type="loudspeaker").schema()[0]["type"],
                         "string")

    def test_options_are_not_required(self):
        # A card that only wants the machine's real inputs says nothing.
        f = a_card(type="audio_in").schema()[0]
        self.assertEqual(f["options"], [])


class TestTheSheetDrawsIt(unittest.TestCase):
    """A type the host accepts and the sheet cannot draw is worse than
    no type: the field renders as a text box and nothing says so."""

    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_the_field_renderer_handles_it(self):
        self.assertIn("f.type === 'audio_in'", self.js,
                      "the settings sheet would render it as a text box")

    def test_it_asks_the_host_for_the_devices(self):
        at = self.js.index("f.type === 'audio_in'")
        body = self.js[at:at + 2500]
        self.assertIn("/api/audio/devices", body,
                      "the list would never be filled in")

    def test_a_device_that_has_gone_is_kept_and_marked(self):
        # Silently selecting something else would move the operator's
        # audio to a different sound card without saying so.
        at = self.js.index("f.type === 'audio_in'")
        body = self.js[at:at + 2500]
        self.assertIn("not found now", body)


class TestTheListenCardUsesIt(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(
            (ROOT / "plugins" / "rx_audio" / "card.json").read_text(
                encoding="utf-8"))
        self.device = self.manifest["settings"]["device"]

    def test_the_input_is_chosen_not_typed(self):
        self.assertEqual(self.device["type"], "audio_in")

    def test_rig_and_mock_are_both_offered(self):
        self.assertIn("rig", self.device["options"])
        self.assertIn("mock", self.device["options"])

    def test_it_still_mocks_by_default(self):
        # The house rule for every audio card: a card that needs hardware
        # to show anything cannot be developed or demonstrated. And on a
        # fresh install `rig` has no audio configured to follow, so it
        # would fall through to the system default input - which is to
        # say it would open somebody's microphone without being asked.
        self.assertEqual(self.device["default"], "mock")

    def test_the_card_still_resolves_rig_to_the_radios_input(self):
        # The resolution lives in the card, not in the manifest, so the
        # dropdown cannot have broken it.
        src = (ROOT / "plugins" / "rx_audio" / "card.py").read_text(
            encoding="utf-8")
        self.assertIn('if want != "rig"', src)
        self.assertIn('audio_in', src)


if __name__ == "__main__":
    unittest.main()
