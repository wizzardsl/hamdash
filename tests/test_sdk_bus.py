"""A card's bus reader, which must outlive the stream it is reading.

The host ends every SSE stream after about a minute on purpose - an open one
holds a server thread (invariant 4) - and the last frame it sends is
`event: reconnect` with an empty payload. Taking a topic out of that raised
KeyError, which is not a RequestException, so it escaped the reader's loop
and ended the thread. The card went on serving requests and looked perfectly
healthy while receiving nothing from the bus ever again, including the
`card.settings_changed` it relies on to notice its own tile being edited.

Run with:  python -m unittest discover -s tests
"""
import json
import threading
import unittest
from unittest import mock

from hamdash.sdk import Card


def frames(*events):
    """An SSE body: the events, then the reconnect the host always sends."""
    out = [f"data: {json.dumps(e)}" for e in events]
    out += ["event: reconnect", "data: {}", ""]
    return out


class FakeStream:
    def __init__(self, lines, status=200):
        self._lines = lines
        self.status_code = status

    def iter_lines(self, decode_unicode=False):
        return iter(self._lines)

    def raise_for_status(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestTheReader(unittest.TestCase):
    def setUp(self):
        self.card = Card.__new__(Card)       # no Flask app, no environment
        self.card.host_url = "http://127.0.0.1:1"
        self.seen = []
        self.card._subs = [("demo.*", lambda t, p: self.seen.append((t, p)))]

    def run_once(self, bodies):
        """Drive `_listen` over the given stream bodies, then stop it."""
        rounds = iter(bodies)
        done = threading.Event()

        def get(url, **kw):
            try:
                return FakeStream(next(rounds))
            except StopIteration:
                done.set()
                raise SystemExit             # the only way out of a forever loop

        with mock.patch("hamdash.sdk.requests.get", side_effect=get), \
                mock.patch.object(threading.Event, "wait", lambda *a, **k: None):
            with self.assertRaises(SystemExit):
                self.card._listen()
        self.assertTrue(done.is_set())

    def test_an_event_reaches_its_handler(self):
        self.run_once([frames({"topic": "demo.hi", "payload": {"n": 1}})])
        self.assertEqual(self.seen, [("demo.hi", {"n": 1})])

    def test_the_reconnect_frame_does_not_end_the_reader(self):
        """It is sent at the end of every stream, so this is once a minute
        for the life of the card."""
        self.run_once([
            frames({"topic": "demo.one", "payload": {}}),
            frames({"topic": "demo.two", "payload": {}}),
            frames({"topic": "demo.three", "payload": {}}),
        ])
        self.assertEqual([t for t, _ in self.seen],
                         ["demo.one", "demo.two", "demo.three"])

    def test_a_malformed_frame_is_skipped_not_fatal(self):
        self.run_once([
            ["data: {this is not json", "data: null",
             'data: {"topic": "demo.ok", "payload": 1}'],
        ])
        self.assertEqual(self.seen, [("demo.ok", 1)])

    def test_a_handler_that_raises_does_not_end_the_reader(self):
        """One card's bad handler must not cost it every later event."""
        order = []
        self.card._subs = [
            ("demo.*", lambda t, p: order.append(t) or (_ for _ in ()).throw(
                RuntimeError("boom"))),
        ]
        self.run_once([frames({"topic": "demo.a", "payload": {}},
                              {"topic": "demo.b", "payload": {}})])
        self.assertEqual(order, ["demo.a", "demo.b"])

    def test_an_event_with_no_topic_is_ignored(self):
        self.run_once([["data: {}", 'data: {"payload": 2}']])
        self.assertEqual(self.seen, [])


if __name__ == "__main__":
    unittest.main()
