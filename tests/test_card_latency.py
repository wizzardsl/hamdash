"""What a card request costs, and the three things that made it cost more.

Every card in this tree is polled - that is the house pattern, because a
stream holds a server thread (invariant 4). So the per-request cost of the
card path is not a micro-optimisation, it is the frame rate of the
spectrum, the decoder and the spots card, and it is the difference between
audio that plays and audio that stutters.

Three separate things were each costing about a Windows timer tick
(15.6 ms), measured while spiking remote audio:

  1. the proxy built a fresh requests.Session per call   15.6 -> 1.1 ms
  2. it returned a generator, which waitress pays a tick for
                                                         15.1 -> 1.1 ms
  3. a *failed* card.settings() lookup was retried on every request
                                                         30.8 -> 2.5 ms

End to end that was 34.5 ms median / 43.7 p95 per card request, and is
2.3 / 3.5 after. None of it is visible from reading the code, and all
three come back silently if somebody tidies them - hence this file.

Timings are not asserted: a test that fails on a slow CI runner teaches
people to ignore it. What is asserted is the *shape* that produced them.

Run with:  python -m unittest discover -s tests
"""
import threading
import unittest
from pathlib import Path

import requests

from hamdash import app as happ

ROOT = Path(__file__).resolve().parent.parent
APP_SRC = (ROOT / "hamdash" / "app.py").read_text(encoding="utf-8")
SDK_SRC = (ROOT / "hamdash" / "sdk" / "__init__.py").read_text(encoding="utf-8")


class TestTheProxyKeepsItsConnections(unittest.TestCase):
    def test_it_does_not_build_a_session_per_request(self):
        """`requests.request(...)` builds a Session, a connection pool and
        adapters and throws them away again - a full timer tick on
        Windows, paid on every request to every card.

        The call site is what matters, not the prose about it, so this
        reads the body of _proxy rather than the whole file.
        """
        body = APP_SRC[APP_SRC.index("    def _proxy(worker, sub):"):]
        body = body[:body.index("\n    # ---------------- layout")]
        code = "\n".join(l for l in body.splitlines()
                         if not l.strip().startswith("#"))
        self.assertNotIn("requests.request(", code,
                         "the card proxy is building a Session per call "
                         "again; use the pooled _session()")
        self.assertIn("_session().request(", code)

    def test_the_session_is_per_thread(self):
        """requests does not promise a Session is safe across threads and
        waitress hands every request to a different one."""
        self.assertIn("threading.local()", APP_SRC)
        self.assertIn("_session()", APP_SRC)

    def test_a_pooled_session_really_is_reused(self):
        """Guards the mechanism, not the timing: thread-local means same
        object within a thread and a different one across threads."""
        local = threading.local()

        def get():
            s = getattr(local, "session", None)
            if s is None:
                s = local.session = requests.Session()
            return s

        self.assertIs(get(), get())
        other = []
        t = threading.Thread(target=lambda: other.append(get()))
        t.start()
        t.join()
        self.assertIsNot(get(), other[0])


class TestTheProxyDoesNotStreamSmallReplies(unittest.TestCase):
    def test_a_small_reply_is_sent_as_bytes(self):
        """A generator response costs waitress a 15.6 ms timer tick -
        measured at 15.12 ms against 1.07 for the same bytes returned
        directly. Almost every card reply is a few kB of JSON."""
        self.assertIn("STREAM_CHUNK", APP_SRC)
        self.assertIn("if len(head) < STREAM_CHUNK:", APP_SRC)

    def test_a_big_reply_still_streams(self):
        """Holding a card page with images in memory to save a tick is the
        wrong trade, so the generator path has to survive."""
        self.assertIn("yield from it", APP_SRC)

    def test_the_threshold_is_big_enough_to_catch_json_replies(self):
        self.assertGreaterEqual(happ.STREAM_CHUNK, 16384)


class TestAFailedSettingsLookupBacksOff(unittest.TestCase):
    """The same rule as the callsign lookup's refusals, in the SDK.

    A card asked about a uid the host will not answer for - a page opened
    outside the board, a tile that has gone - used to pay a full round trip
    to the host on every request for the life of the process, and print a
    line about it each time. It is not a crash and nothing reports it; the
    card just answers slowly for ever.
    """

    def setUp(self):
        from hamdash.sdk import Card
        self.calls = []

        class FakeHost:
            def tile_settings(_self, uid):
                self.calls.append(uid)
                raise requests.ConnectionError("nope")

        card = Card.__new__(Card)
        card.host = FakeHost()
        card._settings = {}
        card._settings_ttl = 5.0
        card._settings_failed = {}
        card._settings_retry = 2.0
        card.manifest = {"settings": {"x": {"default": 1}}}
        self.card = card

    def test_the_host_is_asked_once_not_once_per_request(self):
        for _ in range(50):
            self.card.settings("t1")
        self.assertEqual(len(self.calls), 1,
                         f"asked the host {len(self.calls)} times for one "
                         f"failing lookup")

    def test_it_still_answers_with_the_defaults(self):
        self.assertEqual(self.card.settings("t1"), {"x": 1})

    def test_it_does_try_again_eventually(self):
        """Remembered, not given up on - a tile can appear."""
        self.card.settings("t1")
        self.card._settings_failed["t1"] -= 99
        self.card.settings("t1")
        self.assertEqual(len(self.calls), 2)

    def test_the_source_says_why(self):
        self.assertIn("_settings_failed", SDK_SRC)


class TestCardsRunOnAProperServer(unittest.TestCase):
    def test_a_card_does_not_serve_on_the_development_server(self):
        """Werkzeug's dev server cost 7.7 ms median and 26.4 ms p95 per
        request against waitress' 1.0 and 1.5 on the same Flask app. The
        p95 is the one that hurts: a polled card turns jitter into
        stutter. waitress is already a dependency and already what the
        host runs on."""
        run = SDK_SRC[SDK_SRC.index("def run(self)"):]
        run = run[:run.index("def _warn_unreachable")]
        self.assertIn("waitress", run)
        before = run.index("waitress")
        self.assertIn("app.run(", run)
        self.assertGreater(run.index("app.run("), before,
                           "app.run() must be the ImportError fallback, "
                           "not the first choice")


if __name__ == "__main__":
    unittest.main()
