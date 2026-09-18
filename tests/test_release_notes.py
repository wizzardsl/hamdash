"""Guard the release extractor.

The release workflow publishes whatever this prints. A silent failure here
means a tagged release with wrong or empty notes, which can't be edited out
of the tag. Run with:  python -m unittest discover -s tests
"""
import unittest

from tools.release_notes import declared_version, section

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
