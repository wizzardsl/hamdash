"""Listening moved into the rig card; the Listen card was retired.

The receive audio is a particular radio's, so its controls belong at the
foot of the rig card rather than in a tile of their own. Three things
had to hold for that to work, and each has a test here:

* The audio engine is still the `rx_audio` card's process - a sound card
  that crashes takes that down, not rig control - but the host lets a
  card read only its *own* tiles' settings, so the engine cannot see what
  the rig tile chose. The page passes the device and rate on each request.
* The rig card defaults to the radio's own input ('rig'). That used to be
  unsafe: a radio with no input set resolved to "", the system default,
  which is usually a microphone - which is why the Listen card defaulted
  to a test tone. It refuses now and says what to set.
* The retired card's tiles come off the board, it leaves the Cards menu,
  and its process is *not* reaped while a rig tile is using it - reaping
  goes by tiles, and it has none.

Run with:  python -m unittest discover -s tests
"""
import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
sys.path.insert(0, str(PLUGINS / "rx_audio"))

try:
    import numpy  # noqa: F401  - the engine imports it
    HAVE_NUMPY = True
except ImportError:                                     # pragma: no cover
    HAVE_NUMPY = False
needs_numpy = unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")


def load_engine():
    """The real rx_audio card module, without starting its server."""
    from hamdash.sdk import Card
    with mock.patch.object(Card, "run", lambda self: None):
        spec = importlib.util.spec_from_file_location(
            "rx_audio_engine_for_tests", PLUGINS / "rx_audio" / "card.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


@needs_numpy
class TestFollowingTheRadioIsSafe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_engine()

    def radio(self, audio_in):
        return mock.patch.object(self.m.card.host, "radio",
                                 return_value={"audio_in": audio_in})

    def test_a_radio_with_an_input_is_followed(self):
        with self.radio("USB Audio Device"):
            self.assertEqual(self.m._device_for({"device": "rig"}),
                             "USB Audio Device")

    def test_a_radio_with_none_set_is_never_the_system_default(self):
        """"" opens the system default input - a microphone."""
        for none in ("", "   ", None):
            with self.radio(none):
                got = self.m._device_for({"device": "rig"})
            self.assertNotEqual(got, "", repr(none))
            self.assertEqual(got, self.m.NO_RIG_INPUT)

    def test_the_host_not_answering_is_not_the_system_default_either(self):
        with mock.patch.object(self.m.card.host, "radio",
                               side_effect=RuntimeError("host down")):
            self.assertEqual(self.m._device_for({"device": "rig"}),
                             self.m.NO_RIG_INPUT)

    def test_a_tap_for_no_input_refuses_without_opening_anything(self):
        sd = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"sounddevice": sd}):
            t = self.m.Tap(self.m.NO_RIG_INPUT, 12000)
            t._thread.join(2)
        self.assertEqual(t.status, "error")
        self.assertIn("no audio input set", t.error)
        self.assertIn("Settings -> Radio", t.hint)
        sd.InputStream.assert_not_called()

    def test_the_status_route_says_so_before_listen_is_pressed(self):
        client = self.m.card.app.test_client()
        with self.radio(""), mock.patch.object(self.m.card, "settings",
                                               return_value={}):
            got = client.get("/api/info?device=rig").get_json()
        self.assertEqual(got["status"], "error")
        self.assertIn("no audio input set", got["error"])


@needs_numpy
class TestTheEngineTakesItsOrdersFromThePage(unittest.TestCase):
    """A card may read only its own tiles' settings (the host answers
    anybody else with 403), so the rig page says what its tile chose."""

    @classmethod
    def setUpClass(cls):
        cls.m = load_engine()

    def conf(self, query, tile=None):
        with self.m.card.app.test_request_context(f"/api/info?{query}"), \
             mock.patch.object(self.m.card, "settings",
                               return_value=dict(tile or {})):
            return self.m._conf()

    def test_device_and_rate_from_the_request(self):
        self.assertEqual(self.conf("device=rig&rate=8000",
                                   {"device": "mock", "rate": 12000}),
                         {"device": "rig", "rate": 8000})

    def test_without_them_the_tiles_own_settings(self):
        self.assertEqual(self.conf("who=x", {"device": "mock", "rate": 12000}),
                         {"device": "mock", "rate": 12000})

    def test_a_blank_or_junk_override_is_ignored(self):
        self.assertEqual(self.conf("device=&rate=fast",
                                   {"device": "mock", "rate": 12000}),
                         {"device": "mock", "rate": 12000})

    def test_the_host_is_not_asked_when_the_page_has_said(self):
        """The rig tile is not this card's: the host answers 403 and the
        log reported it as a fault on every listen."""
        with self.m.card.app.test_request_context("/api/info?device=rig"), \
             mock.patch.object(self.m.card, "settings") as asked:
            self.m._conf()
        asked.assert_not_called()

    def test_both_routes_use_it(self):
        src = (PLUGINS / "rx_audio" / "card.py").read_text(encoding="utf-8")
        for route in ('@card.get("/info")', '@card.get("/pcm")'):
            i = src.index(route)
            self.assertIn("_conf()", src[i:i + 600], route)


class TestTheRigCard(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(
            (PLUGINS / "rig_control" / "card.json").read_text(encoding="utf-8"))
        self.page = (PLUGINS / "rig_control" / "index.html").read_text(
            encoding="utf-8")

    def test_it_defaults_to_the_radios_own_input(self):
        dev = self.manifest["settings"]["device"]
        self.assertEqual(dev["type"], "audio_in")
        self.assertEqual(dev["default"], "rig")
        self.assertIn("mock", dev["options"])

    def test_quality_and_buffer_came_with_it(self):
        self.assertIn("rate", self.manifest["settings"])
        self.assertIn("buffer_ms", self.manifest["settings"])

    def test_it_declares_that_it_uses_the_engine(self):
        self.assertIn("rx_audio", self.manifest["uses"])

    def test_the_page_mounts_the_player_with_its_own_settings(self):
        self.assertIn('<script src="/static/js/listen.js"></script>', self.page)
        self.assertRegex(self.page, r"HDListen\.mount\(\$\('#listen'\),\s*"
                                    r"\{\s*conf:\s*\(\)\s*=>\s*conf\s*\}\)")

    def test_stop_waits_for_the_request_already_on_its_way(self):
        """Found live: an audio request sent just before Stop reached the
        engine after it, re-opened the sound card, and held it for the
        reaper's 20 s - the opposite of what Stop is pressed for."""
        js = (ROOT / "hamdash" / "static" / "js" / "listen.js").read_text(
            encoding="utf-8")
        i = js.index("async function stop(")
        body = js[i:js.index("function setVolume", i)]
        self.assertLess(body.index("await inflight"), body.index("/api/stop"))
        i = js.index("async function pull(")
        body = js[i:js.index("function schedule", i)]
        self.assertIn("if (!running) return;", body[body.index("inflight = null"):])

    def test_the_player_sends_device_and_rate(self):
        js = (ROOT / "hamdash" / "static" / "js" / "listen.js").read_text(
            encoding="utf-8")
        self.assertIn("q.set('device', c.device)", js)
        self.assertIn("q.set('rate', c.rate)", js)


class TestTheListenCardIsRetired(unittest.TestCase):
    def test_its_manifest_says_hidden(self):
        from hamdash import registry
        cards = registry.discover([PLUGINS])
        self.assertTrue(cards["rx_audio"].hidden)
        self.assertFalse(cards["rig_control"].hidden)
        self.assertEqual(cards["rig_control"].uses, ["rx_audio"])

    def test_the_board_does_not_offer_it(self):
        js = (ROOT / "hamdash" / "static" / "js" / "app.js").read_text(
            encoding="utf-8")
        self.assertIn("const offered = () => cards.filter((c) => !c.hidden);", js)
        for fn in ("renderPalette", "renderCardsMenu"):
            i = js.index(f"function {fn}()")
            self.assertIn("offered().forEach", js[i:i + 300], fn)


class TestWithoutCards(unittest.TestCase):
    def test_a_tile_of_only_that_card_goes(self):
        from hamdash.config import without_cards
        got = without_cards([
            {"uid": "t1", "card": "rig_control"},
            {"uid": "t2", "card": "rx_audio"},
        ], {"rx_audio"})
        self.assertEqual([e["card"] for t in got for e in t["cards"]],
                         ["rig_control"])

    def test_a_stack_keeps_its_other_cards_and_its_active_moves(self):
        from hamdash.config import without_cards
        got = without_cards([{
            "uid": "s1", "x": 0, "y": 0, "w": 4, "h": 3, "active": "a",
            "cards": [{"uid": "a", "card": "rx_audio", "settings": {}},
                      {"uid": "b", "card": "spots", "settings": {"x": 1}}]}],
            {"rx_audio"})
        self.assertEqual(len(got), 1)
        self.assertEqual([e["uid"] for e in got[0]["cards"]], ["b"])
        self.assertEqual(got[0]["active"], "b")
        self.assertEqual(got[0]["cards"][0]["settings"], {"x": 1})

    def test_nothing_retired_changes_nothing(self):
        from hamdash.config import normalise_layout, without_cards
        tiles = [{"uid": "t1", "card": "rig_control"}]
        self.assertEqual(without_cards(tiles, set()), normalise_layout(tiles))


class TestTheListenSettingsMoveToTheRig(unittest.TestCase):
    """Removing a Listen tile must not silently drop the input chosen in it."""

    def board(self, rig_settings=None, listen_settings=None):
        return [{"uid": "t1", "card": "rig_control",
                 "settings": dict(rig_settings or {})},
                {"uid": "t9", "card": "rx_audio",
                 "settings": dict(listen_settings or {})}]

    def rig(self, tiles):
        from hamdash.config import listen_moves_to_rig
        out = listen_moves_to_rig(tiles)
        return [e for t in out for e in t["cards"]
                if e["card"] == "rig_control"][0]["settings"]

    def test_the_chosen_input_comes_across(self):
        self.assertEqual(
            self.rig(self.board(listen_settings={
                "device": "Microphone (4- USB Audio Device)", "rate": "8000",
                "buffer_ms": 300})),
            {"device": "Microphone (4- USB Audio Device)", "rate": "8000",
             "buffer_ms": 300})

    def test_what_the_rig_tile_already_chose_wins(self):
        self.assertEqual(
            self.rig(self.board({"device": "mock", "step_hz": 500},
                                {"device": "USB", "rate": "8000"})),
            {"device": "mock", "step_hz": 500, "rate": "8000"})

    def test_only_listen_settings_come_across(self):
        self.assertEqual(self.rig(self.board(listen_settings={
            "device": "USB", "something_else": 1})), {"device": "USB"})

    def test_no_listen_tile_changes_nothing(self):
        from hamdash.config import listen_moves_to_rig, normalise_layout
        tiles = [{"uid": "t1", "card": "rig_control", "settings": {"step_hz": 9}}]
        self.assertEqual(listen_moves_to_rig(tiles), normalise_layout(tiles))


class TestTheHost(unittest.TestCase):
    def host(self, tiles):
        home = Path(tempfile.mkdtemp(prefix="hamdash-retire-"))
        (home / "config.json").write_text(json.dumps({
            "radio": {"mock": True}, "wsjtx": {"enabled": False}}),
            encoding="utf-8")
        (home / "layout.json").write_text(json.dumps(tiles), encoding="utf-8")
        from hamdash.app import create_app
        from hamdash.config import Config
        app, shutdown = create_app(Config(home), port=0)
        self.addCleanup(lambda: shutdown() if callable(shutdown) else None)
        return app.test_client(), home

    def cards_on(self, client):
        layout = client.get("/api/layout").get_json()["tiles"]
        return [e["card"] for t in layout for e in t["cards"]]

    def test_a_listen_tile_is_removed_at_startup(self):
        client, home = self.host([{"uid": "t1", "card": "rig_control"},
                                  {"uid": "t9", "card": "rx_audio"}])
        self.assertEqual(self.cards_on(client), ["rig_control"])
        saved = json.loads((home / "layout.json").read_text())
        self.assertNotIn("rx_audio", json.dumps(saved))

    def test_at_startup_its_input_moves_to_the_rig_tile(self):
        client, _ = self.host([
            {"uid": "t1", "card": "rig_control"},
            {"uid": "t9", "card": "rx_audio",
             "settings": {"device": "USB Audio Device", "rate": "8000"}}])
        got = client.get("/api/tiles/t1/settings").get_json()["values"]
        self.assertEqual((got["device"], got["rate"]),
                         ("USB Audio Device", "8000"))

    def test_a_board_saving_one_back_does_not_bring_it_back(self):
        client, _ = self.host([{"uid": "t1", "card": "rig_control"}])
        client.put("/api/layout", json={"tiles": [
            {"uid": "t1", "card": "rig_control"},
            {"uid": "t9", "card": "rx_audio"}]})
        self.assertEqual(self.cards_on(client), ["rig_control"])

    def test_the_engine_is_kept_while_a_rig_tile_uses_it(self):
        """Reaping goes by tiles, and the engine has none of its own."""
        client, _ = self.host([{"uid": "t1", "card": "rig_control"}])
        with mock.patch("hamdash.app.Supervisor.reap", return_value=[]) as reap:
            client.put("/api/layout", json={"tiles": [
                {"uid": "t1", "card": "rig_control"}]})
        kept = reap.call_args.args[0]
        self.assertIn("rig_control", kept)
        self.assertIn("rx_audio", kept)

    def test_without_a_rig_tile_the_engine_can_go(self):
        client, _ = self.host([{"uid": "t1", "card": "rig_control"}])
        with mock.patch("hamdash.app.Supervisor.reap", return_value=[]) as reap:
            client.put("/api/layout", json={"tiles": [
                {"uid": "t2", "card": "spots"}]})
        self.assertNotIn("rx_audio", reap.call_args.args[0])

    def test_the_card_list_says_which_are_retired(self):
        client, _ = self.host([])
        listed = {c["id"]: c for c in client.get("/api/cards").get_json()}
        self.assertTrue(listed["rx_audio"]["hidden"])
        self.assertFalse(listed["rig_control"]["hidden"])


if __name__ == "__main__":
    unittest.main()
