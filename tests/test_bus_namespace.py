"""Bus namespace policy.

The host owns RESERVED; a card owns <card_id>.* and nothing else. Before
this, any card could publish radio.state and make every tile on the board
believe the rig had moved.

Run with:  python -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

from hamdash.bus import ALWAYS, Bus, covered, owns, refusal
from hamdash.registry import Card, discover

PLUGINS = Path(__file__).resolve().parent.parent / "plugins"


class TestOwns(unittest.TestCase):
    def test_own_namespace(self):
        self.assertTrue(owns("mycard", "mycard"))
        self.assertTrue(owns("mycard", "mycard.tuned"))
        self.assertTrue(owns("mycard", "mycard.a.b"))

    def test_prefix_is_not_ownership(self):
        """'mycard' must not own 'mycardiogram.x'."""
        self.assertFalse(owns("mycard", "mycardiogram.x"))
        self.assertFalse(owns("mycard", "other.thing"))


class TestRefusal(unittest.TestCase):
    def test_own_namespace_allowed(self):
        self.assertIsNone(refusal("mycard", "mycard.tuned"))

    def test_reserved_refused(self):
        for topic in ("radio.state", "log.qso_added", "lookup.result",
                      "card.settings_changed", "config.changed", "ui.pop_in",
                      "rig.state"):
            with self.subTest(topic=topic):
                why = refusal("mycard", topic)
                self.assertIsNotNone(why, topic + " should be reserved")
                self.assertIn("reserved", why)

    def test_bare_reserved_word_refused(self):
        """'radio' with no dot is still the reserved namespace."""
        self.assertIsNotNone(refusal("mycard", "radio"))

    def test_a_card_cannot_take_a_reserved_namespace_as_its_id(self):
        """Even for a card called 'radio', the host keeps radio.*"""
        self.assertIsNotNone(refusal("radio", "radio.state"))

    def test_other_card_namespace_refused(self):
        self.assertIn("may only publish mycard.*",
                      refusal("mycard", "othercard.thing"))

    def test_declared_list_narrows(self):
        declared = ["mycard.tuned"]
        self.assertIsNone(refusal("mycard", "mycard.tuned", declared))
        self.assertIn("publishes list",
                      refusal("mycard", "mycard.tunned", declared))

    def test_undeclared_means_the_whole_namespace(self):
        self.assertIsNone(refusal("mycard", "mycard.anything", []))
        self.assertIsNone(refusal("mycard", "mycard.anything", None))

    def test_empty_topic(self):
        self.assertIsNotNone(refusal("mycard", ""))

    def test_messages_name_the_fix(self):
        """House style: an error says what to do, not just what failed."""
        self.assertIn("mycard.*", refusal("mycard", "radio.state"))
        self.assertIn("card.json", refusal("mycard", "mycard.x", ["mycard.y"]))


class TestSubscribeFilter(unittest.TestCase):
    def setUp(self):
        self.bus = Bus()
        self.bus.publish("radio.state", {"hz": 1}, source="radio")
        self.bus.publish("log.qso_added", {"call": "VE7ABC"}, source="host")
        self.bus.publish("othercard.noise", {}, source="othercard")
        self.bus.publish("card.settings_changed", {"uid": "h1"}, source="host")

    def topics(self, **kw):
        return sorted(e["topic"] for e in self.bus.recent(limit=99, **kw))

    def test_undeclared_gets_everything(self):
        self.assertIn("othercard.noise", self.topics(allow=None))

    def test_declared_narrows(self):
        got = self.topics(allow=["radio.state"])
        self.assertIn("radio.state", got)
        self.assertNotIn("othercard.noise", got)
        self.assertNotIn("log.qso_added", got)

    def test_settings_changed_always_arrives(self):
        """Regression guard. Every python card implicitly subscribes to
        card.settings_changed (the SDK registers it), so a card declaring a
        narrow subscribes list would otherwise quietly stop noticing tile
        settings edits - which looks like a caching bug and is not one."""
        self.assertIn("card.settings_changed",
                      self.topics(allow=["radio.state"]))
        self.assertIn("card.settings_changed", ALWAYS)

    def test_requested_filter_still_applies_on_top(self):
        got = sorted(e["topic"] for e in
                     self.bus.recent(topics="radio.", limit=99,
                                     allow=["radio.state", "log.qso_added"]))
        self.assertEqual(got, ["radio.state"])

    def test_stream_snapshot_honours_allow(self):
        gen = self.bus.stream(topics="*", allow=["radio.state"])
        try:
            first = next(gen)
        finally:
            gen.close()
        self.assertIn("radio.state", first)
        self.assertNotIn("othercard.noise", first)


class TestCovered(unittest.TestCase):
    def test_prefix_forms(self):
        self.assertTrue(covered("radio.state", ["radio."]))
        self.assertTrue(covered("radio.state", ["radio.*"]))
        self.assertTrue(covered("radio.state", ["radio.state"]))
        self.assertFalse(covered("radio.state", ["log."]))


class TestManifest(unittest.TestCase):
    def card(self, **kw):
        c = Card(id="mycard", path=Path("."), **kw)
        c.check_namespace()
        return c

    def test_bad_publishes_entry_warns_and_is_dropped(self):
        c = self.card(publishes=["mycard.ok", "radio.state", "other.x"])
        self.assertEqual(c.publishes, ["mycard.ok"])
        self.assertEqual(len(c.warnings), 2)
        self.assertTrue(all(w.startswith("publishes:") for w in c.warnings))

    def test_bad_manifest_does_not_stop_the_card(self):
        """A dud topic is a warning, not an error: the card still runs."""
        self.assertIsNone(self.card(publishes=["radio.state"]).error)

    def test_string_instead_of_list_is_accepted(self):
        self.assertEqual(self.card(subscribes="radio.state").subscribes,
                         ["radio.state"])

    def test_junk_entries_are_dropped(self):
        self.assertEqual(self.card(subscribes=["", "  ", None, 7]).subscribes,
                         [])

    def test_may_publish_uses_the_declaration(self):
        c = self.card(publishes=["mycard.ok"])
        self.assertIsNone(c.may_publish("mycard.ok"))
        self.assertIsNotNone(c.may_publish("mycard.nope"))


class TestBundledCards(unittest.TestCase):
    """The shipped cards must satisfy the policy they document."""

    def setUp(self):
        self.cards = discover([PLUGINS])

    def test_discovers_them(self):
        self.assertIn("logbook", self.cards)

    def test_no_manifest_warnings(self):
        for cid, c in self.cards.items():
            with self.subTest(card=cid):
                self.assertEqual(c.warnings, [], cid + " manifest warns")

    def test_every_declared_topic_is_publishable(self):
        for cid, c in self.cards.items():
            for topic in c.publishes:
                with self.subTest(card=cid, topic=topic):
                    self.assertIsNone(c.may_publish(topic))

    def test_code_only_publishes_what_the_manifest_declares(self):
        found = re.compile(r"""(?:card|HD)\.publish\(\s*['"]([^'"]+)['"]""")
        for cid, c in self.cards.items():
            for src in sorted((PLUGINS / cid).rglob("*")):
                if src.suffix not in (".py", ".html"):
                    continue
                for topic in found.findall(src.read_text(encoding="utf-8")):
                    with self.subTest(card=cid, topic=topic, file=src.name):
                        self.assertIsNone(
                            c.may_publish(topic),
                            cid + " publishes " + topic + " in " + src.name)

    def test_declared_subscribes_cover_what_the_code_listens_for(self):
        """A handler outside the declared list can never fire, and nothing in
        the log would say why."""
        listens = re.compile(r"""(?:card|HD)\.on\(\s*['"]([^'"]+)['"]""")
        for cid, c in self.cards.items():
            if not c.subscribes:
                continue
            for src in sorted((PLUGINS / cid).rglob("*")):
                if src.suffix not in (".py", ".html"):
                    continue
                for topic in listens.findall(src.read_text(encoding="utf-8")):
                    if topic in ALWAYS or topic == "*":
                        continue
                    with self.subTest(card=cid, topic=topic, file=src.name):
                        self.assertTrue(
                            covered(topic, c.subscribes),
                            cid + " listens for " + topic + " in " + src.name
                            + " but card.json subscribes to "
                            + str(c.subscribes))


if __name__ == "__main__":
    unittest.main()
