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

datas = [
    ("hamdash/static", "hamdash/static"),   # board, client js, themes
    ("plugins", "plugins"),                 # bundled cards
]

hiddenimports = [
    # Cards do `from hamdash.sdk import Card`, but they are data files run by
    # runpy - the analyser never follows them, so nothing pulls the SDK in.
    "hamdash.sdk",
    # Imported inside functions in run.py; named here so a refactor that moves
    # the import doesn't silently drop the server from the build.
    "waitress",
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
