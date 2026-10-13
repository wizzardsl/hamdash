"""Cards made from a URL in the UI, and deleting them again.

`kind: url` has been in the manifest from the start; what is new is that
the manifest is now written from a *form*. That changes two things, and
both of them are what this file is about.

The address becomes an iframe `src` on the board's own origin, so a
`javascript:` or `file:` address is not a broken tile - it is code running
with the board's privileges, or a read of the operator's disk. It also
lands in a `<meta http-equiv="refresh">` attribute, where a quote closes
the attribute and the rest is markup.

And deleting is now a button rather than removing a folder, so what may be
deleted has to be decided somewhere other than the operator's memory: only
what hamdash wrote, only inside their cards directory. A card somebody
*wrote* is their code and is not deleted from a menu.

Run with:  python -m unittest discover -s tests
"""
import json
import tempfile
import unittest
from pathlib import Path

from hamdash import registry
from hamdash.app import create_app
from hamdash.config import Config

BUNDLED = Path(__file__).resolve().parent.parent / "plugins"


def a_host():
    home = Path(tempfile.mkdtemp(prefix="hamdash-urlcard-"))
    (home / "config.json").write_text(json.dumps({
        "station": {"callsign": "VA7SLL", "grid": "CN89kg"},
        "radio": {"mock": True},
    }), encoding="utf-8")
    (home / "layout.json").write_text("[]", encoding="utf-8")
    cfg = Config(home)
    app, _shutdown = create_app(cfg, port=0)
    return cfg, app.test_client()


class TestTheAddress(unittest.TestCase):
    def test_a_bare_host_gets_https(self):
        # What somebody actually types. Refusing it would be the app being
        # fussier than the address bar they copied it out of.
        self.assertEqual(registry.tidy_url("qrz.com"), "https://qrz.com")
        self.assertEqual(registry.tidy_url("  www.qrz.com/db  "),
                         "https://www.qrz.com/db")

    def test_http_and_https_are_left_alone(self):
        for u in ("http://10.0.1.5:8073", "https://sota.org.uk/x?a=1&b=2"):
            self.assertEqual(registry.tidy_url(u), u)

    def test_a_port_is_not_a_scheme(self):
        # `localhost:8080` is a host somebody typed, not a `localhost:`
        # scheme. The only thing that tells it from `javascript:` before
        # the colon is what follows it.
        self.assertEqual(registry.tidy_url("localhost:8080/spectrum"),
                         "https://localhost:8080/spectrum")

    def test_the_dangerous_schemes_are_refused_by_name(self):
        # The one that matters: this address goes into an iframe src on
        # the board's own origin.
        for bad in ("javascript:alert(document.cookie)",
                    "file:///C:/Users/Steven/config.json",
                    "data:text/html,<script>fetch('/api/config')</script>",
                    "vbscript:msgbox"):
            with self.assertRaises(registry.CardError) as caught:
                registry.tidy_url(bad)
            self.assertIn(bad.split(":")[0], str(caught.exception))

    def test_whitespace_in_an_address_is_refused(self):
        for bad in ("", "   ", "http://a b.com", "http://a.com\n<script>"):
            with self.assertRaises(registry.CardError):
                registry.tidy_url(bad)


class TestTheId(unittest.TestCase):
    def test_a_name_becomes_one_safe_segment(self):
        self.assertEqual(registry.slug("DX Cluster!"), "dx_cluster")
        self.assertEqual(registry.slug("../../etc"), "etc")
        self.assertEqual(registry.slug("a/b\\c"), "a_b_c")

    def test_a_collision_takes_the_next_number(self):
        # Not tidiness: a user card beats a bundled one of the same id, so
        # a page called "Logbook" would quietly replace the logbook card.
        self.assertEqual(registry.free_id("logbook", {"logbook"}), "logbook_2")
        self.assertEqual(registry.free_id("logbook", {"logbook", "logbook_2"}),
                         "logbook_3")

    def test_an_id_that_is_a_path_is_refused(self):
        for bad in ("..", "../evil", "a/b", "", "C:\\x"):
            with self.assertRaises(registry.CardError):
                registry.safe_id(bad)


class TestMakingOne(unittest.TestCase):
    def setUp(self):
        self.cfg, self.client = a_host()

    def post(self, **body):
        return self.client.post("/api/cards", json=body)

    def test_it_writes_a_manifest_that_reads_back_as_a_card(self):
        r = self.post(name="DX Cluster", url="dxsummit.fi")
        self.assertEqual(r.status_code, 201)
        card = r.get_json()
        self.assertEqual(card["kind"], "url")
        self.assertEqual(card["url"], "https://dxsummit.fi")
        self.assertEqual(card["created_by"], "hamdash")
        self.assertIsNone(card["error"])

        written = self.cfg.user_plugins / card["id"] / "card.json"
        self.assertTrue(written.exists())
        self.assertEqual(json.loads(written.read_text())["name"], "DX Cluster")

    def test_it_shows_up_in_the_card_list_without_a_rescan(self):
        # The board renders its menu from this reply straight afterwards.
        cid = self.post(name="WebSDR", url="http://websdr.org").get_json()["id"]
        ids = [c["id"] for c in self.client.get("/api/cards").get_json()]
        self.assertIn(cid, ids)

    def test_two_of_the_same_name_do_not_collide(self):
        a = self.post(name="Weather", url="a.example").get_json()["id"]
        b = self.post(name="Weather", url="b.example").get_json()["id"]
        self.assertNotEqual(a, b)

    def test_it_cannot_shadow_a_bundled_card(self):
        made = self.post(name="Logbook", url="example.com").get_json()
        self.assertNotEqual(made["id"], "logbook")
        bundled = [c for c in self.client.get("/api/cards").get_json()
                   if c["id"] == "logbook"][0]
        self.assertEqual(bundled["kind"], "python")

    def test_a_bad_address_comes_back_as_something_to_fix(self):
        r = self.post(name="Sneaky", url="javascript:alert(1)")
        self.assertEqual(r.status_code, 400)
        self.assertIn("javascript", r.get_json()["error"])
        # And nothing was written.
        self.assertEqual(list(self.cfg.user_plugins.iterdir()), [])

    def test_a_card_needs_a_name(self):
        r = self.post(name="   ", url="example.com")
        self.assertEqual(r.status_code, 400)
        self.assertIn("name", r.get_json()["error"])


class TestDeletingOne(unittest.TestCase):
    def setUp(self):
        self.cfg, self.client = a_host()

    def test_one_the_board_made_can_be_deleted(self):
        cid = self.client.post("/api/cards", json={
            "name": "Scratch", "url": "example.com"}).get_json()["id"]
        d = self.cfg.user_plugins / cid
        self.assertTrue(d.exists())

        r = self.client.delete(f"/api/cards/{cid}")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(d.exists())
        ids = [c["id"] for c in self.client.get("/api/cards").get_json()]
        self.assertNotIn(cid, ids)

    def test_undo_puts_it_back_under_the_same_id(self):
        # Tiles refer to a card by its id, so a restored card with a new
        # one comes back with the board pointing at nothing.
        made = self.client.post("/api/cards", json={
            "name": "Scratch", "url": "example.com"}).get_json()
        self.client.delete(f"/api/cards/{made['id']}")
        again = self.client.post("/api/cards", json={
            "id": made["id"], "name": made["name"], "url": made["url"]})
        self.assertEqual(again.status_code, 201)
        self.assertEqual(again.get_json()["id"], made["id"])

    def test_a_bundled_card_is_refused(self):
        r = self.client.delete("/api/cards/logbook")
        self.assertEqual(r.status_code, 403)
        self.assertIn("logbook", r.get_json()["error"].lower())
        self.assertTrue((BUNDLED / "logbook" / "card.json").exists())

    def test_a_card_somebody_wrote_is_refused(self):
        # The marker is the test, not the folder: this one is in their own
        # cards directory and is still their code.
        d = self.cfg.user_plugins / "mine"
        d.mkdir()
        (d / "card.json").write_text(json.dumps({
            "id": "mine", "name": "Mine", "kind": "url",
            "url": "https://example.com"}), encoding="utf-8")
        self.client.get("/api/cards", query_string={"rescan": 1})

        r = self.client.delete("/api/cards/mine")
        self.assertEqual(r.status_code, 403)
        self.assertTrue((d / "card.json").exists())

    def test_a_manifest_cannot_claim_its_way_out_of_the_cards_folder(self):
        # `created_by` is set by whoever writes the file, so it cannot be
        # the only condition. Where the folder is, is the other one.
        bundled = registry.discover([BUNDLED])["web_embed"]
        bundled.created_by = registry.MADE_HERE
        why = registry.may_remove(bundled, self.cfg.user_plugins)
        self.assertIsNotNone(why)
        self.assertTrue((bundled.path / "card.json").exists())

    def test_deleting_something_that_is_not_there(self):
        self.assertEqual(self.client.delete("/api/cards/nope").status_code, 404)


class TestTheFramedPage(unittest.TestCase):
    """The redirect a `url` card serves, which carries the address."""

    def setUp(self):
        self.cfg, self.client = a_host()

    def test_the_address_is_escaped_into_the_meta_refresh(self):
        # A quote would close the attribute and the rest would be markup
        # on the board's own origin.
        d = self.cfg.user_plugins / "quoted"
        d.mkdir()
        (d / "card.json").write_text(json.dumps({
            "id": "quoted", "name": "Quoted", "kind": "url",
            "url": 'https://example.com/"><script>alert(1)</script>',
        }), encoding="utf-8")
        self.client.get("/api/cards", query_string={"rescan": 1})

        body = self.client.get("/cards/quoted/").get_data(as_text=True)
        self.assertNotIn("<script>", body)
        self.assertIn("&quot;", body)


if __name__ == "__main__":
    unittest.main()
