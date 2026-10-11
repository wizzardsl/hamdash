"""The card author's documentation, checked against the code it describes.

`docs/cards.md` is the contract somebody writes a card against, and a
reference that has drifted is worse than none: it sends them looking for a
method that was renamed, or leaves a whole service undiscovered because
nobody remembered to write it down. The SDK is small enough that "everything
public is mentioned" is a rule a test can keep.

This does not check that the prose is *right* - nothing can - only that
nothing has been added, renamed or removed without the docs noticing.

Run with:  python -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

from hamdash.sdk import Card, HostClient

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs" / "cards.md"
TEMPLATE = ROOT / "docs" / "card-template"
HAMDASH_JS = ROOT / "hamdash" / "static" / "js" / "hamdash.js"
APP_PY = ROOT / "hamdash" / "app.py"


def public(cls):
    return sorted(n for n in vars(cls) if not n.startswith("_"))


class TestTheReferenceExists(unittest.TestCase):
    def test_it_is_there(self):
        self.assertTrue(DOCS.exists(), "docs/cards.md has gone")

    def test_the_template_is_a_card(self):
        """It is advertised as "copy this and it runs", so it has to be a
        real card: a manifest, and the page the manifest names."""
        import json
        manifest = TEMPLATE / "card.json"
        self.assertTrue(manifest.exists(), "the template has no card.json")
        got = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(got["id"], "card_template")
        self.assertIn(got["kind"], ("url", "html", "python"))
        self.assertTrue((TEMPLATE / got["view"]).exists(),
                        f"the template's view {got['view']!r} is missing")

    def test_the_template_carries_its_claude_notes(self):
        notes = TEMPLATE / "CLAUDE.md"
        self.assertTrue(notes.exists())
        said = notes.read_text(encoding="utf-8")
        for must in ("card.json", "HD.settings", "card.host", "Rescan"):
            with self.subTest(must=must):
                self.assertIn(must, said)


class TestEveryPublicThingIsDocumented(unittest.TestCase):
    """Add a method to the SDK and this fails until it is written down."""

    def setUp(self):
        self.said = DOCS.read_text(encoding="utf-8")

    def test_the_card_class(self):
        missing = [n for n in public(Card) if n not in self.said]
        self.assertEqual(missing, [], f"undocumented on Card: {missing}")

    def test_the_host_client(self):
        missing = [n for n in public(HostClient) if n not in self.said]
        self.assertEqual(missing, [],
                         f"undocumented on card.host: {missing}")

    def test_the_browser_api(self):
        """The keys of the HD object in hamdash.js."""
        js = HAMDASH_JS.read_text(encoding="utf-8")
        body = js.split("const HD = {", 1)[1]
        # Top-level keys only: four spaces in, then `name:` or `name(`.
        keys = set(re.findall(r"(?m)^    ([a-zA-Z][\w]*)\s*[:(]", body))
        keys -= {"cardId"}          # set by the card, documented as prose
        missing = sorted(k for k in keys if f"HD.{k}" not in self.said)
        self.assertEqual(missing, [], f"undocumented on HD: {missing}")


class TestThePermissionsMatch(unittest.TestCase):
    """The names in the table are what a card actually has to write in its
    manifest, so a new gate that nobody documented leaves an author with a
    403 and no way to find out which word fixes it."""

    def test_every_gate_is_in_the_table(self):
        app = APP_PY.read_text(encoding="utf-8")
        gates = set(re.findall(r'guard\("([a-z_]+)"\)', app))
        self.assertTrue(gates, "no permission gates found at all")
        said = DOCS.read_text(encoding="utf-8")
        missing = sorted(g for g in gates if f"`{g}`" not in said)
        self.assertEqual(missing, [],
                         f"permissions the docs never mention: {missing}")

    def test_the_table_invents_nothing(self):
        app = APP_PY.read_text(encoding="utf-8")
        gates = set(re.findall(r'guard\("([a-z_]+)"\)', app))
        said = DOCS.read_text(encoding="utf-8")
        table = set(re.findall(r"^\| `([a-z_]+)` \| `/api/", said, re.M))
        extra = sorted(t for t in table if t not in gates)
        self.assertEqual(extra, [],
                         f"documented permissions that do not exist: {extra}")


class TestTheSettingsTypesMatch(unittest.TestCase):
    def test_every_type_the_host_renders_is_documented(self):
        reg = (ROOT / "hamdash" / "registry.py").read_text(encoding="utf-8")
        kinds = set(re.findall(
            r'kind not in \(([^)]*)\)', reg)[0].replace('"', '').split(", "))
        said = DOCS.read_text(encoding="utf-8")
        missing = sorted(k for k in kinds if f"`{k}`" not in said)
        self.assertEqual(missing, [],
                         f"settings types nobody wrote down: {missing}")


if __name__ == "__main__":
    unittest.main()
