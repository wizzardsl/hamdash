"""One capture per sound card, fanned out to every consumer.

PortAudio on Windows will not reliably give one capture device to two
openers, so a spectrum or decoder tile holding the radio's input used to
lock Listen out of it, and the other way round - and the refusal named
WSJT-X as the likely culprit. `plugins/rx_audio/capture.py` is now the
only thing that opens an input; Listen reads it from the browser and the
other cards through `hamdash.sdk.AudioFeed`.

These drive the real `Hub` with fake sources that count how often a
"device" is opened and closed and play tones at known frequencies, so
the rules are asserted rather than described:

  * two consumers, one device, one open - at the same rate or not;
  * one stopping while the other goes on hearing audio;
  * the device closing exactly once, when the last one leaves;
  * no consumer handed another's backlog;
  * the tone arriving at the frequency it was played at, through each
    rate's own filter.

The last class runs `AudioFeed` against the real engine card over HTTP.

Run with:  python -m unittest discover -s tests
"""
import importlib.util
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

PLUGINS = Path(__file__).resolve().parent.parent / "plugins"
ENGINE = PLUGINS / "rx_audio"

try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:                                     # pragma: no cover
    HAVE_NUMPY = False

needs_numpy = unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")


def _load(name, path):
    if str(ENGINE) not in sys.path:
        sys.path.insert(0, str(ENGINE))         # capture.py imports dsp
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


capture = _load("rx_audio_capture_for_tests", ENGINE / "capture.py") \
    if HAVE_NUMPY else None

RATE = 48000


def tone(hz, n, start=0, rate=RATE, amp=0.5):
    t = (np.arange(n) + start) / rate
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def power_at(sig, hz, rate):
    t = np.arange(len(sig), dtype=np.float64) / rate
    i = np.dot(sig, np.cos(2 * np.pi * hz * t)) / len(sig)
    q = np.dot(sig, np.sin(2 * np.pi * hz * t)) / len(sig)
    return float(np.hypot(i, q))


class Bench:
    """Fake devices. Each one counts its opens and closes, and plays
    nothing until the test pushes samples - so what every tap holds is
    exactly what the test put there."""

    def __init__(self):
        self.opened: dict[str, int] = {}
        self.closed: dict[str, int] = {}
        self.ready: dict[str, threading.Event] = {}
        self.log: list[str] = []

    def source(self, device):
        ready = self.ready.setdefault(device, threading.Event())

        def run(cap):
            self.opened[device] = self.opened.get(device, 0) + 1
            cap.begin(RATE)
            ready.set()
            while not cap.stopping:
                time.sleep(0.005)
            self.closed[device] = self.closed.get(device, 0) + 1
        return run

    def hub(self):
        return capture.Hub(source=self.source, log=self.log.append)


def push(hub, device, sig):
    """Play `sig` into a fake device, the way PortAudio's callback would:
    20 ms at a time."""
    hub_cap = hub.captures[device]
    for i in range(0, len(sig), 960):
        hub_cap.feed(sig[i:i + 960])


def pcm(tap, since):
    first, last, body, _ = tap.since(since, limit=capture.HISTORY)
    return last, np.frombuffer(body, dtype="<i2").astype(np.float32) / 32768


@needs_numpy
class TestOneOpenPerDevice(unittest.TestCase):
    def setUp(self):
        self.bench = Bench()
        self.hub = self.bench.hub()
        self.addCleanup(self.hub.close_all)

    def wait_open(self, device):
        self.assertTrue(self.bench.ready.setdefault(
            device, threading.Event()).wait(2))

    def test_two_listeners_at_one_rate_share_one_tap(self):
        a = self.hub.tap_for("usb", 12000, "listen-page")
        b = self.hub.tap_for("usb", 12000, "decoder-feed")
        self.wait_open("usb")
        self.assertIs(a, b)
        self.assertEqual(self.bench.opened, {"usb": 1})

    def test_two_rates_are_two_filters_on_one_open(self):
        """Listen at 12 kHz and a waterfall at 24: before this, a second
        rate was a second Tap and so a second open of the same device -
        the exact failure, inside the one card meant to prevent it."""
        listen = self.hub.tap_for("usb", 12000, "listen-page")
        water = self.hub.tap_for("usb", 24000, "spectrum-feed")
        self.wait_open("usb")
        self.assertIsNot(listen, water)
        self.assertEqual(self.bench.opened, {"usb": 1})
        self.assertEqual(len(self.hub.captures), 1)

        push(self.hub, "usb", tone(1000, RATE // 2) + tone(5000, RATE // 2))
        _, lo = pcm(listen, 0)
        _, hi = pcm(water, 0)
        self.assertEqual(listen.out_rate, 12000)
        self.assertEqual(water.out_rate, 24000)
        # Both hear the 1 kHz tone at full strength...
        self.assertGreater(power_at(lo, 1000, 12000), 0.2)
        self.assertGreater(power_at(hi, 1000, 24000), 0.2)
        # ...and only the wider one passes 5 kHz. At 12 kHz it is above
        # the 4.3 kHz passband, and must not fold back to 7 kHz's mirror.
        self.assertGreater(power_at(hi, 5000, 24000), 0.2)
        self.assertLess(power_at(lo, 5000, 12000), 0.02)
        self.assertLess(power_at(lo, 7000 - 5000, 12000), 0.02)

    def test_different_devices_are_different_opens(self):
        self.hub.tap_for("usb", 12000, "a")
        self.hub.tap_for("line in", 12000, "b")
        self.wait_open("usb")
        self.wait_open("line in")
        self.assertEqual(self.bench.opened, {"usb": 1, "line in": 1})


@needs_numpy
class TestLeaving(unittest.TestCase):
    def setUp(self):
        self.bench = Bench()
        self.hub = self.bench.hub()
        self.addCleanup(self.hub.close_all)
        self.listen = self.hub.tap_for("usb", 12000, "listen-page")
        self.decode = self.hub.tap_for("usb", 12000, "decoder-feed")
        self.water = self.hub.tap_for("usb", 24000, "spectrum-feed")
        self.assertTrue(self.bench.ready["usb"].wait(2))

    def test_one_stopping_leaves_the_others_listening(self):
        closed = self.hub.release("listen-page")
        self.assertEqual(closed, [])
        self.assertEqual(self.bench.closed, {})
        before = self.decode.seq
        push(self.hub, "usb", tone(700, RATE // 5))
        self.assertGreater(self.decode.seq, before,
                           "the decoder stopped hearing the radio when "
                           "Listen pressed Stop")

    def test_the_last_rate_going_keeps_the_device_for_the_other(self):
        self.hub.release("spectrum-feed")
        self.assertNotIn("usb|24000", self.hub.taps)
        self.assertIn("usb|12000", self.hub.taps)
        self.assertEqual(self.bench.closed, {})

    def test_the_device_closes_exactly_once_when_the_last_leaves(self):
        self.assertEqual(self.hub.release("listen-page"), [])
        self.assertEqual(self.hub.release("spectrum-feed"), [])
        self.assertEqual(self.hub.release("decoder-feed"), ["usb"])
        # Stop waits for the thread to let go, so this is already true.
        self.assertEqual(self.bench.closed, {"usb": 1})
        self.assertEqual(self.bench.log,
                         ["closing usb: last listener left"])
        # And nothing left to close it a second time.
        self.assertEqual(self.hub.release("decoder-feed"), [])
        self.hub.close_all()
        self.assertEqual(self.bench.closed, {"usb": 1})
        self.assertEqual(self.hub.captures, {})

    def test_a_listener_nobody_heard_of_closes_nothing(self):
        self.assertEqual(self.hub.release("a stale tab"), [])
        self.assertEqual(self.bench.closed, {})

    def test_the_reaper_closes_once_when_everybody_has_gone_quiet(self):
        later = time.time() + capture.IDLE_SECONDS + 1
        self.assertEqual(self.hub.reap(later), ["usb"])
        self.assertEqual(self.bench.closed, {"usb": 1})
        self.assertEqual(self.hub.reap(later + 60), [])

    def test_the_reaper_spares_a_listener_still_asking(self):
        later = time.time() + capture.IDLE_SECONDS + 1
        with mock.patch.object(capture.time, "time", return_value=later):
            self.hub.tap_for("usb", 12000, "decoder-feed")
        self.assertEqual(self.hub.reap(later), [])
        self.assertEqual(set(self.hub.taps), {"usb|12000"})
        self.assertEqual(self.bench.closed, {})

    def test_coming_back_after_the_close_opens_it_again(self):
        for who in ("listen-page", "decoder-feed", "spectrum-feed"):
            self.hub.release(who)
        self.bench.ready["usb"].clear()
        self.hub.tap_for("usb", 12000, "listen-page")
        self.assertTrue(self.bench.ready["usb"].wait(2))
        self.assertEqual(self.bench.opened, {"usb": 2})
        self.assertEqual(self.bench.closed, {"usb": 1})


@needs_numpy
class TestNobodyGetsAnotherOnesBacklog(unittest.TestCase):
    def setUp(self):
        self.bench = Bench()
        self.hub = self.bench.hub()
        self.addCleanup(self.hub.close_all)
        self.listen = self.hub.tap_for("usb", 12000, "listen-page")
        self.assertTrue(self.bench.ready["usb"].wait(2))
        # A second of audio that only Listen was there for.
        push(self.hub, "usb", tone(600, RATE))

    def test_a_new_rate_starts_empty(self):
        water = self.hub.tap_for("usb", 24000, "spectrum-feed")
        self.assertEqual(water.seq, 0)
        self.assertEqual(water.since(0)[2], b"")

    def test_joining_at_the_head_hears_only_what_comes_after(self):
        """`since=head`, which is how a feed joins: the decoder starting
        up must not decode the last three seconds of Listen's ring as if
        they were happening now."""
        decode = self.hub.tap_for("usb", 12000, "decoder-feed")
        self.assertIs(decode, self.listen)
        head = decode.seq
        self.assertGreater(head, 0)
        self.assertEqual(decode.since(head)[2], b"")
        push(self.hub, "usb", tone(1500, RATE // 5, start=RATE))
        first, last, _, _ = decode.since(head, limit=capture.HISTORY)
        self.assertEqual(first, head + 1, "it was handed older blocks")
        self.assertEqual(last, decode.seq)
        # Skip the first block: it carries the filter's tail and the part
        # of a block that straddled the join - continuous audio, which is
        # right, not somebody's backlog.
        _, got = pcm(decode, head + 1)
        self.assertGreater(power_at(got, 1500, 12000), 0.2)
        self.assertLess(power_at(got, 600, 12000), 0.005,
                        "the newcomer was handed the old tone")

    def test_each_reader_keeps_its_own_place(self):
        a_last, _ = pcm(self.listen, 0)
        push(self.hub, "usb", tone(900, RATE // 10, start=RATE))
        b_last, b = pcm(self.listen, a_last)
        _, a = pcm(self.listen, 0)
        self.assertEqual(len(b), (b_last - a_last) * self.listen.block_n)
        self.assertGreater(len(a), len(b))


@needs_numpy
class TestAFailedOpen(unittest.TestCase):
    def test_it_is_retried_on_a_timer_and_keeps_its_listeners(self):
        tries = []

        def source(device):
            def run(cap):
                tries.append(device)
                if len(tries) == 1:
                    raise RuntimeError("DSERR_ALLOCATED")
                cap.begin(RATE)
                while not cap.stopping:
                    time.sleep(0.005)
            return run

        hub = capture.Hub(source=source, log=lambda _: None)
        self.addCleanup(hub.close_all)
        tap = hub.tap_for("usb", 12000, "a")
        tap.capture._thread.join(2)
        self.assertEqual(tap.status, "error")
        # Asked again at once: still the error, not a fresh "starting".
        self.assertEqual(hub.tap_for("usb", 12000, "a").status, "error")
        self.assertEqual(len(tries), 1)
        later = time.time() + capture.RETRY_SECONDS + 1
        with mock.patch.object(capture.time, "time", return_value=later):
            again = hub.tap_for("usb", 12000, "a")
        self.assertIs(again, tap, "the listener's tap was replaced")
        for _ in range(200):
            if tap.status == "running":
                break
            time.sleep(0.01)
        self.assertEqual(tap.status, "running")
        self.assertEqual(len(hub.captures), 1)


@needs_numpy
class TestTheFeedAgainstTheRealEngine(unittest.TestCase):
    """`AudioFeed` reading the real rx_audio card over HTTP, mounted where
    the host's proxy puts it, with a fake device behind it playing a tone
    in real time. Two cards' feeds, one device, one open."""

    @classmethod
    def setUpClass(cls):
        from werkzeug.middleware.dispatcher import DispatcherMiddleware
        from werkzeug.serving import make_server
        from hamdash.sdk import Card
        with mock.patch.object(Card, "run", lambda self: None):
            cls.m = _load("rx_audio_engine_for_feed_tests",
                          ENGINE / "card.py")
        cls.bench = Bench()

        def source(device):
            def run(cap):
                cls.bench.opened[device] = cls.bench.opened.get(device, 0) + 1
                cap.begin(RATE)
                at = 0
                nxt = time.time()
                while not cap.stopping:
                    cap.feed(tone(800, 960, start=at))
                    at += 960
                    nxt += 0.02
                    time.sleep(max(0.0, nxt - time.time()))
                cls.bench.closed[device] = cls.bench.closed.get(device, 0) + 1
            return run

        cls.m.hub = cls.m.capture.Hub(source=source,
                                      log=cls.bench.log.append)
        app = DispatcherMiddleware(lambda e, s: (s("404 Not Found", []),
                                                 [b""])[1],
                                   {"/cards/rx_audio": cls.m.card.app})
        import logging
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        cls.server = make_server("127.0.0.1", 0, app, threaded=True)
        cls.port = cls.server.server_port
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.m.hub.close_all()

    def feed(self, rate, got):
        from hamdash.sdk import AudioFeed, HostClient
        host = HostClient(f"http://127.0.0.1:{self.port}", "", "test")
        return AudioFeed(host, "fake usb", rate,
                         lambda s, r: got.append((s, r)))

    def wait_for(self, pred, secs=3.0):
        end = time.time() + secs
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.02)
        return False

    def test_two_feeds_one_device(self):
        dec, spec = [], []
        a = self.feed(12000, dec).start()
        b = self.feed(24000, spec).start()
        try:
            self.assertTrue(self.wait_for(
                lambda: sum(len(s) for s, _ in dec) > 6000
                and sum(len(s) for s, _ in spec) > 12000))
            self.assertEqual(self.bench.opened, {"fake usb": 1})
            self.assertEqual({r for _, r in dec}, {12000})
            self.assertEqual({r for _, r in spec}, {24000})
            d = np.concatenate([s for s, _ in dec])
            self.assertGreater(power_at(d, 800, 12000), 0.2)
            self.assertEqual(a.status, "running")

            # One stops; the other goes on hearing it.
            a.stop()
            self.assertEqual(self.bench.closed, {})
            n = len(spec)
            self.assertTrue(self.wait_for(lambda: len(spec) > n + 3))
            self.assertEqual(b.gaps, 0)
        finally:
            b.stop()
        # The last one out closed it, once.
        self.assertTrue(self.wait_for(
            lambda: self.bench.closed == {"fake usb": 1}))
        self.assertEqual(self.bench.log.count(
            "closing fake usb: last listener left"), 1)

    def test_the_system_default_is_said_in_words(self):
        """A blank `device` means "this tile's settings" to the engine, so
        a spectrum tile left blank must not arrive as one."""
        from hamdash.sdk import AudioFeed, HostClient
        f = AudioFeed(HostClient("http://x", "", "t"), "", 12000, print)
        self.assertEqual(f._params()["device"], "<system default>")
        self.assertEqual(f._params()["device"], self.m.capture.SYSTEM_DEFAULT)
        with self.m.card.app.test_request_context(
                "/api/pcm?device=%3Csystem%20default%3E"):
            self.assertEqual(self.m._device_for(self.m._conf()), "")


@needs_numpy
class TestTheFeedsBookkeeping(unittest.TestCase):
    """`_take` on its own, with replies the engine could send."""

    class R:
        def __init__(self, first, last, head, n=0, status="running"):
            self.headers = {"X-First-Seq": str(first), "X-Last-Seq": str(last),
                            "X-Head-Seq": str(head), "X-Rate": "12000",
                            "X-Status": status}
            self.content = b"\0\0" * n

    def setUp(self):
        from hamdash.sdk import AudioFeed, HostClient
        self.got = []
        self.f = AudioFeed(HostClient("http://x", "", "t"), "usb", 12000,
                           lambda s, r: self.got.append(len(s)))

    def test_it_joins_at_the_head(self):
        self.assertEqual(self.f._params()["since"], "head")
        self.f._take(self.R(140, 140, 140), np)
        self.assertEqual(self.f._since, 140)
        self.assertEqual(self.got, [])

    def test_it_counts_falling_off_the_ring(self):
        self.f._since = 10
        self.f._take(self.R(50, 60, 60, n=2640), np)
        self.assertEqual(self.f.gaps, 1)
        self.assertEqual(self.f._since, 60)
        self.assertEqual(self.got, [2640])

    def test_an_engine_that_restarted_is_rejoined_live(self):
        self.f._since = 9000
        self.f._take(self.R(3, 3, 3), np)
        self.assertEqual(self.f._since, "head")

    def test_an_error_rejoins_live_when_it_clears(self):
        self.f._since = 40
        self.f._take(self.R(40, 40, 40, status="error"), np)
        self.assertEqual(self.f._since, "head")
        self.assertEqual(self.f.status, "error")


class TestTheOtherCardsDoNotOpenDevices(unittest.TestCase):
    """The whole fix rests on this: if the spectrum or the decoder ever
    open an input of their own again, the lock-out comes back."""

    def test_no_card_but_the_engine_opens_an_input(self):
        for card in ("spectrum", "decoder"):
            src = (PLUGINS / card / "card.py").read_text(encoding="utf-8")
            self.assertNotIn("InputStream(", src, card)
            self.assertIn("AudioFeed(", src, card)

    def test_they_declare_the_engine_so_it_is_not_reaped(self):
        import json
        for card in ("spectrum", "decoder"):
            m = json.loads((PLUGINS / card / "card.json").read_text())
            self.assertIn("rx_audio", m.get("uses", []), card)


if __name__ == "__main__":
    unittest.main()
