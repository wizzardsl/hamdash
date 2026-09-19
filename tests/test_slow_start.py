"""A card that is slow to start must still come up.

The bug this guards: `_wait_ready` gave up at START_TIMEOUT, set the status to
"no response" and returned. Nothing ever looked again, so a card that bound its
port a moment later served perfectly while the board went on reporting it dead
until the whole app was restarted. On a cold Windows boot - a fresh python.exe
being read off disk and scanned, several cards importing Flask at once - that
is an ordinary amount of slow, which is why it came right "after the third
reboot", by which point the file cache was warm.

Run with:  python -m unittest discover -s tests
"""
import socket
import threading
import time
import unittest
from unittest import mock

from hamdash import proc
from hamdash.proc import Worker, free_port


class FakeProc:
    """Alive until told otherwise, like a card that is merely busy."""

    def __init__(self):
        self.alive = True

    def poll(self):
        return None if self.alive else 1


def a_worker(port):
    w = Worker(card=mock.Mock(id="slowpoke"), host_url="http://127.0.0.1:0")
    w.port = port
    w.proc = FakeProc()
    w.status = "starting"
    return w


class TestSlowStart(unittest.TestCase):
    def setUp(self):
        # Short windows, so the test measures the behaviour and not the clock.
        self.patches = [
            mock.patch.object(proc, "START_TIMEOUT", 0.3),
            mock.patch.object(proc, "READY_GIVE_UP", 3.0),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.port = free_port()
        self.server = None

    def tearDown(self):
        if self.server:
            self.server.close()

    def bind_after(self, delay):
        """Open the port later, the way a cold start does."""
        def run():
            time.sleep(delay)
            self.server = socket.socket()
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server.bind(("127.0.0.1", self.port))
            self.server.listen(1)
        threading.Thread(target=run, daemon=True).start()

    def test_a_card_that_binds_late_still_reaches_running(self):
        w = a_worker(self.port)
        self.bind_after(1.0)                     # well past START_TIMEOUT
        with mock.patch.object(Worker, "_health_loop"):
            w._wait_ready()
        self.assertEqual(w.status, "running")

    def test_it_stays_starting_while_it_waits(self):
        """Not "no response": the card proxy hands back a self-refreshing page
        while a card is starting, which is what makes a slow start fill in by
        itself instead of needing the app restarted."""
        w = a_worker(self.port)
        seen = []
        self.bind_after(1.0)
        with mock.patch.object(Worker, "_health_loop"):
            t = threading.Thread(target=w._wait_ready, daemon=True)
            t.start()
            for _ in range(6):                   # across the START_TIMEOUT line
                time.sleep(0.1)
                seen.append(w.status)
            t.join(timeout=5)
        self.assertNotIn("no response", seen)
        self.assertEqual(w.status, "running")

    def test_a_card_that_never_listens_is_eventually_reported(self):
        w = a_worker(self.port)                  # nothing ever binds
        w._wait_ready()
        self.assertEqual(w.status, "no response")

    def test_a_card_that_exits_is_left_to_the_drain_thread(self):
        """Exiting is _drain's story to tell - it has the exit code and owns
        the restart decision - so _wait_ready must not overwrite it."""
        w = a_worker(self.port)
        threading.Timer(0.3, lambda: setattr(w.proc, "alive", False)).start()
        w._wait_ready()
        self.assertEqual(w.status, "starting")


if __name__ == "__main__":
    unittest.main()
