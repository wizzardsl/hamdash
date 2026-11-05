"""A flexible input must be allowed to shrink.

A flex item will not shrink below its content, and a text input's content
is about twenty characters wide whatever is typed in it. So `flex: 1` on
an input without `min-width: 0` holds a row open at ~200 px: on a narrow
tile the beacon map's Find button was pushed past the edge and the card
grew a sideways scrollbar - found in a screenshot from the Windows runner,
measured at 262 px of content in a 230 px frame.

Run with:  python -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def rules(text):
    for m in RULE.finditer(text):
        yield m.group(1).strip(), m.group(2)


class TestFlexibleInputsShrink(unittest.TestCase):
    def test_every_flexible_input_can_shrink(self):
        bad = []
        files = list((ROOT / "plugins").rglob("*.html")) + \
            list((ROOT / "hamdash" / "static").rglob("*.html")) + \
            list((ROOT / "hamdash" / "static").rglob("*.css"))
        for path in files:
            if "vendor" in path.parts:
                continue
            for selector, body in rules(path.read_text(encoding="utf-8")):
                if not re.search(r"\binput\b", selector.split(",")[-1]):
                    continue
                if re.search(r"(^|;|\s)flex\s*:\s*1\b", body) and \
                        not re.search(r"min-width\s*:\s*0", body):
                    bad.append(f"{path.relative_to(ROOT)}: {selector}")
        self.assertEqual(bad, [], "add min-width: 0 so it can shrink")


if __name__ == "__main__":
    unittest.main()
