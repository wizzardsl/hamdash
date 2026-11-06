# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for hamdash.

    pip install pyinstaller
    pyinstaller hamdash.spec        ->  dist/hamdash/

**onedir, not onefile, deliberately.** Every python card is a re-exec of this
same binary (`proc.card_command` -> `run.py --run-card`). A onefile build
unpacks its entire bundle to a temp directory on every launch, and every card
is a launch: five cards would mean five unpacks of the same tens of megabytes
before the board finished painting, plus five copies on disk.

The layout below has to match `config.bundle_root()`, which resolves
`sys._MEIPASS`:  <root>/plugins and <root>/hamdash/static.
"""

import sys


datas = [
    ("hamdash/static", "hamdash/static"),   # board, client js, themes
    ("plugins", "plugins"),                 # bundled cards
    # The card template, because the docs say "copy this folder" and
    # somebody who installed the binary has no repository to copy it from.
    ("docs/card-template", "docs/card-template"),
]

hiddenimports = [
    # Cards do `from hamdash.sdk import Card`, but they are data files run by
    # runpy - the analyser never follows them, so nothing pulls the SDK in.
    "hamdash.sdk",
    # Imported inside functions in run.py; named here so a refactor that moves
    # the import doesn't silently drop the server from the build.
    "waitress",
    # The spectrum card needs these, and a card is a data file the analyser
    # never follows. Without them the card is dead in every downloadable
    # build while working perfectly from source.
    "numpy",
    "sounddevice",
]

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Nothing here imports tkinter, but pywebview's dependency graph offers it
    # on some platforms and it is large.
    excludes=["tkinter", "test", "unittest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="hamdash",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # No console window: this is a desktop app, and on Windows it is normally
    # launched from a shortcut. run.ensure_streams() covers the consequence,
    # which is that sys.stdout is None and every print() would otherwise
    # raise. Diagnostics go to <config dir>/hamdash.log instead.
    console=False,
    # tools/make_icon.py draws it. Windows only: a macOS or Linux build of
    # this onedir has no use for an .ico, and PyInstaller would try to
    # convert it (which needs Pillow) for nothing.
    icon="hamdash/static/icon/hamdash.ico" if sys.platform == "win32" else None,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="hamdash",
)

# macOS: a real application, not a bare binary in a folder. Double-clicking
# that opened a Terminal beside it and showed no icon; an .app is what a
# Mac user expects to drag to Applications. The onedir COLLECT above is
# what goes inside it, so cards still re-exec the same binary (invariant 6).
if sys.platform == "darwin":
    import re
    _version = re.search(r'__version__ = "([^"]+)"',
                         open("hamdash/__init__.py", encoding="utf-8").read()).group(1)
    app = BUNDLE(
        coll,
        name="hamdash.app",
        icon="hamdash/static/icon/hamdash.icns",
        bundle_identifier="io.github.wizzardsl.hamdash",
        version=_version,
        info_plist={
            "CFBundleName": "hamdash",
            "CFBundleDisplayName": "hamdash",
            "CFBundleShortVersionString": _version,
            "CFBundleVersion": _version,
            "LSApplicationCategoryType": "public.app-category.utilities",
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            # The radio's sound card is an audio input, and macOS will not
            # hand one to an app that does not say why - the decoder, the
            # waterfall and Listen would all be silent with no prompt.
            "NSMicrophoneUsageDescription":
                "hamdash listens to your radio's audio through its sound card "
                "to decode signals, draw the waterfall and play it elsewhere.",
            # macOS 15 asks before an app talks to the local network, and
            # without this says only "find devices on local networks". It
            # is what lets hamdash reach flrig, rigctld or WSJT-X on another
            # computer, and a board opened from another room.
            "NSLocalNetworkUsageDescription":
                "hamdash talks to your radio's control software (flrig, "
                "rigctld, WSJT-X) and serves the board to other computers "
                "on your network.",
            # The board is a page served by hamdash itself on this machine,
            # over plain http.
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        },
    )
