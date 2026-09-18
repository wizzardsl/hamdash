# Changelog

Notable changes per release. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/). The version of record is
`hamdash/__init__.py:__version__`, which is logged at startup.

## [Unreleased]

### Added
- **Import ADIF** in the logbook, for a file from any other logger, and a
  **Pull** as well as a Push for each sync service. All of them go through the
  same importer, so all of them dedupe: importing a file twice reports nothing
  new the second time.
- An **azimuthal projection** for the map, centred on your station, so every
  bearing is a straight line out from the middle.
- Three more map layers: **D-RAP absorption** and **ionosonde MUF/foF2**, both
  free and cached by the host, and **clouds** from OpenWeatherMap for anyone
  with a key.
- A **country flag beside every callsign**, from its prefix, carried through
  the log, the contact details, the map popups and the path card. Flags are
  bundled rather than fetched from a CDN.
- The map's **Logged contacts** layer fetches the log itself, so ticking the
  box is the whole gesture. The logbook's separate "Map all" button is gone.
- A **distance units** setting — kilometres, miles or nautical miles — used by
  every card that shows a distance. They now show one distance rather than km
  beside miles, and changing it applies immediately.
- The logbook shows the **logged frequency**, and every row has an eye button
  opening all of that contact's recorded fields.
- Clicking a logged contact while the whole log is on the map frames that
  path; **Map all** now draws a line home from each contact.
- **Map all** in the logbook plots every logged contact with a grid, as its
  own map layer.
- **Push and auto-sync** in the logbook, shown only for a service that is set
  up - and for LoTW only when TQSL is actually installed, since without it a
  push cannot work. Auto uploads what has been logged since the last upload
  and then pulls, once a minute; the marker is per service so a restart does
  not re-send the log.
- Local time beside UTC in the top bar, with the zone name.
- A **map card** (`path_map`): your station, the one you are looking at, and
  the short-path great circle between them. Leaflet, bundled rather than from
  a CDN, with a choice of base map — the built-in offline one (Natural Earth,
  no network and no account), OpenStreetMap, or your own tile URL and key.
  Scroll to zoom, drag to pan, click either pin for that station's details and
  public links.
- Two map layers: **greyline**, computed in the browser from the sun's
  position with no network at all, and **aurora** from NOAA SWPC's OVATION
  forecast, cached and thinned by the host so every tile does not poll NOAA.
- **Clicking a logged contact** points the map and the path card at it,
  preferring the grid that was logged over a fresh lookup.
- **Test connection** for both sync services (read-only), and uploads that
  default to a dry run — they count what would be sent and send nothing until
  you say so, optionally narrowed to QSOs from a date.
- Logbook sync with **LoTW** (pull with your username and password; push via
  TQSL, which is the only thing that can sign a LoTW upload) and the **QRZ
  logbook** (pull and push with a logbook API key). Syncs run off the request
  thread and report on the `log.sync` bus topic.
- ADIF import now dedupes and merges instead of appending. A QSO is matched on
  callsign, date, band, mode and start time within 30 minutes; a match is
  updated with what the other log knows, so confirmations land on the right
  contact. `import_adif` returns `{added, updated, skipped}`.
- Confirmation fields on a QSO (`qsl_rcvd`, `lotw_qsl_rcvd`, `qslrdate`,
  `dxcc`, `state`, `cnty`, …), added to existing logs by migration.
- Two ways to fill in your grid, in Settings: **Use my location** (browser
  geolocation, converted by the host; coordinates go in a POST body, never a
  URL) and **From my callsign**. The second refuses to write anything when the
  answer is only approximate, because the prefix table answers with the middle
  of a country.
- Callsign lookup is configurable in Settings, and defaults to a new
  `auto` provider that tries the free no-account sources first. Added
  **HamDB** (free, no account, US and Canada) and **QRZ.com** (needs a paid
  XML subscription; its refusal is shown rather than swallowed).
- Lookup results carry `links` to the public pages for that callsign.
- A **Cards menu** in the rail: everything installed, with a tick beside what
  is on the board. Tick to add, untick to remove, and closing a tile with its
  ✕ unticks it there. Unticking a card with several tiles takes them all off
  and offers Undo, which restores them with their per-tile settings.
- `spectrum` card: live spectrum trace and waterfall of the receiver's audio,
  with automatic contrast, per-tile input selection and a synthesised `mock`
  signal so it works with no radio attached. Needs numpy and sounddevice,
  both now bundled in the frozen build.

### Fixed
- Auto sync could be switched on for LoTW and QRZ at once, which would have
  uploaded every new contact to both and then re-uploaded each service's
  confirmations to the other. It is now one at a time.
- The greyline's subsolar point was drawn as a plain dot and read as another
  station. It is a sun.
- Map lines that crossed the antimeridian ended in mid-ocean with no dot: the
  great circle was split into two polylines, so one stopped at ±180 with no
  station there and the real end was on the other side of the map. Longitudes
  are unwrapped instead, so each line runs continuously to its own dot.
- Some dots were drawn as half dots — Leaflet's canvas renderer only paints
  its padded viewport, and a marker on that edge gets clipped. Markers are SVG
  unless a log runs to thousands.
- Hovering a logged contact now names it, as well as clicking it.
- The dots from **Map all** shared the aurora layer's canvas renderer, so they
  were tied to whether an unrelated layer was switched on and drawn on a
  surface meant for a heat map. They have their own renderer now, and match
  the station pins.
- **Adding a seventh card broke the board.** Every card page opened its own
  event stream, and browsers allow about six connections per origin over
  HTTP/1.1, so the whole budget went on streams and the next request queued
  for ever. One stream per page now, relayed to the cards on it.
- Cards kept the dark theme when the board went light: a card is an iframe
  with its own document, which never saw the board's variables.
- The board could not scroll to cards below the fold — `min-height` on the
  scroll container overrode its `height`, so it grew past the viewport and
  `body { overflow: hidden }` clipped the rest away.
- Arrange mode removed; dragging and resizing never needed it.
- `HD.get(path, params)` sent its parameters in the request body, where no
  server reads them. `HD.distance()` had therefore never worked, the Rescan
  folders button never rescanned, and `HD.log.recent(30)` quietly returned 25.
- `beacon_map` answered every `lookup.result` with another lookup, a feedback
  loop that turned one click into 200+ bus events and overwrote a logged QSO's
  grid with the provider's guess.
- A lookup that succeeded with no gridsquare ended the provider chain, so a
  callsign callook knows by name but not by grid never reached hamdb, which
  does know it.
- A QRZ pull returned nothing: QRZ HTML-escapes the ADIF in its reply, so
  splitting the response on `&` cut the payload off at its first `&lt;` and
  left the ADIF field present but empty.
- The QRZ logbook client did not match QRZ's published API: it sent no
  `User-Agent` (which QRZ rate limits), read `STATUS` fields from the top
  level instead of the nested `DATA` list, and fetched the whole book in one
  unpaged request. Now paged with `MAX`/`AFTERLOGID` as QRZ asks.
- A sync result was overwritten by the generic status line a moment after it
  arrived, so the QSO count from Test connection could not be read.
- Every logbook read leaked a SQLite connection: `with sqlite3.connect(...)`
  commits but does not close. On a long-running shack PC that ends as "too
  many open files" a long way from the cause.
- `callsign_lookup` was not in the list of settings `/api/config` would accept,
  so lookup settings could never be saved. Changing it now also rebuilds the
  lookup instead of quietly using the old provider until restart.
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
