#!/usr/bin/env python3
"""Install the latest published release on Windows and look at its first run.

    python tools/check_installed_release.py        # on a Windows runner

What a new user meets, from the real download: the installer from the
latest GitHub release, installed for the current user, started the way the
Start-menu shortcut starts it (with its window), and photographed once the
board is on screen. Run by windows-check (`-f script=...`); it is not a
unit test and needs a desktop session.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

API = "https://api.github.com/repos/wizzardsl/hamdash/releases/latest"
UA = {"User-Agent": "hamdash release check"}


def get(url, timeout=10):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                timeout=timeout) as r:
        return r.read()


def screenshot(path: str) -> None:
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms, System.Drawing;"
        "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
        "$i=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
        "$g=[System.Drawing.Graphics]::FromImage($i);"
        "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
        f"$i.Save('{path}')")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def main() -> int:
    rel = json.loads(get(API))
    asset = next(a for a in rel["assets"] if a["name"].startswith("hamdash-setup-"))
    print("release", rel["tag_name"], "installer", asset["name"], asset["size"], flush=True)
    setup = Path("hamdash-setup.exe")
    setup.write_bytes(get(asset["browser_download_url"], timeout=120))

    subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                    "/CURRENTUSER", "/LOG=release-install.log"], check=True)
    exe = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "hamdash" / "hamdash.exe"
    print("installed:", exe.exists(), exe, flush=True)
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/hamdash.lnk"
    print("start menu shortcut:", start_menu.exists(), flush=True)

    home = Path(os.environ.get("HAMDASH_HOME") or Path(os.environ["APPDATA"]) / "hamdash")
    home.mkdir(parents=True, exist_ok=True)
    (home / "running.json").unlink(missing_ok=True)
    app = subprocess.Popen([str(exe)])           # as the shortcut does: no flags
    url = None
    for _ in range(90):
        time.sleep(1)
        try:
            url = json.loads((home / "running.json").read_text())["url"]
            stats = json.loads(get(url + "/api/bus/stats", 3))
            if stats.get("subscribers"):
                break
        except (OSError, ValueError, KeyError):
            continue
    else:
        print("FAIL the board never reached the bus", flush=True)
        app.kill()
        return 1
    time.sleep(12)                               # let it paint
    Path("shots").mkdir(exist_ok=True)
    screenshot(str(Path("shots/release-first-run.png").resolve()))
    for path in ("/api/shell", "/api/health", "/api/updates"):
        print(path, get(url + path).decode()[:300], flush=True)
    req = urllib.request.Request(url + "/api/shutdown", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", **UA})
    urllib.request.urlopen(req, timeout=10).read()
    app.wait(30)
    print("PASS installed release ran and closed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
