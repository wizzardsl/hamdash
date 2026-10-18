"""The logbook card's Push buttons follow the log, not just the last sync.

The bug this guards: the card read its "Push QRZ (n)" counts when it
loaded and when a sync finished, and at no other time. A push therefore
left the buttons at "nothing to send", disabled - and a contact logged
afterwards could not be pushed until the card was reloaded. Reported
exactly that way: three contacts logged and pushed, a fourth logged, no
way to push it.

The same gap covered deletes, and worse: the card did not subscribe to
`log.qso_deleted` at all, so a contact removed from another tile or
another browser never reached it.

Run with:  python -m unittest discover -s tests
"""
import json
import re
import unittest
from pathlib import Path

CARD = Path(__file__).resolve().parent.parent / "plugins" / "logbook"
PAGE = (CARD / "index.html").read_text(encoding="utf-8")
MANIFEST = json.loads((CARD / "card.json").read_text(encoding="utf-8"))


def braces_after(pattern: str) -> str:
    """The text inside the first `{ ... }` that follows `pattern`."""
    m = re.search(pattern, PAGE)
    assert m, f"not found: {pattern}"
    start = PAGE.index("{", m.end() - 1)
    depth, i = 1, start + 1
    while depth:
        depth += {"{": 1, "}": -1}.get(PAGE[i], 0)
        i += 1
    return PAGE[start + 1:i - 1]


class TestThePushCountFollowsTheLog(unittest.TestCase):
    def test_a_contact_arriving_refreshes_the_push_buttons(self):
        """Logged here, by WSJT-X, by an import or by another tile - every
        one of them arrives as `log.qso_added`."""
        body = braces_after(r"HD\.on\('log\.qso_added',\s*\([^)]*\)\s*=>\s*\{")
        self.assertIn("refreshSync()", body)

    def test_a_contact_logged_here_refreshes_them_itself(self):
        body = braces_after(r"\$\('#save'\)\.onclick\s*=\s*async\s*\(\)\s*=>\s*\{")
        self.assertIn("refreshSync()", body)

    def test_a_delete_refreshes_them(self):
        body = braces_after(r"\.querySelector\('\.del'\)\.onclick\s*="
                            r"\s*async\s*\([^)]*\)\s*=>\s*\{")
        self.assertIn("refreshSync()", body)

    def test_a_delete_elsewhere_reaches_this_card(self):
        self.assertIn("log.qso_deleted", MANIFEST["subscribes"],
                      "the board filters what a card hears by its subscribes")
        body = braces_after(r"HD\.on\('log\.qso_deleted',\s*\([^)]*\)\s*=>\s*\{")
        self.assertIn("refresh()", body)
        self.assertIn("refreshSync()", body)


if __name__ == "__main__":
    unittest.main()
