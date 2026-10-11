"""An exception in a thread reaches the log.

`hamdash.log` is advertised as carrying unhandled exceptions "including in
threads", and that is most of what this app is: the rig poller, the PTT
watchdog, every card's output drain, the bus readers, the audio captures.
A thread that dies takes its job with it and nothing else notices - the
process stays up and looks healthy, which is precisely why the hook is
there.

It did not work. `threading.excepthook` is handed an `ExceptHookArgs`
carrying `thread`, and the hook read `args.thread_name`, so it raised
AttributeError every single time it fired. What reached the log was that
AttributeError, never the exception it was called about. Found by
stopping a running instance and reading the console.

Run with:  python -m unittest discover -s tests
"""
import logging
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import run as runpy                                     # noqa: E402
from hamdash.config import Config                       # noqa: E402


class TestAThreadExceptionIsLogged(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="hamdash-thread-"))
        self.addCleanup(shutil.rmtree, self.home, True)

        root = logging.getLogger()
        before_handlers = list(root.handlers)
        before_level = root.level
        before_hook = threading.excepthook

        def restore():
            for h in list(root.handlers):
                if h not in before_handlers:
                    root.removeHandler(h)
                    try:
                        h.close()
                    except Exception:                   # noqa: BLE001
                        pass
            root.handlers[:] = before_handlers
            root.setLevel(before_level)
            threading.excepthook = before_hook

        self.addCleanup(restore)
        runpy.setup_logging(Config(self.home))

    def boom(self):
        raise ZeroDivisionError("the rig poller fell over")

    def test_the_hook_records_the_real_exception(self):
        with self.assertLogs("hamdash", level="ERROR") as caught:
            t = threading.Thread(target=self.boom, name="rig-poll")
            t.start()
            t.join()
        said = "\n".join(caught.output)
        self.assertIn("ZeroDivisionError", said)
        self.assertIn("the rig poller fell over", said)

    def test_it_says_which_thread(self):
        """Which one died is most of the diagnosis: the poller, a card's
        drain and the watchdog fail very differently."""
        with self.assertLogs("hamdash", level="ERROR") as caught:
            t = threading.Thread(target=self.boom, name="ptt-watchdog")
            t.start()
            t.join()
        self.assertIn("ptt-watchdog", "\n".join(caught.output))

    def test_the_hook_does_not_raise_about_itself(self):
        """It used to, which is how the real traceback was lost."""
        with self.assertLogs("hamdash", level="ERROR") as caught:
            t = threading.Thread(target=self.boom, name="whatever")
            t.start()
            t.join()
        self.assertNotIn("thread_name", "\n".join(caught.output))
        self.assertNotIn("AttributeError", "\n".join(caught.output))

    def test_a_vanished_thread_object_does_not_break_it(self):
        """`thread` is None when the object has already gone, which is
        what happens at interpreter shutdown - and shutdown is exactly
        when this fired in the wild."""
        args = threading.ExceptHookArgs(
            [ZeroDivisionError, ZeroDivisionError("late"), None, None])
        with self.assertLogs("hamdash", level="ERROR") as caught:
            threading.excepthook(args)                  # must not raise
        self.assertIn("unknown", "\n".join(caught.output))

    def test_it_reaches_the_log_file_people_are_told_to_read(self):
        t = threading.Thread(target=self.boom, name="drain")
        t.start()
        t.join()
        logging.shutdown()
        text = (self.home / "hamdash.log").read_text(encoding="utf-8",
                                                     errors="replace")
        self.assertIn("ZeroDivisionError", text)
        self.assertIn("drain", text)


if __name__ == "__main__":
    unittest.main()
