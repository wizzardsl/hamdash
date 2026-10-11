"""Guards for the frozen build.

None of this can be caught by running from source, which is the problem: a
broken spec or a wrong assumption about sys.executable produces an app that
works perfectly in development and cannot start a single card once packaged.

Run with:  python -m unittest discover -s tests
"""
import ast
import re
import sys
import json
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


class TestRunCardSeesItsOwnDirectory(unittest.TestCase):
    """`python card.py` puts the script's directory on sys.path; runpy does
    not, and --run-card is runpy. A card split over more than one file - as
    psk_reporter is, with its store - therefore imports fine from source and
    fails only in a frozen build. That is the exact shape of bug the frozen
    path keeps producing, so it is pinned here rather than left to the smoke
    test to find after a release."""

    def test_the_entry_directory_goes_on_the_path(self):
        import run
        card = ROOT / "plugins" / "psk_reporter" / "card.py"
        here = str(card.resolve().parent)
        seen = []
        with mock.patch("runpy.run_path",
                        side_effect=lambda *a, **k: seen.extend(sys.path)):
            run.run_card(str(card))
        self.assertIn(here, seen)

    def test_a_missing_entry_is_reported_not_raised(self):
        import run
        self.assertEqual(run.run_card(str(ROOT / "nope" / "card.py")), 2)


def declared_dependencies() -> set[str]:
    """Import names hamdash says it ships, from requirements.txt.

    A card may import numpy without this environment having numpy: CI
    installs only what the *host* needs, deliberately, the same way it
    installs no pywebview. Checking against what happens to be installed
    turns a card's declared dependency into a test failure on every machine
    that has not got round to installing it.
    """
    names = set()
    for line in (ROOT / "requirements.txt").read_text(
            encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        dist = re.split(r"[<>=!;\[ ]", line)[0].strip().lower()
        if dist:
            names.add(dist)
    # The two whose import name is not their package name.
    if "pywebview" in names:
        names.add("webview")
    if "pywin32" in names:
        names.update({"win32com", "pythoncom", "win32api"})
    return names


class TestEveryCardImports(unittest.TestCase):
    """A card that cannot be imported is a card that cannot start, and the
    board only says "no response"."""

    def test_what_a_card_imports_is_findable(self):
        import importlib.util
        shipped = declared_dependencies()
        for manifest in sorted((ROOT / "plugins").glob("*/card.json")):
            meta = json.loads(manifest.read_text(encoding="utf-8"))
            if meta.get("kind") != "python":
                continue
            entry = manifest.parent / meta.get("entry", "card.py")
            with self.subTest(card=meta.get("id")):
                self.assertTrue(entry.exists(), f"{entry} is missing")
                tree = ast.parse(entry.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    # Both spellings. `import cw` beside the card is how the
                    # decoder reaches its DSP, and it resolves for exactly
                    # the same reason `from store import ...` does - which
                    # is to say only because run_card arranges it.
                    if isinstance(node, ast.ImportFrom):
                        if node.level:
                            continue
                        tops = [(node.module or "").split(".")[0]]
                    elif isinstance(node, ast.Import):
                        tops = [a.name.split(".")[0] for a in node.names]
                    else:
                        continue
                    for top in tops:
                        if not top:
                            continue
                        # Either a file beside the card - which only
                        # resolves because run_card puts that directory on
                        # sys.path - something hamdash ships, or something
                        # this interpreter can find on its own.
                        beside = (manifest.parent / f"{top}.py").exists()
                        found = (beside or top.lower() in shipped
                                 or importlib.util.find_spec(top) is not None)
                        self.assertTrue(
                            found,
                            f"{entry.name} imports {top!r}, which is neither "
                            f"beside it, in requirements.txt, nor importable")


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

    def test_the_card_template_ships(self):
        """`docs/cards.md` tells an author to copy `docs/card-template`.
        Somebody who installed the binary has no repository to copy it
        from, so it has to be in the build."""
        dest = {d.replace("\\", "/") for _, d in self.datas()}
        self.assertIn("docs/card-template", dest,
                      "the docs say to copy the template and the build "
                      "does not contain it")

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
