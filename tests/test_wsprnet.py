"""WSPR spots to WSPRnet: what is sent, what is not, and what a failure
does to the queue.

Nothing here touches the network: the uploader posts to a list. The form
is checked field by field against WSJT-X's client, which is the only
description of WSPRnet's upload there is (see services/wsprnet.py).

Run with:  python -m unittest discover -s tests
"""
import calendar
import unittest

from hamdash.config import DEFAULTS
from hamdash.services import wsprnet

ME = {"callsign": "VA7SLL", "grid": "DO00ha"}
SLOT = calendar.timegm((2026, 1, 6, 12, 2, 0))


def event(decodes=None, **over):
    payload = {"mode": "WSPR", "slot_ts": SLOT, "dial_hz": 10138700,
               "simulated": False, "source": "alsa_input.usb",
               "decodes": decodes if decodes is not None else [
                   {"text": "K1ABC FN42 37", "call": "K1ABC", "grid": "FN42",
                    "dbm": 37, "snr": -21, "dt": 0.34, "drift": -1.2,
                    "audio_hz": 1484.6, "rf_hz": 10140185, "kind": 1}]}
    payload.update(over)
    return {"topic": "decoder.decoded", "source": "decoder",
            "payload": payload}


class Net:
    def __init__(self, replies=None):
        self.sent = []
        self.replies = list(replies or [])

    def __call__(self, url, form):
        self.sent.append((url, dict(form)))
        r = self.replies.pop(0) if self.replies else "1 spot(s) added"
        if isinstance(r, Exception):
            raise r
        return r


def uploader(net=None, station=ME, t=None):
    clock = t or [1000.0]
    u = wsprnet.Uploader(lambda: station, "1.7.0", post=net or Net(),
                         clock=lambda: clock[0])
    u.set_enabled(True)
    return u, clock


class TestTheForm(unittest.TestCase):
    def test_every_field_as_wsjtx_sends_it(self):
        net = Net()
        u, _ = uploader(net)
        self.assertEqual(u.offer(event()), 1)
        self.assertTrue(u.send_one())
        url, form = net.sent[0]
        self.assertEqual(url, "http://wsprnet.org/post/")
        self.assertEqual(form, {
            "function": "wspr", "rcall": "VA7SLL", "rgrid": "DO00ha",
            "rqrg": "10.138700", "date": "260106", "time": "1202",
            "sig": "-21", "dt": "0.3", "drift": "-1", "tqrg": "10.140185",
            "tcall": "K1ABC", "tgrid": "FN42", "dbm": "37",
            "version": "hamdash 1.7.0", "mode": "2"})

    def test_a_compound_call_has_no_grid_and_a_known_hash_has_six(self):
        u, _ = uploader()
        u.offer(event([
            {"call": "PJ4/K1ABC", "grid": "", "dbm": 33, "snr": -20,
             "dt": 0.1, "drift": 0, "rf_hz": 10140100, "kind": 2},
            {"call": "PJ4/K1ABC", "grid": "FK52UD", "dbm": 33, "snr": -20,
             "dt": 0.1, "drift": 0, "rf_hz": 10140100, "kind": 3}]))
        self.assertEqual([(f["tcall"], f["tgrid"]) for f in u.queue],
                         [("PJ4/K1ABC", ""), ("PJ4/K1ABC", "FK52UD")])


class TestWhatIsNotSent(unittest.TestCase):
    def test_off_by_default(self):
        self.assertEqual(DEFAULTS["wsprnet"], {"enabled": False})
        u = wsprnet.Uploader(lambda: ME, "x", post=Net())
        self.assertEqual(u.offer(event()), 0)
        self.assertEqual(u.blocked(), "off")

    def test_no_callsign_or_no_locator(self):
        for st, why in (({"grid": "DO00"}, "no callsign"),
                        ({"callsign": "VA7SLL"}, "no locator")):
            u, _ = uploader(station=st)
            self.assertEqual(u.blocked(), why)
            self.assertEqual(u.offer(event()), 0)

    def test_simulated_or_without_a_dial(self):
        u, _ = uploader()
        self.assertEqual(u.offer(event(simulated=True)), 0)
        self.assertEqual(u.offer(event(dial_hz=None)), 0)
        self.assertEqual(u.skipped, {"simulated": 1, "no dial frequency": 1})

    def test_a_hash_nobody_has_named_and_the_operator_s_own_call(self):
        u, _ = uploader()
        n = u.offer(event([
            {"call": "", "grid": "FK52UD", "dbm": 33, "snr": -20, "dt": 0,
             "drift": 0, "rf_hz": 10140100, "kind": 3},
            {"call": "VA7SLL", "grid": "DO00", "dbm": 23, "snr": -10,
             "dt": 0, "drift": 0, "rf_hz": 10140100, "kind": 1}]))
        self.assertEqual(n, 0)

    def test_ft8_is_psk_reporter_s(self):
        u, _ = uploader()
        self.assertEqual(u.offer(event(mode="FT8")), 0)

    def test_only_the_decoder_may_say_what_was_heard(self):
        u, _ = uploader()
        e = event()
        e["source"] = "ui"
        self.assertEqual(u.offer(e), 0)


class TestFailures(unittest.TestCase):
    def test_a_network_failure_keeps_the_spot_and_waits(self):
        net = Net([ConnectionError("down")])
        u, clock = uploader(net)
        u.offer(event())
        self.assertFalse(u.send_one())
        self.assertEqual(len(u.queue), 1)
        self.assertIn("will retry", u.error)
        self.assertFalse(u.send_one())          # not yet
        self.assertEqual(len(net.sent), 1)
        clock[0] += wsprnet.RETRY_S + 1
        self.assertTrue(u.send_one())
        self.assertEqual((u.total_sent, len(u.queue), u.error), (1, 0, ""))

    def test_a_refusal_drops_that_spot_only_and_says_what_it_was(self):
        net = Net(["Bad grid", "1 spot(s) added"])
        u, _ = uploader(net)
        u.offer(event())
        u.offer(event())
        u.send_one()
        self.assertEqual((u.rejected, len(u.queue)), (1, 1))
        self.assertIn("Bad grid", u.error)
        u.send_one()
        self.assertEqual((u.total_sent, len(u.queue)), (1, 0))

    def test_a_long_outage_keeps_the_newest(self):
        u, _ = uploader()
        for _ in range(wsprnet.MAX_QUEUE + 5):
            u.offer(event())
        self.assertEqual(len(u.queue), wsprnet.MAX_QUEUE)

    def test_turning_it_off_forgets_the_queue(self):
        u, _ = uploader()
        u.offer(event())
        u.set_enabled(False)
        self.assertEqual(u.queue, [])

    def test_stopping_sends_what_is_waiting_without_hanging(self):
        net = Net()
        u, _ = uploader(net)
        u.offer(event())
        u.offer(event())
        u.stop(budget=2.0)
        self.assertEqual(len(net.sent), 2)


class TestTheHost(unittest.TestCase):
    def test_the_status_endpoint_and_settings_key(self):
        import tempfile
        from hamdash.app import create_app
        from hamdash.config import Config
        with tempfile.TemporaryDirectory() as home:
            app, shutdown = create_app(Config(home))
            try:
                c = app.test_client()
                self.assertFalse(c.get("/api/wsprnet").get_json()["enabled"])
                c.put("/api/config", json={"wsprnet": {"enabled": True},
                                           "station": ME})
                s = c.get("/api/wsprnet").get_json()
                self.assertTrue(s["enabled"])
                self.assertEqual(s["blocked"], "")
            finally:
                shutdown()


if __name__ == "__main__":
    unittest.main()
