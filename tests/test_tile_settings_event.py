"""What `card.settings_changed` says when a tile's settings are saved.

Reported as "Listen is stuck on the test tone and will not follow the radio's
sound card". The rig tile stores only what the operator changed - its radio,
and now its memories - and the announcement carried exactly that: no manifest
defaults. The page took it as its settings, its `device` (default "rig")
vanished, and the audio engine, asked without one, used its own default -
the test tone. Before memories this only happened on a settings-sheet save,
which stores every field; a memory save stores one.

The same payload is in `bus.ALWAYS`, so every card page on the board gets it,
and it carried a tile's secrets raw (the Map card's weather key). It is now
what reading the settings returns to a card that is not the owner: defaults
filled in, secrets blanked, `secrets_set` saying which exist. The board gets
`stored` - what is actually on the tile, secrets left out - for its own copy.

Run with:  python -m unittest discover -s tests
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hamdash import app as happ
from hamdash.app import create_app
from hamdash.config import Config


class TestTheAnnouncement(unittest.TestCase):
    def setUp(self):
        home = Path(tempfile.mkdtemp(prefix="hamdash-tse-"))
        self.addCleanup(shutil.rmtree, home, True)
        (home / "config.json").write_text(json.dumps(
            {"wsjtx": {"enabled": False}}), encoding="utf-8")
        tile = lambda uid, card, settings: {  # noqa: E731
            "uid": uid, "x": 0, "y": 0, "w": 6, "h": 5, "active": uid,
            "cards": [{"uid": uid, "card": card, "settings": settings}]}
        (home / "layout.json").write_text(json.dumps([
            tile("rig1", "rig_control", {"radio": "r900"}),
            tile("map1", "path_map", {"owm_key": "SECRET-KEY-123"}),
        ]), encoding="utf-8")
        app, _ = create_app(Config(home), port=8765)
        self.c = app.test_client()
        self.sent = []
        patch = mock.patch.object(
            happ.BUS, "publish",
            side_effect=lambda topic, payload, **kw: self.sent.append((topic, payload)))
        patch.start()
        self.addCleanup(patch.stop)

    def said(self, uid):
        got = [p for t, p in self.sent
               if t == "card.settings_changed" and p.get("uid") == uid]
        self.assertTrue(got, "nothing was announced")
        return got[-1]

    def test_a_memory_save_still_says_listen_follows_the_radio(self):
        r = self.c.put("/api/tiles/rig1/settings",
                       json={"values": {"memories": [
                           {"label": "FT8", "hz": 14074000, "mode": "USB"}]}})
        self.assertEqual(r.status_code, 200)
        p = self.said("rig1")
        self.assertEqual(p["values"]["device"], "rig",
                         "the default fell out, so Listen played the test tone")
        self.assertEqual(p["values"]["radio"], "r900")
        self.assertEqual(len(p["values"]["memories"]), 1)

    def test_what_is_stored_is_kept_apart_from_the_defaults(self):
        """The board keeps a copy of what is on the tile and writes it back
        with every layout save. Defaults in it would be pinned into
        layout.json, so a later change of default would never arrive."""
        self.c.put("/api/tiles/rig1/settings", json={"values": {"memories": []}})
        p = self.said("rig1")
        self.assertNotIn("device", p["stored"])
        self.assertEqual(p["stored"]["radio"], "r900")

    def test_no_card_page_is_sent_a_secret(self):
        self.c.put("/api/tiles/map1/settings", json={"values": {"zoom": 3}})
        p = self.said("map1")
        self.assertNotIn("SECRET-KEY-123", json.dumps(p))
        self.assertEqual(p["values"]["owm_key"], "")
        self.assertIs(p["secrets_set"]["owm_key"], True)

    def test_the_secret_is_still_stored(self):
        self.c.put("/api/tiles/map1/settings", json={"values": {"zoom": 3}})
        layout = json.loads(self.c.get("/api/layout").data)
        tiles = layout.get("tiles", layout)
        entry = next(c for t in tiles for c in t["cards"] if c["uid"] == "map1")
        self.assertEqual(entry["settings"]["owm_key"], "SECRET-KEY-123")


if __name__ == "__main__":
    unittest.main()
