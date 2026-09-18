#!/usr/bin/env python3
"""Extract one release's notes from CHANGELOG.md.

    python tools/release_notes.py 0.3.4

Prints the body of that version's section to stdout. Refuses, loudly, in the
two cases that produce a bad release rather than a failed one:

  - the tag disagrees with hamdash.__version__ (this has already happened
    once: __init__ said 0.1.0 while the app was 0.3.3)
  - there is no CHANGELOG section for the version, which would otherwise
    publish a release with empty notes

Reads __version__ with a regex rather than importing hamdash, so the release
job needs no runtime dependencies installed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
INIT = ROOT / "hamdash" / "__init__.py"


def declared_version(text: str) -> str:
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if not m:
        raise SystemExit(f"no __version__ found in {INIT}")
    return m.group(1)


def section(changelog: str, version: str) -> str:
    """The body under '## [<version>]', up to the next '## ' heading."""
    start = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n", changelog, re.M)
    if not start:
        raise SystemExit(
            f"CHANGELOG.md has no '## [{version}]' section.\n"
            f"Add one (move the Unreleased entries into it) before tagging.")
    rest = changelog[start.end():]
    end = re.search(r"^## ", rest, re.M)
    body = (rest[:end.start()] if end else rest).strip()
    if not body:
        raise SystemExit(
            f"the '## [{version}]' section is empty; a release with no notes "
            f"is worse than no release")
    return body


def main(argv: list[str]) -> int:
    # The changelog contains the arrows from the pywebview recursion
    # notes. A cp1252 Windows console cannot encode those, so writing
    # notes to a pipe dies on U+2192 unless stdout is UTF-8.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    if len(argv) != 2:
        raise SystemExit(f"usage: {argv[0]} <version>   e.g. 0.3.4")
    version = argv[1].lstrip("v")

    declared = declared_version(INIT.read_text(encoding="utf-8"))
    if declared != version:
        raise SystemExit(
            f"version mismatch: tag says {version}, "
            f"{INIT.relative_to(ROOT)} says {declared}.\n"
            f"Bump __version__ and retag; the tree is the source of truth.")

    print(section(CHANGELOG.read_text(encoding="utf-8"), version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
