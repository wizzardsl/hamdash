"""Popped-out windows open where they were - across a restart, and only on
a screen that still exists.

`windows._GEOMETRY` used to be the only record, so every restart put each
pop-out back in the middle of the screen. It is kept in
`<home>/windows.json` now (a fact about this machine's screens, so not in
layout.json, which travels). What is pinned here: a position on a monitor
that has since gone is dropped rather than used - the window would open
somewhere nobody can reach - while one on a second monitor that is still
there is kept; sizes are shrunk to the screen; a broken file is forgotten,
not fatal; and only the newest windows are kept, so the file cannot grow
for ever as tiles come and go. Nothing here opens a window.

Run with:  python -m unittest discover -s tests
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hamdash import windows                             # noqa: E402

LAPTOP = (0, 0, 1366, 768)
SECOND = (1366, 0, 1920, 1080)


class TestPlace(unittest.TestCase):
    def test_kept_where_it_was(self):
        g = windows.place({"w": 500, "h": 400, "x": 100, "y": 50}, [LAPTOP])
        self.assertEqual(g, {"w": 500, "h": 400, "x": 100, "y": 50})

    def test_on_a_second_monitor_that_is_still_there(self):
        g = windows.place({"w": 500, "h": 400, "x": 2000, "y": 300},
                          [LAPTOP, SECOND])
        self.assertEqual((g["x"], g["y"]), (2000, 300))

    def test_on_a_monitor_that_has_gone(self):
        """Unplugged since: the window would open off every screen."""
        g = windows.place({"w": 500, "h": 400, "x": 2000, "y": 300}, [LAPTOP])
        self.assertEqual((g["x"], g["y"]), (None, None))
        self.assertEqual((g["w"], g["h"]), (500, 400))

    def test_a_title_bar_off_the_edge_does_not_count(self):
        g = windows.place({"w": 500, "h": 400, "x": 1340, "y": 10}, [LAPTOP])
        self.assertIsNone(g["x"])

    def test_too_big_for_the_screen_it_lands_on(self):
        g = windows.place({"w": 1800, "h": 1000, "x": 2000, "y": 30}, [LAPTOP])
        self.assertEqual((g["w"], g["h"]), (1366, 768))

    def test_nothing_remembered(self):
        g = windows.place({}, [LAPTOP])
        self.assertEqual(g, {"w": None, "h": None, "x": None, "y": None})

    def test_no_screens_to_ask_keeps_what_there_is(self):
        g = windows.place({"w": 500, "h": 400, "x": 100, "y": 50}, [])
        self.assertEqual((g["x"], g["y"]), (100, 50))


class TestTheFile(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="hamdash-geo-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = self.dir / "windows.json"

    def test_round_trip(self):
        windows.save(self.path, {"t1": {"w": 500, "h": 400, "x": 10, "y": 20,
                                        "at": 5.0}})
        self.assertEqual(windows.load(self.path)["t1"],
                         {"w": 500, "h": 400, "x": 10, "y": 20, "at": 5.0})

    def test_a_broken_file_is_forgotten(self):
        for junk in ("{not json", "[1, 2]", '{"t1": "x"}', ""):
            self.path.write_text(junk, encoding="utf-8")
            self.assertEqual(windows.load(self.path), {}, junk)

    def test_a_missing_file_is_nothing(self):
        self.assertEqual(windows.load(self.dir / "absent.json"), {})

    def test_only_the_newest_are_kept(self):
        many = {f"t{i}": {"w": 1, "h": 1, "x": 0, "y": 0, "at": float(i)}
                for i in range(windows.KEEP + 20)}
        windows.save(self.path, many)
        kept = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(kept), windows.KEEP)
        self.assertIn(f"t{windows.KEEP + 19}", kept)
        self.assertNotIn("t0", kept)

    def test_the_launcher_says_where(self):
        run_py = (ROOT / "run.py").read_text(encoding="utf-8")
        self.assertIn('native_windows.configure(url, cfg.home / "windows.json")',
                      run_py)


if __name__ == "__main__":
    unittest.main()
