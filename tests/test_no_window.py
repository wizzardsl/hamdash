"""No desktop window is not no board.

Found building the app on Linux: pywebview installs from pip without a GUI
toolkit to drive (GTK or Qt with Python bindings), and `webview.start()`
raised `WebViewException` with the board already serving and the cards
already starting. The traceback ended the process - exit 1, no window,
nothing on screen - where the board itself was perfectly able to run in a
browser, as it does when pywebview is not installed at all.

Two things are pinned: `main()` carries on when the window cannot open, and
`/api/shell` then says "browser" - a page told it is in the native shell
reaches for the pywebview bridge to pop a card out (invariant 2). The
existing no-pywebview fallback had that second fault too.

Run with:  python -m unittest discover -s tests
"""
import ast
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hamdash.app import create_app                      # noqa: E402
from hamdash.config import Config                       # noqa: E402

RUN_PY = (ROOT / "run.py").read_text(encoding="utf-8")


def _main():
    for node in ast.walk(ast.parse(RUN_PY)):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError("run.py has no main()")


def _sets_browser(handler) -> bool:
    """Does this except-block set shell["native"] = False?"""
    for n in ast.walk(handler):
        if (isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
                and n.value.value is False
                and any(isinstance(t, ast.Subscript)
                        and ast.unparse(t) == "shell['native']"
                        for t in n.targets)):
            return True
    return False


class TestTheShellCanChangeItsMind(unittest.TestCase):
    def setUp(self):
        home = Path(tempfile.mkdtemp(prefix="hamdash-nowindow-"))
        self.addCleanup(shutil.rmtree, home, True)
        (home / "config.json").write_text(json.dumps(
            {"radio": {"mock": True}}), encoding="utf-8")
        (home / "layout.json").write_text("[]", encoding="utf-8")
        self.shell = {"native": True}
        app, shutdown = create_app(Config(home), port=8765,
                                   native=lambda: self.shell["native"])
        self.addCleanup(shutdown)
        self.client = app.test_client()

    def native(self):
        return self.client.get("/api/shell").get_json()["native"]

    def test_asked_each_time_not_fixed_at_start(self):
        self.assertTrue(self.native())
        self.shell["native"] = False
        self.assertFalse(self.native())

    def test_a_plain_bool_still_works(self):
        home = Path(tempfile.mkdtemp(prefix="hamdash-nowindow-"))
        self.addCleanup(shutil.rmtree, home, True)
        (home / "layout.json").write_text("[]", encoding="utf-8")
        app, shutdown = create_app(Config(home), port=8765, native=True)
        self.addCleanup(shutdown)
        self.assertTrue(app.test_client().get("/api/shell").get_json()["native"])


class TestMainCarriesOn(unittest.TestCase):
    def test_a_window_that_cannot_open_is_caught(self):
        for node in ast.walk(_main()):
            if isinstance(node, ast.Try) and "webview.start()" in ast.unparse(
                    ast.Module(body=node.body, type_ignores=[])):
                (handler,) = node.handlers
                self.assertTrue(_sets_browser(handler),
                                "the page would be told it is in a window")
                self.assertIn("http.join()", ast.unparse(handler),
                              "and main() must go on serving")
                return
        self.fail("webview.start() is not inside a try in main()")

    def test_no_pywebview_is_a_browser_too(self):
        for node in ast.walk(_main()):
            if isinstance(node, ast.Try) and "import webview" in ast.unparse(
                    ast.Module(body=node.body, type_ignores=[])):
                for h in node.handlers:
                    if h.type is not None and ast.unparse(h.type) == "ImportError":
                        self.assertTrue(_sets_browser(h))
                        return
        self.fail("no ImportError fallback around `import webview` in main()")


class TestTheWindowFitsTheScreen(unittest.TestCase):
    """1440x900 opened partly off a 1024x768 screen (GitHub's Windows
    runner), and would on a 1366x768 laptop or a 1080p one at 150%."""

    @classmethod
    def setUpClass(cls):
        import run
        cls.size = staticmethod(run.window_size)

    def test_a_big_screen_gets_the_board_s_size(self):
        self.assertEqual(self.size([(2560, 1440)]), (1440, 900, 960, 640))

    def test_small_screens_get_a_window_inside_them(self):
        for sw, sh in ((1024, 768), (1366, 768), (1280, 720), (800, 600)):
            with self.subTest(screen=(sw, sh)):
                w, h, mw, mh = self.size([(sw, sh)])
                self.assertLessEqual(w, sw)
                self.assertLessEqual(h, sh)
                self.assertLessEqual(mw, w, "a minimum bigger than the window")
                self.assertLessEqual(mh, h)

    def test_the_primary_screen_decides(self):
        self.assertEqual(self.size([(1024, 768), (2560, 1440)])[:2], (921, 691))

    def test_nothing_to_ask_is_the_default(self):
        self.assertEqual(self.size([]), (1440, 900, 960, 640))

    def test_main_asks_the_screen(self):
        main = ast.unparse(_main())
        self.assertIn("window_size(", main)
        self.assertIn("webview.screens", main)
        self.assertNotIn("width=1440", main)

    def test_it_is_placed_on_that_screen(self):
        """Left to WinForms' CenterScreen, the right-sized window still
        opened off the edge of the runner's screen."""
        for node in ast.walk(_main()):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "webview.create_window":
                self.assertIn("screen", [k.arg for k in node.keywords])
                return
        self.fail("main() creates no window")


if __name__ == "__main__":
    unittest.main()
