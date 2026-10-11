"""What you bind is not what you dial.

`--host 0.0.0.0` means "listen on every interface", which is how you reach
the board from another machine. It is not an address anything can connect
*to*: on Windows it is WinError 10049, "the requested address is not valid
in its context", measured rather than assumed.

Two things are built from that value and both of them dial it - the URL
handed to every card process as HAMDASH_HOST, and the page the native
window opens. A card that cannot call home does not crash: it gets
manifest defaults instead of its tile's settings and hears nothing from
the bus, so the board loads, the tiles paint, and the settings silently
do nothing. That is the quietest failure in this tree, which is why it
gets a test rather than a comment.

Run with:  python -m unittest discover -s tests
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from hamdash.app import create_app, reachable
from hamdash.config import Config

RUN_PY = (Path(__file__).resolve().parent.parent / "run.py").read_text(
    encoding="utf-8")


class TestReachable(unittest.TestCase):
    def test_the_wildcards_become_loopback(self):
        for host in ("0.0.0.0", "::", "[::]", "*", "", "  "):
            with self.subTest(host=host):
                self.assertEqual(reachable(host), "127.0.0.1")

    def test_a_real_address_is_left_alone(self):
        """Binding one interface is the sensible way to do this, and that
        address is both bindable and dialable - it must not be rewritten
        to loopback, or a card would call the wrong host."""
        for host in ("127.0.0.1", "10.0.1.212", "192.168.1.50", "localhost"):
            with self.subTest(host=host):
                self.assertEqual(reachable(host), host)


class TestTheCardsCanCallHome(unittest.TestCase):
    """The supervisor's base URL is HAMDASH_HOST for every card process."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="hamdash-bind-"))
        (self.home / "config.json").write_text(json.dumps(
            {"station": {"callsign": "VA7SLL"}, "radio": {"mock": True}}),
            encoding="utf-8")
        (self.home / "layout.json").write_text("[]", encoding="utf-8")
        self.addCleanup(shutil.rmtree, self.home, True)

    def host_url(self, host):
        app, _ = create_app(Config(self.home), host=host, port=8765)
        return app.config["SUP"].base if "SUP" in app.config else None

    def test_binding_everywhere_still_gives_cards_a_real_address(self):
        from hamdash import app as happ
        seen = {}
        real = happ.Supervisor

        class Spy(real):
            def __init__(self, base, **kw):
                seen["base"] = base
                super().__init__(base, **kw)

        happ.Supervisor = Spy
        try:
            create_app(Config(self.home), host="0.0.0.0", port=8765)
        finally:
            happ.Supervisor = real
        self.assertNotIn("0.0.0.0", seen["base"],
                         "cards would be told to dial 0.0.0.0, which is not "
                         "an address they can connect to")
        self.assertEqual(seen["base"], "http://127.0.0.1:8765")


class TestTheWindowOpensSomethingThatExists(unittest.TestCase):
    def test_run_py_does_not_build_the_url_from_the_bind_address(self):
        """A native window pointed at http://0.0.0.0:8765 shows an error
        page, and `--browser` prints a URL that does not work."""
        self.assertNotIn('url = f"http://{args.host}:{port}"', RUN_PY)
        self.assertIn("reachable(args.host)", RUN_PY)

    def test_run_py_still_binds_what_it_was_asked_to(self):
        """The fix must not quietly stop the app listening on all
        interfaces - that is the whole point of passing 0.0.0.0."""
        self.assertIn("host=args.host", RUN_PY)


if __name__ == "__main__":
    unittest.main()
