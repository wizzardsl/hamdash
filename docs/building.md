# Building and testing

Running the tests, and turning the tree into a desktop application.

- [The board's own tests](#the-boards-own-tests)
- [Building a desktop app](#building-a-desktop-app)

*[&larr; back to the README](../README.md)*

---

## The board's own tests

`hamdash/static/js/layout.js` is what a board *is* — normalising, docking,
undocking, overlap, what reaches `layout.json` — with no DOM in it, split out
of `grid.js` so the part that can be wrong invisibly is the part that gets
tested. `grid.js` keeps the pixels.

```bash
node --test 'tests/js/*.test.js'
```

No npm and no package.json: Node's own runner, and the thing being tested has
no dependencies either. The Python suite covers the host's half, and both
check themselves against `tests/fixtures/layouts.expected.json` — the host
and the board both rewrite `layout.json`, so they have to agree about what a
layout becomes.

## Building a desktop app

```bash
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean hamdash.spec   # -> dist/hamdash/
python tools/smoke_frozen.py                   # start it and make it run a card
```

The result is a folder, not a single file, and that is deliberate. Every
python card is started by re-execing the app itself — frozen, `sys.executable`
is the hamdash binary rather than an interpreter, so the binary is also the
card runner (`hamdash --run-card card.py`). A onefile build unpacks its whole
bundle on every launch, and every card is a launch: five cards would mean five
unpacks of the same tens of megabytes before the board finished painting.

Run the smoke test after any change to the spec or to how cards are spawned.
From source `sys.executable` is a real interpreter and the re-exec path never
runs, so a broken build is invisible in development — the app serves the board
perfectly and cannot start a single card.

**Third-party cards in a frozen build** work, with one limit: the card is run
by the app's bundled interpreter, so it can import the standard library,
`hamdash.sdk`, Flask and requests, and nothing else. A card that imports
something outside that fails at startup and the tile shows why:

```
ModuleNotFoundError: No module named 'numpy'
```

which is a readable answer rather than a crash, but it does mean a card with
dependencies needs a source install, or those dependencies added to
`hamdash.spec` and a rebuild.

**On Linux the window needs GTK, and pip does not bring it.** pywebview
installs from pip on Linux without a toolkit to drive, and the app then has
no window to open. It says so in the log and carries on in your web browser,
the same as `--browser`. For the window, use the system's GTK bindings and a
virtual environment that can see them:

```bash
sudo apt install python3-gi gir1.2-webkit2-4.1
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt pyinstaller
```

A build made from that environment carries GTK with it (about 300 MB,
against 93 MB without) and opens its own window. CI builds Linux exactly
this way, and checks the window on a virtual screen:

```bash
xvfb-run -a python tools/smoke_frozen.py --window
```

because a build with no toolkit passes the plain smoke test - it carries on
in the browser - and the only thing missing is the window. It still relies on the
machine having WebKitGTK installed, which every desktop Linux with a GNOME
or GTK browser has.

**The Windows installer** is `installer/hamdash.iss` (Inno Setup 6), built
from `dist/hamdash` by `build.yml` and tested there the way an operator
would use it: installed silently, smoke-tested, installed again over itself
as an upgrade (a stale file planted in the old copy must be gone, the
settings folder must not be), and uninstalled (the settings folder must
survive that too). Its `AppId` must never change - it is how the next
installer finds this one to upgrade.

```bash
iscc /DAppVersion=1.4.0 installer\hamdash.iss    # -> dist\hamdash-setup.exe
```

**A Windows machine on demand.** `windows-check.yml` builds the app on
GitHub's Windows runner, runs both smoke tests, opens the board in its real
WebView2 window, photographs the screen, and keeps the screenshot and the
log as an artifact. It can also run one script from the repository:

```bash
gh workflow run windows-check.yml
gh workflow run windows-check.yml -f script=tools/some_probe.py -f tests=false
gh run download --name windows-check     # shots/board.png and the log
```

The runner has no radio and no sound card, so OmniRig and real audio still
need a Windows PC.

**The builds are unsigned.** Windows shows a SmartScreen warning and macOS
needs right-click → Open the first time. Signing needs a certificate and an
Apple developer account.
