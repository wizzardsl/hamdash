# Changelog

Notable changes per release. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/). The version of record is
`hamdash/__init__.py:__version__`, which is logged at startup.

## [Unreleased]

### Added
- A **Cards menu** in the rail: everything installed, with a tick beside what
  is on the board. Tick to add, untick to remove, and closing a tile with its
  ✕ unticks it there. Unticking a card with several tiles takes them all off
  and offers Undo, which restores them with their per-tile settings.
- `spectrum` card: live spectrum trace and waterfall of the receiver's audio,
  with automatic contrast, per-tile input selection and a synthesised `mock`
  signal so it works with no radio attached. Needs numpy and sounddevice,
  both now bundled in the frozen build.

### Fixed
- `card.settings()` fell back to `manifest["settings_defaults"]`, a key no
  card.json has ever had, so a card asking about a tile that isn't on the
  board got `{}` instead of its manifest defaults.
- A `layout.json` that was valid JSON of the wrong shape got as far as an
  unrelated 500 (`'str' object has no attribute 'get'`). It is now rejected
  where it is read, and a hand-written `{"tiles": [...]}` wrapper is accepted.
- The release workflow built the per-platform zips and then published without
  attaching them, so 0.3.4 shipped with no downloads. The publish step now
  verifies at least one asset landed and fails if not, and is idempotent so a
  `workflow_dispatch` re-run repairs an existing release rather than erroring
  because the tag is already there.

## [0.3.4]

### Added
- The app can be packaged as a desktop build. `hamdash.spec` (onedir),
  `--run-card` so the frozen binary is its own card runner, `build.yml` for
  Windows and macOS, and `tools/smoke_frozen.py`, which starts a build and
  makes it run a card — the only check that exercises the frozen path at all.
  Releases now carry a zip per platform.
- Bus namespace policy. The host owns the reserved namespaces
  (`card. config. log. lookup. radio. rig. ui.`); a card may publish only
  `<card_id>.*` and is refused elsewhere with a message naming the fix.
- `publishes` and `subscribes` in `card.json`. Both narrow what a card may do;
  undeclared means unrestricted, so existing cards are unaffected. A
  `publishes` entry outside the card's own namespace is a manifest warning
  rather than an error — the card still runs, it just can't use that topic.
- The SDK warns at startup when an `@card.on` handler falls outside the
  card's declared `subscribes`, which would otherwise be a card that runs
  and silently never reacts.

### Fixed
- Cards could not be started from a frozen build at all: they were spawned as
  `[sys.executable, entry]`, which under PyInstaller means the hamdash binary
  with a stray argument rather than an interpreter.
- A windowed build has no `sys.stdout`, so every `print()` raised on NoneType
  — including the one relaying a card's output, which would have killed the
  drain thread and lost the diagnostics the log exists for.
- A card could publish `radio.state` and make every tile on the board believe
  the rig had moved. It can't now.
- `/api/publish` took `source` from the request body, so a card could label
  its own events `"host"`. An identified caller's source is now its card id.
- `/api/events/recent` ignored the subscription filter, so a card held to a
  `subscribes` list could read the same events back out of history.
- `__version__` said `0.1.0` while the app was 0.3.3; it is now the single
  source of truth and appears in the first line of `hamdash.log`.
- `bus.py` documented the radio topic as `rig.state`; it is and always was
  `radio.state`. `rig.` is now reserved so nothing can squat the name the
  docs pointed at.

## [0.3.3]

### Fixed
- "Maximum recursion depth exceeded" then a frozen window on Windows.
  pywebview builds its JavaScript proxy by introspecting the `js_api` object,
  and the winforms backend follows public attributes. A pywebview `Window`
  stored on the api led that walk into .NET
  (`Window.native` → `Form.AccessibilityObject.Bounds` → `Rectangle.Empty` →
  …forever). Window handles now live in module-level privates and
  `tests/test_js_api_shape.py` fails if anything data-shaped reappears.
- "Get an app to open this 'about' link" on Windows 11. `window.pywebview` is
  injected asynchronously; checking for it at page load was a race, and losing
  it fell through to Picture-in-Picture inside WebView2, which handed the
  new-window request to the Windows shell. The UI waits for `pywebviewready`
  and refuses to fall back when `/api/shell` reports a native shell.
- Event streams no longer starve the thread pool. Streams are capped at 55s
  with client reconnect, the pool is 64, the bus refuses past 24 concurrent
  streams with a 503, and `channel_timeout` sits just above the stream
  lifetime.
- Card processes are reaped when their last tile is removed; they used to
  accumulate for the life of the session.
- Log records are capped at 2000 chars, and stdout/stderr are UTF-8 with
  `errors="replace"` so a stray byte on a cp1252 Windows console produces a
  message rather than `--- Logging error ---`.

## [0.3.2] and earlier

Not recorded. History before the repo begins here.
