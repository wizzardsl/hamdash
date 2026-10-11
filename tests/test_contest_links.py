"""Clicking a contest goes somewhere, and says where.

The card had a `rules` link all along and it had never once rendered: QSO
Dates' bulk export carries a `website` field that is empty on every one of
its 1000 rows, and their site has no per-contest page to fall back on. A
link that is always absent is indistinguishable from no feature at all,
which is how it was reported - as "do these have a link?".

So the name links to a search, and the thing worth guarding is the
honesty: a search must be marked as a search, because a link dressed as
the organiser's own rules page is a wrong answer that looks like a right
one. The `website` field is still preferred, so this needs no change the
day they start filling it in.

Run with:  python -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

PAGE = (Path(__file__).resolve().parent.parent / "plugins" / "spots"
        / "index.html").read_text(encoding="utf-8")


def between(start, end):
    """The source between two markers, or "" if the page has neither - so a
    missing helper fails a test by name instead of erroring on import."""
    try:
        return PAGE[PAGE.index(start):PAGE.index(end)]
    except ValueError:
        return ""


def name_markup():
    m = re.search(r'<div class="name">(.*?)</div>', PAGE, re.S)
    return m.group(1) if m else ""


RULES = between("function rules(c)", "function paintContests")


class TestTheNameIsTheLink(unittest.TestCase):
    def test_the_name_is_wrapped_in_an_anchor(self):
        """It was a four-letter `rules` word at the end of a dim meta line.
        The thing you want to click is the contest."""
        name = name_markup()
        self.assertIn("<a href=", name)
        self.assertIn("esc(c.name)", name)

    def test_it_opens_outside_the_app(self):
        """Invariant 2: a target=_blank link is intercepted by hamdash.js
        and routed through HD.openExternal. Without the target it would
        navigate the tile's iframe to somebody's website."""
        name = name_markup()
        self.assertIn('target="_blank"', name)
        self.assertIn('rel="noopener"', name)


class TestASearchIsMarkedAsOne(unittest.TestCase):
    def test_the_real_field_is_preferred(self):
        self.assertIn("c.website", RULES)
        self.assertLess(RULES.index("c.website"), RULES.index("duckduckgo"),
                        "the search must be the fallback, not the first choice")

    def test_a_search_is_not_claimed_to_be_the_rules(self):
        """`known` is what drives the dotted underline and the tooltip."""
        self.assertIn("known: true", RULES)
        self.assertIn("known: false", RULES)

    def test_the_dotted_underline_exists_to_be_used(self):
        self.assertIn(".guess", PAGE)
        self.assertIn("text-decoration-style: dotted", PAGE)
        self.assertIn("'guess'", PAGE)

    def test_the_query_is_encoded(self):
        """A contest name has spaces, ampersands and slashes in it - the
        ARRL 10 GHz & Up Contest has two of the three."""
        self.assertIn("encodeURIComponent", RULES)

    def test_only_an_http_url_is_followed(self):
        """The field is somebody else's free text. A `javascript:` or
        `data:` value in it must not become a link the operator clicks."""
        self.assertIn("^https?", RULES)


class TestTheSourceIsCredited(unittest.TestCase):
    def test_the_card_says_whose_calendar_it_is(self):
        """The export's own licence is CC0 "free to use with attribution",
        and only the docs carried it."""
        self.assertIn("qsodates.com", PAGE)
        self.assertIn("QSO Dates", PAGE)


if __name__ == "__main__":
    unittest.main()
