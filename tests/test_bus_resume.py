"""A reconnecting stream resumes; it is not sent the snapshot again.

The bug this guards: every event stream ends after STREAM_SECONDS on purpose
(invariant 4) and the client reconnects - and every connect, reconnects
included, began with the last event on every topic. Right for a newcomer,
which needs the current radio state; wrong once a minute for ever after.
The last `log.qso_added` arrived again every 55 seconds: the logbook card
jumped back to page one, the path map reloaded its contacts, and the rig
card's handler printed one W7G contact some 150 times in an evening, which
is how it was found - in the console, with the log itself perfectly fine.

Browsers already send `Last-Event-ID` when an EventSource reconnects, once
the events carry an `id`; the SDK now sends the same header.

Run with:  python -m unittest discover -s tests
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from hamdash import bus as busmod
from hamdash.bus import BUS


def parse(body: str):
    """SSE text -> (events, last id seen), the way EventSource reads it."""
    events, last = [], None
    for frame in body.split("\n\n"):
        data = None
        for line in frame.split("\n"):
            if line.startswith("id: "):
                last = int(line[4:])
            elif line.startswith("data: "):
                data = line[6:]
        if data and data != "{}":
            events.append(json.loads(data))
    return events, last


class Host(unittest.TestCase):
    def setUp(self):
        home = Path(tempfile.mkdtemp(prefix="hamdash-resume-"))
        (home / "config.json").write_text(json.dumps({
            "radio": {"mock": True}, "wsjtx": {"enabled": False}}),
            encoding="utf-8")
        (home / "layout.json").write_text("[]", encoding="utf-8")
        from hamdash.app import create_app
        from hamdash.config import Config
        app, self._shutdown = create_app(Config(home), port=0)
        self.client = app.test_client()
        for name, value in (("STREAM_SECONDS", 0.3), ("KEEPALIVE_SECONDS", 0.05)):
            p = mock.patch.object(busmod, name, value)
            p.start()
            self.addCleanup(p.stop)
        # A topic of this test's own: BUS is the process-wide one.
        self.topic = f"log.resume_{id(self)}"

    def tearDown(self):
        try:
            self._shutdown()
        except Exception:
            pass

    def connect(self, last_id=None):
        headers = {} if last_id is None else {"Last-Event-ID": str(last_id)}
        r = self.client.get(f"/api/events?topics={self.topic}", headers=headers)
        return parse(r.get_data(as_text=True))


class TestANewcomer(Host):
    def test_gets_the_current_state(self):
        BUS.publish(self.topic, {"call": "W7G"})
        events, _ = self.connect()
        self.assertEqual([e["payload"]["call"] for e in events], ["W7G"])

    def test_every_event_carries_an_id(self):
        BUS.publish(self.topic, {"call": "W7G"})
        events, last = self.connect()
        self.assertTrue(all("id" in e for e in events))
        self.assertIsNotNone(last)


class TestAReconnect(Host):
    def test_is_not_sent_what_it_already_had(self):
        """The reported case: one contact, delivered again every minute."""
        BUS.publish(self.topic, {"call": "W7G"})
        first, last = self.connect()
        self.assertEqual(len(first), 1)
        again, _ = self.connect(last)
        self.assertEqual(again, [], "a reconnect replayed the last contact")

    def test_three_reconnects_in_a_row_stay_quiet(self):
        BUS.publish(self.topic, {"call": "W7G"})
        _, last = self.connect()
        for _ in range(3):
            got, last = self.connect(last)
            self.assertEqual(got, [])

    def test_gets_what_was_published_while_it_was_away(self):
        """Better than before, not just quieter: the gap between two streams
        used to lose anything published in it."""
        BUS.publish(self.topic, {"call": "W7G"})
        _, last = self.connect()
        BUS.publish(self.topic, {"call": "K0LLT"})
        BUS.publish(self.topic, {"call": "W7F"})
        got, _ = self.connect(last)
        self.assertEqual([e["payload"]["call"] for e in got], ["K0LLT", "W7F"])

    def test_a_quiet_subscriber_keeps_up(self):
        """A client that wants one rare topic is told how far the bus has
        got, so it does not fall out of the history window while it waits
        and come back as a newcomer - which would be the replay again."""
        BUS.publish(self.topic, {"call": "W7G"})
        _, last = self.connect()
        for i in range(busmod.MAX_HISTORY + 20):
            BUS.publish(f"radio.resume_noise_{id(self)}", {"n": i})
        _, last = self.connect(last)       # nothing for it, but it moves on
        got, _ = self.connect(last)
        self.assertEqual(got, [])


class TestAnIdTheBusCannotVouchFor(Host):
    def test_an_id_from_before_a_restart_gets_the_state(self):
        BUS.publish(self.topic, {"call": "W7G"})
        got, _ = self.connect(1)           # far older than anything held
        self.assertEqual([e["payload"]["call"] for e in got], ["W7G"])

    def test_an_id_from_the_future_gets_the_state(self):
        BUS.publish(self.topic, {"call": "W7G"})
        got, _ = self.connect(10 ** 15)
        self.assertEqual([e["payload"]["call"] for e in got], ["W7G"])

    def test_a_junk_header_is_a_newcomer_not_an_error(self):
        BUS.publish(self.topic, {"call": "W7G"})
        r = self.client.get(f"/api/events?topics={self.topic}",
                            headers={"Last-Event-ID": "not-a-number"})
        self.assertEqual(r.status_code, 200)
        got, _ = parse(r.get_data(as_text=True))
        self.assertEqual([e["payload"]["call"] for e in got], ["W7G"])


class TestTheCardReaderSendsItsPlace(unittest.TestCase):
    """The SDK's own reader, which a browser's EventSource does not cover."""

    def test_the_second_connect_says_where_the_first_got_to(self):
        from hamdash.sdk import Card
        card = Card.__new__(Card)
        card.host_url = "http://127.0.0.1:1"
        card._subs = [("log.*", lambda t, p: None)]
        bodies = iter([
            ["id: 41", 'data: {"topic": "log.qso_added", "payload": {}, "id": 41}',
             "", "id: 57", "", "event: reconnect", "data: {}", ""],
            [],
        ])
        sent = []

        class Stream:
            status_code = 200

            def __init__(self, lines):
                self.lines = lines

            def iter_lines(self, decode_unicode=False):
                return iter(self.lines)

            def raise_for_status(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def get(url, **kw):
            sent.append(dict(kw.get("headers") or {}))
            try:
                return Stream(next(bodies))
            except StopIteration:
                raise SystemExit

        with mock.patch("hamdash.sdk.requests.get", side_effect=get), \
                mock.patch.object(threading.Event, "wait", lambda *a, **k: None):
            with self.assertRaises(SystemExit):
                card._listen()
        self.assertNotIn("Last-Event-ID", sent[0], "a first connect is a newcomer")
        self.assertEqual(sent[1].get("Last-Event-ID"), "57",
                         "the reconnect must carry the last id read, keepalive included")


if __name__ == "__main__":
    unittest.main()
