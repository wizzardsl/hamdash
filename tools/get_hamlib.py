"""Fetch Hamlib's `rigctld`, which pip cannot install.

Hamlib is a native program, not a Python package, so it cannot go in
`requirements.txt`. This fetches the official Windows build into the
hamdash config directory, where `services/rigs.find_rigctld` looks for it -
so after running this once, a Hamlib rig in Settings just works.

**hamdash does not redistribute Hamlib.** You download it here, from the
Hamlib project's own release, which is why this is a script you run rather
than something bundled: `rigctl` and `rigctld` are GPL-2.0-or-later and
hamdash is MIT, and shipping them in our releases would put their licence
and a source offer on every one of our artifacts. Running them as a
separate process, which is what hamdash does, is nobody's problem.

The download is checked against the SHA-256 that Hamlib publishes beside
it. Worth being plain about what that is and is not: both come from the
same release, so it catches a truncated or tampered-with *download*, not a
tampered-with *release*. Hamlib also signs everything with a GPG key, which
is the stronger check if you have their key.

    python tools/get_hamlib.py                 # into the hamdash config dir
    python tools/get_hamlib.py --version 4.7.2
    python tools/get_hamlib.py --home ./dev

Only Windows has binaries in the Hamlib release. macOS and Linux get the
one-line package-manager command instead, because building from their
tarball is not something a script should do behind your back.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hamdash.config import default_home                  # noqa: E402

VERSION = "4.7.2"
BASE = "https://github.com/Hamlib/Hamlib/releases/download"
# What to tell someone whose platform Hamlib does not ship binaries for.
PACKAGED = {
    "darwin": "brew install hamlib",
    "linux": "sudo apt install libhamlib-utils   (or your distro's "
             "hamlib / hamlib-utils package)",
}


def asset_for(version: str) -> tuple[str, str]:
    """The zip and the checksum file for this machine."""
    width = "w64" if sys.maxsize > 2 ** 32 else "w32"
    return (f"hamlib-{width}-{version}.zip", f"SHA256SUM-{width}-{version}")


def fetch(url: str, what: str) -> bytes:
    print(f"  {what}: {url}")
    with urlopen(url, timeout=60) as r:            # noqa: S310 - pinned host
        return r.read()


def wanted_sha(sums: bytes, name: str) -> str:
    """The digest for `name` out of a SHA256SUM file.

    The format is `<hex>  <filename>`, and the file lists every artifact in
    the release, so the name has to be matched rather than the first line
    taken.
    """
    for line in sums.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) >= 2 and Path(parts[-1]).name == name:
            return parts[0].lower()
    raise SystemExit(f"{name} is not listed in the checksum file")


def unpack(blob: bytes, into: Path) -> Path:
    """Extract, refusing any member that would land outside `into`.

    A zip can name `../` in its members and write anywhere the process can.
    Nothing in Hamlib's does, which is exactly why it would go unnoticed.
    """
    into.mkdir(parents=True, exist_ok=True)
    root = into.resolve()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for member in z.namelist():
            dest = (root / member).resolve()
            if dest != root and root not in dest.parents:
                raise SystemExit(f"refusing {member!r}: it escapes {into}")
        z.extractall(root)
    return root


def find_rigctld_under(root: Path) -> Path | None:
    for name in ("rigctld.exe", "rigctld"):
        for found in sorted(root.glob(f"**/{name}"), reverse=True):
            if found.is_file():
                return found
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", default=VERSION)
    ap.add_argument("--home", default=None,
                    help="hamdash config dir (default: the usual one)")
    ap.add_argument("--force", action="store_true",
                    help="download again even if rigctld is already there")
    args = ap.parse_args(argv)

    home = Path(args.home).expanduser() if args.home else default_home()
    into = home / "hamlib"

    if sys.platform != "win32":
        how = PACKAGED.get(sys.platform, "your package manager's hamlib")
        print(f"Hamlib publishes binaries for Windows only.\n"
              f"On {sys.platform}, install it with:\n\n    {how}\n\n"
              f"hamdash finds rigctld on PATH, so nothing else is needed.")
        return 1

    have = find_rigctld_under(into) if into.exists() else None
    if have and not args.force:
        print(f"already here: {have}\n(--force to fetch it again)")
        return 0

    zip_name, sums_name = asset_for(args.version)
    print(f"Hamlib {args.version} -> {into}")
    sums = fetch(f"{BASE}/{args.version}/{sums_name}", "checksums")
    want = wanted_sha(sums, zip_name)
    blob = fetch(f"{BASE}/{args.version}/{zip_name}",
                 f"{zip_name} ({len(sums) and '~3 MB'})")
    got = hashlib.sha256(blob).hexdigest()
    if got != want:
        print(f"\nSHA-256 mismatch - not unpacking it.\n"
              f"  expected {want}\n  got      {got}")
        return 2
    print(f"  sha256 ok: {got[:16]}...")

    unpack(blob, into)
    exe = find_rigctld_under(into)
    if not exe:
        print(f"unpacked, but no rigctld under {into}")
        return 3
    print(f"\nrigctld: {exe}")
    print("hamdash will find it there. Add a Hamlib radio in "
          "Settings -> Radio and pick your rig's model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
