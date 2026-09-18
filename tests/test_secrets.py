"""Secrets must survive a save that was not about them.

The bug this guards: /api/config replaced the lookup password with the literal
"***" so the browser had something to show, the settings form loaded that into
the field, and the next Save wrote "***" back as the password. Opening
settings and pressing Save destroyed it.

Run with:  python -m unittest discover -s tests
"""
import json
import tempfile
import unittest
from pathlib import Path

from hamdash.app import create_app
from hamdash.config import SECRETS, Config


def a_host():
    home = Path(tempfile.mkdtemp(prefix="hamdash-secret-"))
    (home / "config.json").write_text(json.dumps({
        "station": {"callsign": "VA7SLL", "grid": "CN89kg"},
        "radio": {"mock": True},
        "callsign_lookup": {"provider": "auto", "user": "me",
                            "password": "lookuppass"},
        "logbook_sync": {"lotw_user": "va7sll", "lotw_password": "realpass",
                         "qrz_api_key": "REAL-KEY-1234"},
    }), encoding="utf-8")
    (home / "layout.json").write_text("[]", encoding="utf-8")
    cfg = Config(home)
    app, _shutdown = create_app(cfg, port=0)
    return cfg, app.test_client()


class TestSecretsAreWriteOnly(unittest.TestCase):
    def setUp(self):
        self.cfg, self.client = a_host()

    def stored(self, section, key):
        return self.cfg.data[section][key]

    def test_they_never_reach_the_browser(self):
        got = self.client.get("/api/config").get_json()
        for section, key in SECRETS:
            with self.subTest(secret=f"{section}.{key}"):
                self.assertEqual(got[section][key], "")

    def test_no_placeholder_is_sent_that_could_be_saved_back(self):
        """"***" was a real value as far as the next save was concerned."""
        body = json.dumps(self.client.get("/api/config").get_json())
        self.assertNotIn("***", body)
        self.assertNotIn("lookuppass", body)
        self.assertNotIn("REAL-KEY-1234", body)

    def test_it_says_which_are_set_without_saying_what(self):
        got = self.client.get("/api/config").get_json()
        self.assertTrue(got["secrets_set"]["callsign_lookup.password"])
        self.assertTrue(got["secrets_set"]["logbook_sync.qrz_api_key"])

    def test_saving_other_settings_leaves_them_alone(self):
        """The reported bug, end to end."""
        self.client.put("/api/config", json={
            "station": {"callsign": "VE7XYZ", "grid": "CN89"},
            "callsign_lookup": {"provider": "hamdb", "user": "me"},
            "logbook_sync": {"lotw_user": "va7sll"},
        })
        self.assertEqual(self.stored("callsign_lookup", "password"), "lookuppass")
        self.assertEqual(self.stored("logbook_sync", "lotw_password"), "realpass")
        self.assertEqual(self.stored("logbook_sync", "qrz_api_key"), "REAL-KEY-1234")
        self.assertEqual(self.cfg.data["station"]["callsign"], "VE7XYZ")

    def test_an_empty_field_means_untouched_not_erased(self):
        """What the form sends for a box the operator never typed in."""
        self.client.put("/api/config", json={
            "callsign_lookup": {"provider": "auto", "user": "me", "password": ""},
            "logbook_sync": {"qrz_api_key": "", "lotw_password": "   "},
        })
        self.assertEqual(self.stored("callsign_lookup", "password"), "lookuppass")
        self.assertEqual(self.stored("logbook_sync", "qrz_api_key"), "REAL-KEY-1234")
        self.assertEqual(self.stored("logbook_sync", "lotw_password"), "realpass")

    def test_a_new_value_replaces(self):
        self.client.put("/api/config",
                        json={"logbook_sync": {"qrz_api_key": "NEW-KEY\n"}})

    def test_null_clears(self):
        """The only way to remove one, so it cannot happen by accident."""
        self.client.put("/api/config",
                        json={"logbook_sync": {"qrz_api_key": None}})
        self.assertEqual(self.stored("logbook_sync", "qrz_api_key"), "")

    def test_a_saved_secret_still_works_afterwards(self):
        """Not just stored: the service built from it must get the real one."""
        self.client.put("/api/config",
                        json={"callsign_lookup": {"provider": "hamqth",
                                                  "user": "me"}})
        got = self.client.get("/api/config").get_json()
        self.assertEqual(got["callsign_lookup"]["provider"], "hamqth")
        self.assertEqual(self.stored("callsign_lookup", "password"), "lookuppass")


class TestPastedCredentials(unittest.TestCase):
    """A clipboard adds whitespace to a credential; the service that gets it
    then answers "username or password incorrect", which sends you looking at
    the password rather than at the paste."""

    def setUp(self):
        self.cfg, self.client = a_host()

    def stored(self, section, key):
        return self.cfg.data[section][key]

    def test_a_pasted_key_loses_its_trailing_newline(self):
        self.client.put("/api/config",
                        json={"logbook_sync": {"qrz_api_key": "NEW-KEY\n"}})
        self.assertEqual(self.stored("logbook_sync", "qrz_api_key"), "NEW-KEY")

    def test_a_username_loses_surrounding_space(self):
        self.client.put("/api/config",
                        json={"callsign_lookup": {"user": "  VA7SLL "}})
        self.assertEqual(self.stored("callsign_lookup", "user"), "VA7SLL")

    def test_zero_width_characters_go_too(self):
        """A rich-text copy carries these and nothing shows them on screen."""
        self.client.put("/api/config",
                        json={"callsign_lookup": {"password": "\ufeffpw\u200b"}})
        self.assertEqual(self.stored("callsign_lookup", "password"), "pw")

    def test_the_middle_is_left_alone(self):
        self.client.put("/api/config",
                        json={"callsign_lookup": {"password": " two words "}})
        self.assertEqual(self.stored("callsign_lookup", "password"), "two words")

    def test_whitespace_only_is_not_a_new_secret(self):
        """It must read as "I was not editing this", not as a blank password."""
        self.client.put("/api/config",
                        json={"callsign_lookup": {"password": "   "}})
        self.assertEqual(self.stored("callsign_lookup", "password"), "lookuppass")

    def test_null_still_clears(self):
        self.client.put("/api/config",
                        json={"callsign_lookup": {"password": None}})
        self.assertEqual(self.stored("callsign_lookup", "password"), "")


if __name__ == "__main__":
    unittest.main()


class TestTileSecrets(unittest.TestCase):
    """Same rule for a card's own secret settings, like the map's weather key.

    One difference: the card that owns the tile does get the real value - it
    has to build a tile URL out of it. Everyone else, the settings form
    included, only learns that one is set.
    """

    def setUp(self):
        home = Path(tempfile.mkdtemp(prefix="hamdash-tile-secret-"))
        (home / "config.json").write_text('{"radio": {"mock": true}}',
                                          encoding="utf-8")
        (home / "layout.json").write_text(json.dumps([
            {"uid": "m1", "card": "path_map", "x": 0, "y": 0, "w": 6, "h": 4,
             "settings": {"owm_key": "WEATHER-KEY", "graticule": True}},
        ]), encoding="utf-8")
        self.cfg = Config(home)
        app, _ = create_app(self.cfg, port=0)
        self.client = app.test_client()

    def stored(self):
        return (self.cfg.tile("m1") or {}).get("settings", {}).get("owm_key")

    def test_the_board_is_not_shown_the_key(self):
        got = self.client.get("/api/tiles/m1/settings").get_json()
        self.assertEqual(got["values"]["owm_key"], "")
        self.assertTrue(got["secrets_set"]["owm_key"])

    def test_saving_other_tile_settings_keeps_it(self):
        self.client.put("/api/tiles/m1/settings",
                        json={"values": {"graticule": False, "owm_key": ""}})
        self.assertEqual(self.stored(), "WEATHER-KEY")
        self.assertIs(self.cfg.tile("m1")["settings"]["graticule"], False)

    def test_a_new_key_replaces(self):
        self.client.put("/api/tiles/m1/settings",
                        json={"values": {"owm_key": "OTHER-KEY"}})
        self.assertEqual(self.stored(), "OTHER-KEY")

    def test_non_secret_settings_are_unaffected(self):
        got = self.client.get("/api/tiles/m1/settings").get_json()
        self.assertIs(got["values"]["graticule"], True)
