"""`[hidden]` does nothing to an element the stylesheet gives a `display`.

The user agent's `[hidden] { display: none }` lives in the UA origin, and any
author declaration beats it outright - specificity never enters into it. So
an element styled `display: flex` and then hidden in code stays exactly where
it was, filtering nothing or offering a control that should not exist.

This has now been walked into three times (`.dirs`, `.peek`, `.sync-svc`,
`.bar`, and then `.chip` with the note already written in CLAUDE.md), which
is what this test is for: it is cheap, and the failure it prevents is
invisible on screen - the attribute is set, the DOM agrees, and the thing is
still there.

Run with:  python -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Elements written with a literal `hidden`, and elements a script hides.
HIDDEN_MARKUP = re.compile(r"<[a-z][^>]*\shidden(?=[\s/>])[^>]*>", re.I)
CLASSES = re.compile(r'class="([^"]+)"')
HIDDEN_JS = re.compile(r"""\$\(['"]([.#][\w-]+)['"]\)\.hidden\s*=""")
# `.foo { ... display: ... }` - a simple class or id rule that sets display.
RULE = re.compile(r"(?m)^\s*([.#][\w-]+)\s*\{([^}]*)\}")


def scopes():
    """Files grouped by what can see what.

    A card page is one file: its markup and its stylesheet live in the same
    place, so a rule and the element it fails to hide do too. The board is
    not - its markup is `index.html` and its `display` declarations are in
    `app.css` - and comparing each file against itself therefore never
    looked at the board at all. The whole shell went unguarded until a row
    added to the rail walked into this for the fifth time and the test said
    nothing.

    Not one global pile, though: card pages share class names - `.bar` is
    in two of them - so one card guarding `.bar[hidden]` would cover for
    another that had not.
    """
    for card in sorted((ROOT / "plugins").glob("*/*.html")):
        yield card.relative_to(ROOT).as_posix(), [card]
    static = ROOT / "hamdash" / "static"
    shell = sorted(static.rglob("*.html")) + sorted(static.rglob("*.css"))
    yield "the board shell", shell


class TestHiddenBeatsDisplay(unittest.TestCase):
    def test_anything_hidden_in_code_is_actually_hidden(self):
        bad = []
        for where, paths in scopes():
            text = "\n".join(p.read_text(encoding="utf-8") for p in paths)

            # What this file gives an author `display`.
            styled = {sel for sel, body in RULE.findall(text)
                      if re.search(r"(?<![-\w])display\s*:", body)}
            # What it already guards.
            guarded = set(re.findall(r"([.#][\w-]+)\[hidden\]", text))

            want = set()
            for tag in HIDDEN_MARKUP.findall(text):
                for names in CLASSES.findall(tag):
                    want.update("." + n for n in names.split())
            want.update(HIDDEN_JS.findall(text))

            for sel in sorted(want & styled - guarded):
                bad.append(f"{where}: "
                           f"`{sel}` is hidden in code but the stylesheet "
                           f"gives it a display, so it stays on screen. "
                           f"Add `{sel}[hidden] {{ display: none; }}`.")
        self.assertEqual(bad, [], "\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
