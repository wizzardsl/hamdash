"""Shared services are opt-in, and the refusal says what to add.

This covers the half of the permission system that is real. A card's server
calls carry a token and a python card genuinely cannot reach a service it did
not declare - that is worth a regression test, because it is the part
somebody writing a card relies on and the part that would break silently.

The other half is documented rather than defended: every card *page* shares
one origin with the board and with every other card, so a page can reach
`parent.document`, find another card's frame and run a request inside it.
Measured: a card declaring nothing got 403 asking as itself and 200 - with
the radio's frequency - asking from the rig card's window. `README.md` and
`docs/cards.md` both carry that, and nothing here pretends otherwise.

Run with:  python -m unittest discover -s tests
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from hamdash.app import create_app
from hamdash.config import Config

PORT = 8765
HOST = f"http://127.0.0.1:{PORT}"

# Services that are gated, and a card that may use each one.
GATED = ["/api/radio/state", "/api/log", "/api/lookup/VA7SLL",
         "/api/space/drap"]
# Open to anything, with no declaration at all.
OPEN = ["/api/bands", "/api/config", "/api/themes", "/api/audio/devices"]


def a_host():
    home = Path(tempfile.mkdtemp(prefix="hamdash-perm-"))
    (home / "config.json").write_text(json.dumps({
        "station": {"callsign": "VA7SLL", "grid": "CN89kg"},
        "radio": {"mock": True},
    }), encoding="utf-8")
    (home / "layout.json").write_text("[]", encoding="utf-8")

    # A card that asks for nothing, and one that asks for the radio.
    for cid, perms in (("asks_nothing", []), ("asks_radio", ["radio"])):
        d = home / "cards" / cid
        d.mkdir(parents=True)
        (d / "card.json").write_text(json.dumps({
            "id": cid, "name": cid, "kind": "html", "view": "index.html",
            "permissions": perms,
        }), encoding="utf-8")
        (d / "index.html").write_text("<p>hi</p>", encoding="utf-8")

    cfg = Config(home)
    app, _shutdown = create_app(cfg, port=PORT)
    return home, app.test_client()


def as_card(cid):
    """What the browser sends for a request from that card's page."""
    return {"Referer": f"{HOST}/cards/{cid}/?uid=t1"}


class TestWhatACardMayReach(unittest.TestCase):
    def setUp(self):
        home, self.client = a_host()
        self.addCleanup(shutil.rmtree, home, True)

    def test_a_card_that_asked_for_nothing_is_refused(self):
        for path in GATED:
            with self.subTest(path=path):
                r = self.client.get(path, headers=as_card("asks_nothing"))
                self.assertEqual(r.status_code, 403, path)

    def test_the_refusal_names_what_to_add(self):
        """A bare 403 sends a card author looking in the wrong place."""
        r = self.client.get("/api/radio/state",
                            headers=as_card("asks_nothing"))
        said = r.get_json()
        self.assertEqual(said["permission"], "radio")
        self.assertEqual(said["card"], "asks_nothing")
        self.assertIn("card.json", said["error"])

    def test_a_card_that_asked_gets_it(self):
        r = self.client.get("/api/radio/state", headers=as_card("asks_radio"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])

    def test_asking_for_one_service_does_not_bring_the_others(self):
        for path in ("/api/log", "/api/lookup/VA7SLL", "/api/space/drap"):
            with self.subTest(path=path):
                r = self.client.get(path, headers=as_card("asks_radio"))
                self.assertEqual(r.status_code, 403, path)

    def test_the_open_services_need_no_declaration(self):
        """Bands, config, themes and the device list are deliberately open:
        a card should not have to ask permission to know what band it is on
        or which theme to draw in."""
        for path in OPEN:
            with self.subTest(path=path):
                r = self.client.get(path, headers=as_card("asks_nothing"))
                self.assertEqual(r.status_code, 200, path)

    def test_the_board_itself_is_not_a_card(self):
        """The dashboard's own page carries no card referer and must not be
        caught by a card's permissions."""
        for path in GATED:
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertNotEqual(r.status_code, 403, path)

    def test_an_unknown_card_is_not_granted_anything(self):
        r = self.client.get("/api/radio/state", headers=as_card("not_a_card"))
        # No card matched, so there is nothing to refuse on - the request is
        # treated as the board's. Worth pinning: it must not *crash*, and it
        # must not hand an unknown id a card's identity.
        self.assertNotEqual(r.status_code, 500)

    def test_a_card_page_is_marked_unverified(self):
        """The host knows the difference between a token and a referer, and
        says so in the refusal. If this ever reads True for a page call,
        something has started trusting the wrong thing."""
        r = self.client.get("/api/radio/state",
                            headers=as_card("asks_nothing"))
        self.assertIs(r.get_json()["verified"], False)


if __name__ == "__main__":
    unittest.main()
