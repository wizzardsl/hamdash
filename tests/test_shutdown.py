"""Stopping hamdash properly, from the machine it runs on.

A kill cannot be caught. On Windows a headless instance has no window to
close and `taskkill` needs `/F`, which skips the signal handlers - so the
cards are not stopped and `rig.stop()` never runs, and that is the call
which takes a transmitter off the air. Running headless is the normal
case for a board somebody operates from another room, so "there is no
safe way to stop it" is not an edge case there.

The local-only rule is not a security control - nothing in this app is
authenticated, deliberately - but stopping somebody's station should not
be a URL a browser elsewhere in the house can load.

Run with:  python -m unittest discover -s tests
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from hamdash.app import _local_addresses, create_app
from hamdash.config import Config

ROOT = Path(__file__).resolve().parent.parent
RUN_PY = (ROOT / "run.py").read_text(encoding="utf-8")


def a_host(on_quit=None):
    home = Path(tempfile.mkdtemp(prefix="hamdash-stop-"))
    (home / "config.json").write_text(json.dumps(
        {"station": {"callsign": "VA7SLL"}, "radio": {"mock": True}}),
        encoding="utf-8")
    (home / "layout.json").write_text("[]", encoding="utf-8")
    app, _ = create_app(Config(home), port=8765, on_quit=on_quit)
    return home, app


class TestItDoesNotTakeTheProcessWithIt(unittest.TestCase):
    """The route must not end the process itself.

    It used to call os._exit(0) directly, which worked in production and
    meant this very suite killed itself the moment it exercised the
    route: exit code 0, no summary line, and every test after it simply
    not run. A green tick for a suite that stopped early is the worst
    way to be wrong about tests.
    """

    def test_the_app_does_not_hard_exit(self):
        """Code, not the prose about it - a comment naming os._exit is
        not a call to it, and a test that cannot tell them apart fails
        on its own explanation."""
        src = (ROOT / "hamdash" / "app.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("#"))
        self.assertNotIn("os._exit", code,
                         "ending the process is the launcher's business")

    def test_the_launcher_does(self):
        self.assertIn("os._exit(0)", RUN_PY)

    def test_the_whole_suite_still_reaches_the_end(self):
        """If this file runs at all, the route has been exercised above
        and the process survived it."""
        self.assertTrue(True)

class TestWhoMayAsk(unittest.TestCase):
    def setUp(self):
        self.called = []
        home, self.app = a_host(on_quit=lambda: self.called.append(True))
        self.addCleanup(shutil.rmtree, home, True)
        self.client = self.app.test_client()

    def test_this_machine_may(self):
        r = self.client.post("/api/shutdown",
                             environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["stopping"])

    def test_the_machines_own_lan_address_counts(self):
        """An instance bound to a LAN address does not listen on loopback
        at all, so a request made *on the host* arrives from that LAN
        address. Checking only for 127.0.0.1 would refuse the operator on
        their own machine - which is the one case this exists for."""
        mine = [a for a in _local_addresses()
                if a.replace(".", "").isdigit() and not a.startswith("127.")]
        if not mine:
            self.skipTest("this machine has no non-loopback IPv4 address")
        r = self.client.post("/api/shutdown",
                             environ_overrides={"REMOTE_ADDR": mine[0]})
        self.assertEqual(r.status_code, 200, f"refused {mine[0]}")

    def test_another_machine_may_not(self):
        r = self.client.post(
            "/api/shutdown",
            environ_overrides={"REMOTE_ADDR": "203.0.113.7"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.called, [], "it stopped anyway")

    def test_the_refusal_says_what_to_do_instead(self):
        r = self.client.post(
            "/api/shutdown",
            environ_overrides={"REMOTE_ADDR": "203.0.113.7"})
        said = r.get_json()["error"]
        self.assertIn("Ctrl-C", said)
        self.assertIn("--stop", said)

    def test_a_header_cannot_claim_to_be_local(self):
        """`remote_addr` is the socket peer, so a forwarding header does
        not move it - and waitress runs with
        `clear_untrusted_proxy_headers=True`, so one cannot be trusted
        into place either. Worth pinning: the check is only as good as
        the address being unforgeable."""
        r = self.client.post(
            "/api/shutdown",
            headers={"X-Forwarded-For": "127.0.0.1",
                     "X-Real-IP": "127.0.0.1"},
            environ_overrides={"REMOTE_ADDR": "203.0.113.7"})
        self.assertEqual(r.status_code, 403)
    def test_it_is_a_post(self):
        """A GET would let a link, an image or a prefetch stop the
        station."""
        r = self.client.get("/api/shutdown",
                            environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(r.status_code, 405)


class TestAnInstanceWithNoWayOut(unittest.TestCase):
    def test_it_says_so_rather_than_pretending(self):
        home, app = a_host(on_quit=None)
        self.addCleanup(shutil.rmtree, home, True)
        r = app.test_client().post(
            "/api/shutdown", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(r.status_code, 501)


class TestTheNoteItLeaves(unittest.TestCase):
    """`--stop` has to find the instance: the port is often not fixed (a
    native window takes whatever is free) and the bind address may not be
    loopback, so guessing is no good."""

    def test_run_py_writes_and_clears_it(self):
        self.assertIn("note_running(cfg, args.host, port, url)", RUN_PY)
        self.assertIn("clear_running(cfg)", RUN_PY)

    def test_stop_is_handled_before_the_host_is_built(self):
        """--stop must not take the log file or start a server, the same
        rule --run-card follows."""
        body = RUN_PY[RUN_PY.index("def main()"):]
        self.assertLess(body.index("if args.stop:"),
                        body.index("setup_logging(cfg)"),
                        "--stop is being handled after host setup")

    def test_the_quit_hook_is_passed_through(self):
        self.assertIn("on_quit=", RUN_PY)

    def test_headless_says_how_to_stop_it(self):
        """The gap this closes is invisible otherwise: there is no window
        and no obvious way out."""
        self.assertIn("--stop", RUN_PY)
        self.assertIn("Ctrl-C", RUN_PY)


class TestAPortThatIsTaken(unittest.TestCase):
    """A fixed --port is what a remote operator uses, and a taken one is
    what they meet. waitress fails deep: the last line is
    `PermissionError: [WinError 10013] An attempt was made to access a
    socket in a way forbidden by its access permissions`, which does not
    name the port and, on Windows, does not say the true thing - that
    something already has it."""

    def test_it_is_caught_before_anything_is_built(self):
        import socket
        import run as runpy
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
            why = runpy.why_not_bindable("127.0.0.1", port)
            self.assertIn("already using it", why)
        finally:
            s.close()

    def test_a_free_port_is_fine(self):
        import run as runpy
        self.assertEqual(runpy.why_not_bindable("127.0.0.1", 0), "")

    @unittest.skipIf(os.name == "nt", "Windows does not hold TIME_WAIT "
                     "against a bind; the option is POSIX-only")
    def test_a_port_left_in_time_wait_is_free(self):
        """Stopping an instance and starting it again on Linux said
        "something is already using it" for about a minute, with nothing
        listening: the connections it had served sat in TIME_WAIT, which
        a plain bind refuses and waitress - setting SO_REUSEADDR - does
        not. A real TIME_WAIT, made the way a server makes one: the
        listening side closes the connection first."""
        import socket
        import run as runpy
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        cli = socket.create_connection(("127.0.0.1", port))
        conn, _ = srv.accept()
        conn.close()            # the server closes first: it gets TIME_WAIT
        cli.recv(1)
        cli.close()
        srv.close()             # nothing is listening any more
        self.assertEqual(runpy.why_not_bindable("127.0.0.1", port), "")

    def test_the_message_says_what_to_do(self):
        self.assertIn("pick another with --port", RUN_PY)

if __name__ == "__main__":
    unittest.main()
