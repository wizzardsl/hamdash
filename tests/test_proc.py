"""Starting a card, once.

Waitress hands every request to a different thread of a pool, and a board
loading a tile makes several at the same moment: the iframe, its settings,
the card's own first call. All of them go through `Supervisor.ensure`, so
starting has to be safe to ask for concurrently.

Nothing here spawns a process - `subprocess.Popen` is stood in for, because
what is being tested is how many times it would have been called.

Run with:  python -m unittest discover -s tests
"""
import threading
import unittest
from pathlib import Path
from unittest import mock

from hamdash import proc
from hamdash.proc import Supervisor
from hamdash.registry import Card


def a_card(cid="demo"):
    return Card(id=cid, path=Path("."), name="Demo", entry="card.py")


class FakeProc:
    """A process that is alive until it is told otherwise."""

    def __init__(self, *a, **kw):
        self.stdout = iter(())
        self._code = None

    def poll(self):
        return self._code

    def wait(self, timeout=None):
        return self._code or 0

    def terminate(self):
        self._code = 0

    kill = terminate


class TestStartingOnce(unittest.TestCase):
    def setUp(self):
        self.sup = Supervisor("http://127.0.0.1:1/", home="")
        self.spawned = []

        def popen(*a, **kw):
            p = FakeProc()
            self.spawned.append(p)
            return p

        self.popen = mock.patch("subprocess.Popen", side_effect=popen)
        self.popen.start()
        self.addCleanup(self.popen.stop)
        # The readiness poll and the log drain are threads of their own that
        # would open sockets and read a pipe; neither is what is under test.
        for name in ("_wait_ready", "_drain"):
            patch = mock.patch.object(proc.Worker, name, lambda self: None)
            patch.start()
            self.addCleanup(patch.stop)

    def test_two_requests_at_once_start_one_process(self):
        """They each spawned one. The second took the port the first had been
        told to use, the host kept one process handle and the other's port,
        and the tile sat on "Starting..." for ever while a stray process held
        the sound card. It presents as a card that will not load."""
        card = a_card()
        go = threading.Barrier(8)

        def race():
            go.wait()
            self.sup.ensure(card)

        threads = [threading.Thread(target=race) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.spawned), 1)

    def test_one_worker_per_card(self):
        """Two threads building a Worker each means the second replaces the
        first in the dict, and nothing owns the process the first spawned."""
        card = a_card()
        go = threading.Barrier(6)
        seen = []

        def race():
            go.wait()
            seen.append(self.sup.ensure(card))

        threads = [threading.Thread(target=race) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len({id(w) for w in seen}), 1)
        self.assertIs(seen[0], self.sup.get(card.id))

    def test_the_host_keeps_the_port_the_card_was_told(self):
        """The whole point of the lock: whatever port was handed out in the
        environment is the one the worker then proxies to."""
        card = a_card()
        with mock.patch.object(proc, "free_port",
                               side_effect=[41001, 41002, 41003]):
            self.sup.ensure(card)
            self.sup.ensure(card)
        self.assertEqual(self.sup.get(card.id).port, 41001)

    def test_a_card_that_died_is_started_again(self):
        """The lock must not turn "already running" into "never again"."""
        card = a_card()
        w = self.sup.ensure(card)
        self.assertEqual(len(self.spawned), 1)
        self.spawned[0]._code = 1               # it exited
        w.status = "stopped"
        self.sup.ensure(card)
        self.assertEqual(len(self.spawned), 2)


if __name__ == "__main__":
    unittest.main()
