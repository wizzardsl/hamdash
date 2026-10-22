"""The window opens whatever the radio is doing.

1.0.0 on Windows opened nothing, said nothing and logged nothing. The
launcher waited for `/api/health` with 0.5 s probes; health asked the rig
inline; with flrig not running that took 2-4 s, because Windows spends
about two seconds on each refused loopback connect. Every probe timed out,
`main()` returned 1 after fifteen seconds, and the one line saying so went
to stderr - which is devnull in a windowed build.

Three faults, each enough on its own, so each has its own test: the probe
asks the server, not the radio; health does not wait on the radio
(invariant 4); and a refusal to start reaches the log.

Run with:  python -m unittest discover -s tests
"""
import ast
import json
import logging
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import run as runpy                                     # noqa: E402
from hamdash.app import create_app                      # noqa: E402
from hamdash.config import Config                       # noqa: E402
from hamdash.services import rigs                       # noqa: E402

RUN_PY = (ROOT / "run.py").read_text(encoding="utf-8")

SLOW = 3.0          # what an unreachable flrig cost per state() on Windows


def a_host():
    home = Path(tempfile.mkdtemp(prefix="hamdash-probe-"))
    (home / "config.json").write_text(json.dumps(
        {"station": {"callsign": "VA7SLL"}, "radio": {"mock": True}}),
        encoding="utf-8")
    (home / "layout.json").write_text("[]", encoding="utf-8")
    app, shutdown = create_app(Config(home), port=8765)
    return home, app, shutdown


def slow_state(self):
    """A radio that is not there: no answer for SLOW seconds."""
    time.sleep(SLOW)
    return {"ok": False, "error": "flrig is not running"}


class TestHealthDoesNotWaitOnTheRadio(unittest.TestCase):
    def setUp(self):
        home, self.app, shutdown = a_host()
        self.addCleanup(shutil.rmtree, home, True)
        self.addCleanup(shutdown)
        self.client = self.app.test_client()

    def test_a_slow_radio_does_not_slow_health(self):
        with mock.patch.object(rigs.RigLink, "state", slow_state):
            t = time.monotonic()
            r = self.client.get("/api/health")
            took = time.monotonic() - t
        self.assertEqual(r.status_code, 200)
        self.assertLess(took, 1.0, "health asked the radio inline")
        self.assertIn("radio", r.get_json())

    def test_it_reports_what_the_poller_last_saw(self):
        for last, want in ((None, None), ({"ok": False}, False),
                           ({"ok": True}, True)):
            with self.subTest(last=last), \
                    mock.patch.object(rigs.RigManager, "last_state",
                                      lambda self, v=last: v):
                self.assertEqual(
                    self.client.get("/api/health").get_json()["radio"], want)

    def test_the_poller_keeps_its_reading(self):
        mgr = rigs.RigManager.__new__(rigs.RigManager)
        mgr._links, mgr._active = {}, "a"
        self.assertIsNone(mgr.last_state(), "nothing polled is not an answer")
        link = rigs.RigLink(None, {"id": "a", "backend": "mock"})
        link.last = {"ok": True, "freq": 7041000}
        mgr._links["a"] = link
        self.assertEqual(mgr.last_state()["freq"], 7041000)


class TestTheLauncherAsksTheServer(unittest.TestCase):
    """End to end: a real waitress, a radio that takes SLOW seconds."""

    def test_wait_for_succeeds_with_the_radio_unreachable(self):
        from waitress import create_server
        home, app, shutdown = a_host()
        self.addCleanup(shutil.rmtree, home, True)
        self.addCleanup(shutdown)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with mock.patch.object(rigs.RigLink, "state", slow_state):
            server = create_server(app, host="127.0.0.1", port=port)
            threading.Thread(target=server.run, daemon=True).start()
            self.addCleanup(server.close)
            t = time.monotonic()
            ok = runpy.wait_for(f"http://127.0.0.1:{port}", timeout=5.0)
            took = time.monotonic() - t
        self.assertTrue(ok, "the launcher gave up on a server that was up")
        self.assertLess(took, SLOW)

    def test_ping_touches_nothing(self):
        home, app, shutdown = a_host()
        self.addCleanup(shutil.rmtree, home, True)
        self.addCleanup(shutdown)
        with mock.patch.object(rigs.RigLink, "state",
                               side_effect=AssertionError("asked the rig")):
            r = app.test_client().get("/api/ping")
        self.assertEqual(r.status_code, 200)

    def test_the_probe_is_not_health(self):
        """Code, not prose: the docstring says why it is not health."""
        fn = _func("wait_for")
        body = fn.body[1:] if ast.get_docstring(fn) else fn.body
        strings = [n.value for b in body for n in ast.walk(b)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertIn("/api/ping", strings)
        self.assertNotIn("/api/health", strings)


def _func(name):
    for node in ast.walk(ast.parse(RUN_PY)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"run.py has no {name}()")


class TestARefusalIsLogged(unittest.TestCase):
    def test_fail_logs_and_returns_the_code(self):
        with self.assertLogs("hamdash", logging.ERROR) as got:
            self.assertEqual(runpy.fail(1, "the server did not answer"), 1)
        self.assertIn("the server did not answer", got.output[0])

    def test_every_failing_return_in_main_goes_through_fail(self):
        """`return 1` with a print in front of it is invisible in a
        windowed build. A non-zero literal must be `return fail(...)`."""
        bad = [n.lineno for n in ast.walk(_func("main"))
               if isinstance(n, ast.Return)
               and isinstance(n.value, ast.Constant) and n.value.value]
        self.assertEqual(bad, [], "a bare non-zero return in main()")

    def test_logging_is_up_before_the_first_refusal(self):
        main = ast.get_source_segment(RUN_PY, _func("main"))
        self.assertLess(main.index("setup_logging("),
                        main.index("why_not_bindable("),
                        "a taken port would be refused before the log exists")


if __name__ == "__main__":
    unittest.main()
