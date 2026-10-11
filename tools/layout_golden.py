#!/usr/bin/env python3
"""Write tests/fixtures/layouts.expected.json from the host's normaliser.

    python tools/layout_golden.py

The golden file is the contract between two implementations in different
languages: `hamdash/config.py` normalises a layout on the host, and
`hamdash/static/js/layout.js` normalises it in the board, and both rewrite
`layout.json`. Generating it from the host side makes the host the reference
and leaves the browser to agree or fail.

Run it when a fixture is added or the normaliser deliberately changes, and
read the diff before committing: a change here is a change to what happens
to somebody's saved board.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hamdash.config import normalise_layout            # noqa: E402
from tests.test_layout_agreement import canonical      # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "layouts.json"
GOLDEN = ROOT / "tests" / "fixtures" / "layouts.expected.json"


def main() -> int:
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"]
    out = [{"why": c["why"],
            "tiles": canonical(normalise_layout(c["tiles"]), c["tiles"])}
           for c in cases]
    GOLDEN.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN.relative_to(ROOT)} ({len(out)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
