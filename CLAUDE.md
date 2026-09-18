# hamdash — working notes for Claude Code

A card-based ham radio dashboard. Python + Flask host in a native webview
window (pywebview), cards as independent processes. Target platforms:
Windows 11 (primary — this is where every bug so far has been), macOS, Linux.

Read `README.md` for what the thing is and how to run it. This file is the
stuff that isn't obvious from the code and that has already cost a day.

## Run and verify

```bash
python run.py                      # native window
python run.py --browser            # http://127.0.0.1:8765, no webview
python run.py --home ./dev         # throwaway config dir
python -m unittest discover -s tests
```

Diagnostics, all cheap to poll while reproducing something:

- `<config dir>/hamdash.log` — versions, every card's stdout, unhandled
  exceptions including in threads. On Windows: `%APPDATA%\hamdash\hamdash.log`
- `GET /api/health` — card count, running count, wedged cards, radio state
- `GET /api/bus/stats` — open event streams vs the limit
- `GET /api/shell` — whether the page is in the native shell or a browser

The rig defaults to **mock mode**, so everything is testable with no hardware.
Real radio needs Hamlib's `rigctld` running and `radio.mock: false` in config.

## Layout

```
run.py              launcher, logging, waitress, pywebview window
hamdash/app.py      Flask: static shell, REST API, SSE bus, card proxy
hamdash/bus.py      pub/sub + bounded SSE streams
hamdash/proc.py     one OS process per python card, health, restart, reap
hamdash/registry.py reads card.json from plugins/ and <home>/cards/
hamdash/windows.py  native pop-out windows (pywebview js_api bridge)
hamdash/config.py   config.json, layout.json, tile settings, search paths
hamdash/services/   radio (rigctld), callsign, logbook, grid math
hamdash/sdk/        what a card author imports
hamdash/static/     board, drag/resize/pop-out, themes, settings forms
plugins/            rig_control, logbook, beacon_map, web_embed, hello_card
tests/              regression guards — js_api shape, bus namespace, release notes
tools/              release_notes.py — what the release workflow publishes
.github/workflows/  ci.yml (matrix, reusable), release.yml (on tag)
```

## Invariants — break these and it breaks in ways that are hard to trace

**1. `PopoutApi` in `hamdash/windows.py` is methods-only. No attributes.**
pywebview builds the JS proxy by introspecting the object, and the Windows
winforms backend follows public attributes. Storing a pywebview `Window` on it
sends the walk into .NET: `Window.native` → `Form.AccessibilityObject.Bounds`
→ `Rectangle.Empty` → `Rectangle.Empty` → forever. Symptom: "maximum recursion
depth exceeded", megabytes of log spam, frozen window. All handles live in
module-level privates. `tests/test_js_api_shape.py` enforces this.

**2. Never fall back to `window.open` or Picture-in-Picture inside the native
shell.** In a webview those aren't degraded, they escape to the OS: Windows
pops "Get an app to open this 'about' link" with a Store button. The backend is
chosen from `/api/shell`, never guessed from the browser, and in a native shell
it's the pywebview bridge or an honest error. Same reason `hamdash.js`
intercepts `target="_blank"` clicks and `window.open` and routes them through
`HD.openExternal()` → `open_external()` in `windows.py`.

**3. `window.pywebview` is injected asynchronously.** Checking for it at page
load is a race that's lost about half the time on Windows. Wait for the
`pywebviewready` event (`awaitBridge()` in `popout.js`), and never block board
boot on it — detection runs after the board paints, or you get an empty window
for several seconds.

**4. Every SSE stream holds one server thread for its life.** Waitress is a
blocking server. The board, each card page, and each card process each keep a
stream. So: streams are capped at 55s and clients reconnect (that's the only
way a thread held by a vanished client comes back), the pool is 64, the bus
refuses past 24 concurrent streams with a 503, and `channel_timeout` sits just
above the stream lifetime. Don't add blocking waits in request handlers —
that's what turned this into a hang. The card proxy waits 2.5s for a starting
card, then returns a self-refreshing "Starting…" page rather than sleeping in
a thread.

**5. Tile identity is threaded through the proxy.** One card process serves
every tile of its kind, so per-instance state hangs off the tile uid, which
arrives as `?uid=` and `X-Hamdash-Tile`. Server side: `card.tile_uid()` and
`card.settings(uid)`. Client side: `HD.tileUid`, `HD.settings()`,
`HD.onSettings()`. Tile settings live on the tile in `layout.json`, not in
`config.json`, so they travel with the tile.

**6. Card subprocesses are spawned as `[sys.executable, entry]`.** That breaks
under PyInstaller, where `sys.executable` is the app binary, not an
interpreter. If packaging comes up, the app has to become its own card runner
(a `--run-card` re-exec) — see the "Not done yet" section of the README.

## Conventions

- Cards are processes, not imports, on purpose: a card that crashes, hangs or
  has conflicting dependencies takes only itself down. Keep it that way.
- Shared services are opt-in. A card that wants to ignore the bus and the
  logbook and just draw a waterfall must stay viable.
- `permissions` in `card.json` gates `/api/radio/*`, `/api/log*`,
  `/api/lookup/*`. Enforced properly for a card's *server* calls (token);
  matched on `Referer` for a card's *page* calls, which is a speed bump, not a
  sandbox. Don't describe it as security.
- **Bus namespace.** The host owns `card. config. log. lookup. radio. rig.
  ui.` (`bus.RESERVED`); a card owns `<card_id>.*` and nothing else. A card
  publishing outside it gets a 403 naming the fix. `publishes`/`subscribes` in
  `card.json` narrow further — undeclared means "the whole of my namespace" /
  "everything", so adding a declaration is opt-in and never silently breaks an
  existing card. Two things are delivered regardless of `subscribes`:
  `bus.ALWAYS` (`card.settings_changed`, which the SDK subscribes to on the
  card's behalf) and the card's own namespace (two tiles of one card are
  separate pages sharing a process). `source` on `/api/publish` is no longer
  taken from the body for an identified caller — a card used to be able to
  label its events `"host"`.
- The topic is `radio.state`, not `rig.state`. The docs said `rig.` for a
  while; `rig.` is reserved so nothing can squat the name they point at, but
  nothing publishes it.
- `wedged` ≠ `crashed`. A card can bind its port and hang; `/‌_health` polling
  catches that. Wedged cards are deliberately not auto-restarted — restarting
  something hung while holding a serial handle makes it worse.
- Log records are capped at 2000 chars, stdout/stderr are UTF-8 with
  `errors="replace"` (Windows consoles are cp1252 and a stray byte produces
  `--- Logging error ---` instead of a message).
- Errors in the UI name what to fix, not just what failed. The SDK unwraps the
  host's JSON `error` rather than raising a bare `403 Forbidden`.

## Releasing

Tagging is the trigger; the tree is the source of truth.

1. Bump `__version__` in `hamdash/__init__.py`.
2. Move the `## [Unreleased]` entries in `CHANGELOG.md` under `## [X.Y.Z]`.
3. Commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`.

`release.yml` runs the full CI matrix, then publishes a GitHub Release whose
notes are that CHANGELOG section, extracted by `tools/release_notes.py`. It
refuses to publish if the tag disagrees with `__version__` or the section is
missing or empty — a tag can't be edited after the fact, so those fail the job
rather than shipping wrong notes. Write the changelog entry when the fix
lands, not at release time; the 0.3.3 entry had to be reconstructed from the
README and is guesswork as a result.

## Open threads

- This machine logs `pywebview 6.2.1` on win32. The gui backend only resolves
  once a window opens, so `--browser` won't tell you; run `python run.py` and
  read the first lines of the log to decide whether always-on-top is available.
- Manifest `warnings` (a bad `publishes` entry) are computed and returned by
  `/api/cards` but nothing in the UI renders them yet.
- Card *pages* are still identified by `Referer`, which the page controls. The
  namespace policy is enforced properly only for a card's server calls. Same
  caveat as `permissions`; real isolation needs card pages on their own origin.
- Popped-out tiles still hold their grid slot. Optional "free the space".
- Rig depth: VFO A/B, split, PTT timeout, sharing the rig with WSJT-X.
- Multiple boards (tabs) — `layout.json` becomes a list of boards.
- Packaging: PyInstaller + GitHub Actions matrix, code signing, bundling
  Hamlib, and whether third-party cards work in a frozen build.

## House style

Terse commit messages and terse explanations. Lead with the answer. Flag
failure modes explicitly rather than discovering them in production. Prefer
fixing the cause over adding a retry.
