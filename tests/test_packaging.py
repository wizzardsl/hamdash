"""Guards for the frozen build.

None of this can be caught by running from source, which is the problem: a
broken spec or a wrong assumption about sys.executable produces an app that
works perfectly in development and cannot start a single card once packaged.

Run with:  python -m unittest discover -s tests
"""
import ast
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

from hamdash import config, proc

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "hamdash.spec"


class TestCardCommand(unittest.TestCase):
    """Invariant 6: cards used to be spawned as [sys.executable, entry],
    which under PyInstaller means [the hamdash binary, 'card.py'] - the app
    launching itself with a stray argument instead of running the card."""

    def test_from_source_uses_the_interpreter(self):
        with mock.patch.object(proc, "FROZEN", False):
            self.assertEqual(proc.card_command("card.py"),
                             [sys.executable, "card.py"])

    def test_frozen_re_execs_the_binary_as_a_card_runner(self):
        with mock.patch.object(proc, "FROZEN", True):
            self.assertEqual(proc.card_command("card.py"),
                             [sys.executable, "--run-card", "card.py"])

    def test_the_launcher_understands_what_the_supervisor_sends(self):
        """The two halves have to agree on the flag, and they live in
        different files."""
        with mock.patch.object(proc, "FROZEN", True):
            argv = proc.card_command("card.py")
        launcher = (ROOT / "run.py").read_text(encoding="utf-8")
        self.assertIn(argv[1], launcher,
                      "run.py does not handle the flag proc.py sends")
        self.assertIn('sys.argv[1] == "--run-card"', launcher)


class TestBundlePaths(unittest.TestCase):
    def test_source_layout(self):
        self.assertFalse(config.FROZEN)
        self.assertEqual(config.bundle_root(), ROOT)
        self.assertTrue(config.BUNDLED_PLUGINS.is_dir())
        self.assertTrue(config.BUNDLED_STATIC.is_dir())
        self.assertTrue(config.BUNDLED_THEMES.is_dir())

    def test_meipass_is_honoured(self):
        with mock.patch.object(sys, "_MEIPASS", r"X:\unpacked", create=True):
            self.assertEqual(config.bundle_root(), Path(r"X:\unpacked"))


class TestSpec(unittest.TestCase):
    """The spec decides where files land; config.bundle_root() decides where
    they are looked for. Nothing else makes those two agree."""

    def setUp(self):
        if not SPEC.exists():
            self.skipTest("hamdash.spec is missing")
        self.text = SPEC.read_text(encoding="utf-8")

    def datas(self):
        """The `datas` list, read without executing the spec (it is only
        valid inside PyInstaller, where Analysis and friends are injected)."""
        tree = ast.parse(self.text)
        for node in tree.body:
            if (isinstance(node, ast.Assign)
                    and any(getattr(t, "id", "") == "datas" for t in node.targets)):
                return [tuple(ast.literal_eval(e)) for e in node.value.elts]
        self.fail("no `datas` assignment found in hamdash.spec")

    def test_bundles_what_the_app_looks_for(self):
        dest = {d for _, d in self.datas()}
        rel = lambda p: str(p.relative_to(ROOT)).replace("\\", "/")
        for path in (config.BUNDLED_PLUGINS, config.BUNDLED_STATIC):
            with self.subTest(path=rel(path)):
                self.assertIn(
                    rel(path), {d.replace("\\", "/") for d in dest},
                    f"{rel(path)} is read at runtime but the spec does not "
                    f"bundle it there; a frozen build would not find it")

    def test_sources_exist(self):
        for src, _ in self.datas():
            with self.subTest(src=src):
                self.assertTrue((ROOT / src).exists(), f"{src} is missing")

    def test_sdk_is_a_hidden_import(self):
        """Cards are data files run by runpy, so nothing the analyser follows
        ever reaches hamdash.sdk."""
        self.assertIn("hamdash.sdk", self.text)

    def test_stays_onedir(self):
        """onefile would unpack the whole bundle once per card process."""
        self.assertIn("COLLECT(", self.text)
        self.assertIn("exclude_binaries=True", self.text)

    def test_windowed_build_has_the_stdout_shim(self):
        """console=False means sys.stdout is None, and every print() in the
        host - including the card log drain - would raise on NoneType."""
        if not re.search(r"console\s*=\s*False", self.text):
            self.skipTest("spec builds with a console")
        launcher = (ROOT / "run.py").read_text(encoding="utf-8")
        self.assertIn("ensure_streams", launcher)


if __name__ == "__main__":
    unittest.main()
