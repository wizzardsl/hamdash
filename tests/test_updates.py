"""Is there a newer hamdash?

`services/updates.py` asks GitHub's public "latest release" once a day and
says so in Settings -> Help. Pinned here: versions compare as numbers (1.10
is newer than 1.9), switched off means no request at all, a failure is kept
as the answer rather than raised, nothing from the config goes out, and an
app built by a test never starts the checker - the suite must not be
asking GitHub anything.

Nothing here touches the network.

Run with:  python -m unittest discover -s tests
"""
import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hamdash import __version__                         # noqa: E402
from hamdash.app import create_app                      # noqa: E402
from hamdash.config import Config                       # noqa: E402
from hamdash.services import updates                    # noqa: E402


class TestVersions(unittest.TestCase):
    def test_numbers_not_strings(self):
        self.assertTrue(updates.newer("1.10.0", "1.9.3"))
        self.assertFalse(updates.newer("1.9.3", "1.10.0"))

    def test_a_tag_with_a_v(self):
        self.assertTrue(updates.newer("v1.4.0", "1.3.1"))

    def test_the_same_version_is_not_newer(self):
        self.assertFalse(updates.newer("v1.3.1", "1.3.1"))
        self.assertFalse(updates.newer("1.3", "1.3.0"))

    def test_an_odd_tag_is_not_called_newer(self):
        for tag in ("v2.0.0-rc1", "latest", "", "v1.x"):
            with self.subTest(tag=tag):
                self.assertFalse(updates.newer(tag, "1.3.1"))


class TestTheChecker(unittest.TestCase):
    def test_says_when_there_is_one(self):
        u = updates.Updates(lambda: True,
                            fetch=lambda: ("v99.0.0", "https://example/r"))
        s = u.check()
        self.assertTrue(s["newer"])
        self.assertEqual((s["latest"], s["url"], s["current"]),
                         ("99.0.0", "https://example/r", __version__))

    def test_off_means_no_request(self):
        asked = []
        u = updates.Updates(lambda: False,
                            fetch=lambda: asked.append(1) or ("v99", ""))
        s = u.check()
        self.assertEqual(asked, [])
        self.assertFalse(s["enabled"])
        self.assertFalse(s["newer"])

    def test_a_failure_is_an_answer_not_an_exception(self):
        def down():
            raise OSError("network unreachable")
        s = updates.Updates(lambda: True, fetch=down).check()
        self.assertIn("unreachable", s["error"])
        self.assertFalse(s["newer"])
        self.assertTrue(s["checked"])

    def test_a_later_success_clears_the_error(self):
        answers = iter([OSError("down"), ("v0.0.1", "")])

        def fetch():
            a = next(answers)
            if isinstance(a, Exception):
                raise a
            return a
        u = updates.Updates(lambda: True, fetch=fetch)
        u.check()
        self.assertEqual(u.check()["error"], "")

    def test_the_request_carries_nothing_of_the_station(self):
        """One GET to a fixed URL with hamdash's User-Agent."""
        seen = {}

        class Reply:
            status_code = 200
            text = ""

            def raise_for_status(self):
                pass

            def json(self):
                return {"tag_name": "v1.0.0", "html_url": "u"}

        def get(url, **kw):
            seen.update(url=url, **kw)
            return Reply()
        with mock.patch("requests.get", get):
            updates._fetch()
        self.assertEqual(seen["url"], updates.LATEST)
        self.assertNotIn("params", seen)
        self.assertEqual(set(seen["headers"]), {"User-Agent", "Accept"})


class TestTheHost(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="hamdash-updates-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / "layout.json").write_text("[]", encoding="utf-8")

    def app(self, **kw):
        app, shutdown = create_app(Config(self.home), port=8765, **kw)
        self.addCleanup(shutdown)
        return app.test_client()

    def test_a_test_app_never_starts_the_checker(self):
        before = {t.name for t in threading.enumerate()}
        self.app()
        self.assertNotIn("update-check",
                         {t.name for t in threading.enumerate()} - before)

    def test_the_launcher_asks_for_it(self):
        run_py = (ROOT / "run.py").read_text(encoding="utf-8")
        self.assertIn("check_updates=True", run_py)

    def test_status_endpoint(self):
        s = self.app().get("/api/updates").get_json()
        self.assertEqual(s["current"], __version__)
        self.assertTrue(s["enabled"], "on by default")
        self.assertFalse(s["newer"])

    def test_turning_it_off_is_saved(self):
        c = self.app()
        c.put("/api/config", json={"updates": {"check": False}})
        self.assertFalse(c.get("/api/updates").get_json()["enabled"])
        stored = json.loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertFalse(stored["updates"]["check"])

    def test_check_now_does_not_wait_on_the_network(self):
        with mock.patch.object(updates, "_fetch",
                               side_effect=AssertionError("asked on a request thread")):
            r = self.app().post("/api/updates/check")
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
