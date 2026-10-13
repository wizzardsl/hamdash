"""Every link in the documentation goes somewhere.

The README is the landing page and the docs are now a set of pages that link
to each other, which is a nice shape right up until a file is renamed and the
front page quietly starts pointing at nothing. A dead link there is the first
thing a visitor meets, and it is the kind of rot nobody notices from inside
the repository.

Only relative links are checked. Whether somebody else's site is up is not
this suite's business.

Run with:  python -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def pages():
    yield ROOT / "README.md"
    yield ROOT / "CLAUDE.md"
    # The front-door files a visitor meets before any of the docs. They
    # carry links out to LICENSE, docs/ and each other, and nothing else
    # was checking them.
    for name in ("CONTRIBUTING.md", "SECURITY.md"):
        here = ROOT / name
        if here.exists():
            yield here
    yield from sorted(ROOT.glob("docs/**/*.md"))


def headings(text):
    """GitHub's anchor for each heading: lowercased, punctuation dropped,
    spaces to hyphens."""
    out = set()
    for line in text.splitlines():
        if line.startswith("#"):
            t = line.lstrip("#").strip().lower()
            out.add(re.sub(r"[^a-z0-9 _-]", "", t).replace(" ", "-"))
    return out


class TestTheDocumentationHangsTogether(unittest.TestCase):
    def test_every_relative_link_has_a_file(self):
        bad = []
        for page in pages():
            text = page.read_text(encoding="utf-8")
            for label, target in LINK.findall(text):
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                path = target.split("#", 1)[0]
                if not path:
                    continue
                dest = (page.parent / path).resolve()
                if not dest.exists():
                    bad.append(f"{page.name}: [{label}]({target})")
        self.assertEqual(bad, [], "links pointing at nothing:\n" +
                         "\n".join(bad))

    def test_every_anchor_is_a_real_heading(self):
        """A `#section` that no longer exists scrolls nowhere and looks like
        the page failed to load."""
        bad = []
        for page in pages():
            text = page.read_text(encoding="utf-8")
            for label, target in LINK.findall(text):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                path, _, anchor = target.partition("#")
                if not anchor:
                    continue
                dest = page if not path else (page.parent / path).resolve()
                if not dest.exists() or dest.is_dir():
                    continue
                if anchor not in headings(dest.read_text(encoding="utf-8")):
                    bad.append(f"{page.name}: [{label}]({target})")
        self.assertEqual(bad, [], "anchors that go nowhere:\n" +
                         "\n".join(bad))

    def test_the_readme_is_a_landing_page(self):
        """It was 1071 lines once, which is a scroll rather than a front
        page. The topic pages exist so it can stay short."""
        lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        self.assertLess(len(lines), 250,
                        "the README is growing back into one long page; "
                        "put the detail in docs/ and link to it")

    def test_the_index_lists_every_page(self):
        """docs/README.md is what somebody browsing the folder sees."""
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        missing = [p.name for p in sorted(ROOT.glob("docs/*.md"))
                   if p.name != "README.md" and p.name not in index]
        self.assertEqual(missing, [],
                         f"pages nothing links to: {missing}")


if __name__ == "__main__":
    unittest.main()
