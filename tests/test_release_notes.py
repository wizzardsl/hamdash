"""Guard the release extractor.

The release workflow publishes whatever this prints. A silent failure here
means a tagged release with wrong or empty notes, which can't be edited out
of the tag. Run with:  python -m unittest discover -s tests
"""
import unittest

from tools.release_notes import (declared_version, duplicate_headings,
                                 section)

CHANGELOG = """\
# Changelog

## [Unreleased]

### Added
- nothing yet

## [0.3.4]

### Fixed
- the thing, with an arrow: a -> b

### Added
- another thing

## [0.3.3]

### Fixed
- the earlier thing

## [0.3.2] and earlier

Not recorded.
"""


class TestSection(unittest.TestCase):
    def test_extracts_one_version_only(self):
        body = section(CHANGELOG, "0.3.4")
        self.assertIn("the thing, with an arrow", body)
        self.assertIn("another thing", body)
        self.assertNotIn("the earlier thing", body,
                         "bled into the next version's section")
        self.assertNotIn("nothing yet", body,
                         "picked up Unreleased")

    def test_last_section_runs_to_end_of_file(self):
        self.assertIn("Not recorded.", section(CHANGELOG, "0.3.2"))

    def test_missing_version_refuses(self):
        with self.assertRaises(SystemExit) as cm:
            section(CHANGELOG, "9.9.9")
        self.assertIn("9.9.9", str(cm.exception))

    def test_empty_section_refuses(self):
        empty = "# Changelog\n\n## [1.0.0]\n\n## [0.9.0]\n\n- something\n"
        with self.assertRaises(SystemExit) as cm:
            section(empty, "1.0.0")
        self.assertIn("empty", str(cm.exception))

    def test_version_prefix_is_not_a_partial_match(self):
        """'0.3.3' must not match the '[0.3.2] and earlier' style heading."""
        body = section(CHANGELOG, "0.3.3")
        self.assertIn("the earlier thing", body)
        self.assertNotIn("Not recorded.", body)


class TestDeclaredVersion(unittest.TestCase):
    def test_reads_the_string(self):
        self.assertEqual(
            declared_version('"""doc"""\n__version__ = "1.2.3"\n'), "1.2.3")

    def test_refuses_when_absent(self):
        with self.assertRaises(SystemExit):
            declared_version("# nothing here\n")

    def test_matches_the_real_package(self):
        """The shipped __init__ must stay parseable by the release job."""
        from pathlib import Path
        init = Path(__file__).resolve().parent.parent / "hamdash" / "__init__.py"
        self.assertRegex(
            declared_version(init.read_text(encoding="utf-8")),
            r"^\d+\.\d+\.\d+")


if __name__ == "__main__":
    unittest.main()


class TestNoSectionRepeatsAHeading(unittest.TestCase):
    """Entries landing on top of each other, caught on push.

    Twice now a release has gone to be cut with `Added/Fixed/Added/Fixed`
    in one section - a session adds `### Added` above an `### Added` that
    was already there, which is easy to do and invisible until the notes
    are published. Both times it was spotted by eye.

    `release_notes.py` refuses it at tag time, which is the backstop that
    matters because a tag cannot be edited afterwards. This is the one
    that fires first, on every push, including for `Unreleased` - where
    the mistake is actually made and where it is still free to fix.
    """

    REPEATED = "\n".join(
        ["### Added", "- a", "### Fixed", "- b", "### Added", "- c"])
    CLEAN = "\n".join(["### Added", "- a", "### Fixed", "- b"])

    def test_the_detector_sees_a_repeat(self):
        self.assertEqual(duplicate_headings(self.REPEATED), ["### Added"])

    def test_the_detector_leaves_a_clean_section_alone(self):
        self.assertEqual(duplicate_headings(self.CLEAN), [])

    def test_every_section_of_the_real_changelog_is_clean(self):
        import re
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent
                / "CHANGELOG.md").read_text(encoding="utf-8")
        # Split on the version headings, keeping each one's name.
        parts = re.split(r"^(## .+)$", text, flags=re.M)
        bad = []
        for i in range(1, len(parts) - 1, 2):
            name, body = parts[i].strip(), parts[i + 1]
            for h in duplicate_headings(body):
                bad.append(f"{name} repeats {h}")
        self.assertEqual(bad, [], "\n".join(
            ["Merge these into one block each - the release notes are "
             "copied from the section verbatim:", *bad]))
