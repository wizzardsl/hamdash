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

pyinstaller hamdash.spec           # -> dist/hamdash/  (needs pip install pyinstaller)
python tools/smoke_frozen.py       # starts that build and makes it run a card
```

The smoke test is the only thing that exercises the frozen path: from source
`sys.executable` is an interpreter and the re-exec never happens, so a broken
spec looks perfectly healthy in development.

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
run.py              launcher, logging, waitress, pywebview window, --run-card
hamdash/app.py      Flask: static shell, REST API, SSE bus, card proxy
hamdash/bus.py      pub/sub + bounded SSE streams
hamdash/proc.py     one OS process per python card, health, restart, reap
hamdash/registry.py reads card.json from plugins/ and <home>/cards/
hamdash/windows.py  native pop-out windows (pywebview js_api bridge)
hamdash/config.py   config.json, layout.json, tile settings, search paths
hamdash/services/   radio (rigctld), callsign, logbook, grid math
hamdash/sdk/        what a card author imports
hamdash/static/     board, drag/resize/pop-out, themes, settings forms
plugins/            rig_control, logbook, beacon_map, spectrum, web_embed,
                    hello_card
tests/              regression guards — js_api shape, bus namespace,
                    packaging/spec drift, release notes
tools/              release_notes.py (release notes), smoke_frozen.py (build check)
hamdash.spec        PyInstaller build definition — onedir, see invariant 7
.github/workflows/  ci.yml (tests), build.yml (frozen apps), release.yml (on tag)
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

**3a. One event stream per browser page, shared by every card on it.**
A card page is an iframe on the same origin as the board, and browsers allow
only about six concurrent connections per origin over HTTP/1.1. Each card
opening its own EventSource spent one for the life of the tile: at seven tiles
the budget was gone and the *next request the board made* - any request -
queued for ever. It presents as a card that will not load, or a theme that
will not apply, never as a connection limit. So the top page owns the stream
and relays events to its frames by postMessage; `hamdash.js` detects a
same-origin parent and does not open a socket. Because events then arrive
through the parent, a card page's declared `subscribes` is filtered by the
board (`window.__hamdashRelayFilter` in `app.js`), not by the server.

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

**6. Cards are spawned through `proc.card_command()`, never `sys.executable`
directly.** From source that is the interpreter; frozen, `sys.executable` is
the hamdash binary, so the app is its own card runner and re-execs itself as
`hamdash --run-card card.py`. `run.py:main()` handles that flag *before*
argparse and before any host setup — a card process must not build a Config,
take the log file or start a server. The two halves live in different files
and `tests/test_packaging.py` checks they still agree on the flag.

**7. The build is onedir, not onefile.** Every card is a re-exec of the
binary, and onefile unpacks its whole bundle on every launch: five cards would
mean five unpacks of the same tens of megabytes before the board painted.

**8. `console=False` means `sys.stdout` is None.** In a windowed PyInstaller
build every `print()` raises `AttributeError` on NoneType — including the one
in `proc._drain` that relays a card's output, which would kill the drain
thread and lose exactly the diagnostics the log exists for.
`run.ensure_streams()` runs first and points them at devnull. A real stream (a
pipe from the supervisor) is left alone.

## Secrets

- `config.SECRETS` lists them, and `/api/config` never sends one. It used to
  substitute the literal `"***"` so the form had something to show - the form
  loaded that into the field and the next Save wrote `***` back as the
  password. **Anything a form is shown, it will send back.**
- Absent or empty means "not editing this" and keeps what is stored; an
  explicit `null` clears it. That is the only way to remove one, so it cannot
  happen by accident.
- **Clear is not a mode.** Pressing it marks the field for clearing; typing
  or pasting afterwards has to cancel that, or the new value is discarded and
  the secret is wiped by a save the operator thought was setting it.
- **Credentials are trimmed, on paste and on the server.** A key copied from a
  web page carries a trailing newline or a zero-width character; nothing on
  screen shows it and the service answers only "incorrect", which sends you
  looking at the password rather than at the paste. `config.tidy_credential`
  and `CREDENTIALS`.
- Tile settings of type `secret` follow the same rule, with one difference:
  the card that owns the tile does get the real value, because it has to use
  it (`path_map` builds a tile URL from its weather key). Everyone else gets
  `secrets_set` and an empty string.

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

## The board UI

- Distance is formatted in exactly one place, `HD.fmtDistance`, from
  `config.units`. Cards must not print `km` themselves. `hamdash:units` fires
  when it changes, for anything holding a rendered string.

- **Cards are iframes: their own document.** Setting theme variables on the
  board's `documentElement` never reached them. `hamdash.js` themes whatever
  page loads it and re-themes on `config.changed`, which is in `bus.ALWAYS`
  for that reason.
- Applying a theme is async, so anything that *copies* the variables rather
  than reading them live - canvas, or Leaflet writing colours into SVG
  attributes - must listen for the `hamdash:theme` event. Reading them at boot
  gets the stylesheet's defaults.
- **`min-height` on a scroll container beats its `height`.** Sizing `.grid`
  that way made the board grow past the viewport, and since `body` is
  `overflow:hidden` everything below the fold was clipped away unreachable. A
  spacer child carries the extent instead.
- Arrange mode is gone. Tiles always dragged by their header and resized from
  the corner, and the grid guides already appear while dragging, so it was a
  mode you entered to do what already worked.

- The **Cards menu** (`renderCardsMenu` in `app.js`) is rendered from
  `board.tiles` every time it opens and on every `saveLayout`, so it has no
  state of its own to drift — closing a tile with ✕ unticks it for free.
- Ticking adds one tile; unticking removes *all* tiles of that card, because a
  tick is per card and a card can have many tiles. That discards per-tile
  settings, so the toast offers Undo: `board.add()` honours a `uid` you pass
  it, which is what makes restoring them exact.
- **`.toast` is `pointer-events: none`** so a status message never swallows a
  click on the board. A toast with a button has to add `.has-action` or the
  button is unclickable and silently does nothing.
- **No `confirm()`/`alert()`.** Nothing in the app used them before the menu
  briefly did; they are browser chrome inside a webview, and the app already
  has toasts and `<dialog class="sheet">`.
- Buttons built from nested spans need an explicit `aria-label`. The menu's
  six checkboxes computed an empty accessible name until they got one.

## Audio cards

`plugins/spectrum` is the first card that owns a hardware resource, and the
shape it settled on is worth reusing:

- **Poll, don't stream.** A card's own SSE works through the proxy, but it
  holds a host thread for its life (invariant 4). The card rings recent frames
  and serves everything since a sequence number; the page polls. Send bytes,
  and only the bins the tile shows.
- **Nothing tells a card that a tile was deleted.** So a card holding a device
  must reap it: every request marks its device wanted, and a thread closes
  anything unwanted for 30s. Without that, changing the input in tile settings
  leaks the old one and the radio's audio stays locked against other software.
- **Don't respin a failed resource on every request.** Doing so means the
  caller only ever sees a brand new object in its "starting" state and never
  the error saying what is wrong. Retry on a timer instead.
- **PortAudio is not thread-safe across enumerate/open.** Both go through one
  lock, or Windows blocks for a long time or forever.
- **Mock by default**, like the rig. A card that needs hardware to show
  anything can't be developed or demonstrated.

## Logbook sync

- **Auto sync is exclusive.** Only one service may be automatic at a time;
  ticking one unticks the other. Both running would upload the same contacts
  twice and then re-upload each other's confirmations on the next round.

- **LoTW push has no API.** ARRL takes signed `.tq8` only, and the certificate
  is inside TQSL. `Lotw.push` shells out to TQSL or refuses with what to do.
  Don't "simplify" this into an HTTP POST; there isn't one.
- LoTW answers a bad password with an HTML page and a 200, so the pull checks
  the body looks like ADIF rather than trusting the status code.
- **Dedupe is the whole game on pull.** `Logbook.find_match` is callsign +
  date + band + mode + start time within `DUPE_MINUTES` (30, which is what
  LoTW itself uses). `import_adif` merges onto a match instead of inserting,
  and returns `{added, updated, skipped}` — "nothing was added" and "nothing
  happened" are different answers.
- A sync runs in `qsl.Runner`, one at a time, off the request thread, and
  reports on `log.sync`. Doing it inline would hold a server thread for a
  minute (invariant 4).
- `sqlite3.connect` as a context manager commits but does **not** close. Use
  `Logbook._conn`, which does both; the old code leaked a connection per read.
- **QRZ HTML-escapes the ADIF payload in its reply.** Splitting the response
  on `&` truncates it at the first `&lt;` and leaves `ADIF` present and empty,
  which reads as "no ADIF" and sends you looking in the wrong place. Take
  everything after the first `ADIF=` whole and `html.unescape` it; parse only
  the small fields in front. Do not `unquote_plus` the payload - a `+` in a
  comment is a plus.
- **QRZ's logbook API has three traps**, all of which were hit first time:
  a real `User-Agent` is mandatory (QRZ names `python-requests` as the kind it
  rate limits), `STATUS` answers in a nested `DATA` list rather than top-level
  fields, and `FETCH` must be paged with `MAX:n,AFTERLOGID:n` walked by
  `app_qrzlog_logid` or a big book times out. Spec:
  qrz.com/docs/logbook/QRZLogbookAPI.html
- **QRZ `DELETE` is never sent.** It is permanent, and nothing in hamdash
  should be able to remove somebody's QSOs. A test asserts the action never
  appears.
- **Push is dry-run by default, server side as well as in the UI** — a caller
  that omits `dry_run` counts rather than uploads. `log.since(date)` scopes
  it; a junk date selects everything, so the UI must not pass its placeholder.
- Untested against the live services — there is no LoTW or QRZ account here.
  The HTTP shapes come from their docs and the tests mock them.

## One station, several cards

- `lookup.result` is the "what are we looking at" event. Cards draw from the
  payload; they must not call a lookup *back* in response to one.
  `beacon_map` did exactly that and it was a feedback loop: one click produced
  200+ events, and because the echo carried no `?grid=` it replaced a logged
  QSO's real grid with the provider's guess for the callsign.
- `?grid=` on `/api/lookup/<call>` overrides the locator for that answer only
  and never touches the cache. A logged QSO's grid is what was actually
  worked.
- A lookup can succeed with an empty `locator` (callook does this for some US
  calls). `auto` falls through to the next provider in that case and only
  settles for the grid-less answer if nothing better turns up. Anything that
  plots must handle "answered, no grid" without leaving the previous station
  on screen.
- **`setPointerCapture` on `pointerdown` makes every child unclickable**: the
  following `click` goes to the captured element, not to what is under the
  cursor. Capture once a drag has actually started (a few pixels of movement),
  and suppress the click that ends a drag.
- Pan and zoom change the viewBox only. Rebuilding 56 coastline paths per
  wheel notch is the difference between a map and a slideshow.
- **Longitudes are unwrapped, never split at the antimeridian.** Splitting a
  great circle into two polylines leaves one stopping dead at +-180 with no
  station on the end - it reads as a dot that failed to draw, and the real end
  is 360 degrees away off the other side. Leaflet accepts longitudes beyond
  +-180 and draws them in the neighbouring world copy, so `unwrapLon` keeps
  the line continuous and `endpoint()` puts the marker on the end of its own
  line. Anything framing a path must use `endpoint()` too, or fitBounds spans
  the wrong way round the planet.
- **Unwrapping only works if the world repeats, so the flat map draws
  everything three times** (`worldCopies()`, offsets -360/0/+360). A tile
  basemap repeats for ever; ours was one GeoJSON layer from -180 to 180, so a
  contact unwrapped to -221 - which is exactly where Japan is, one world west
  - floated in blank background and the map looked broken at the moment it was
  being most correct. Land, greyline, aurora, D-RAP, ionosondes, the station
  pair and the logged contacts all repeat; `maxBounds` stops panning where the
  land stops, so there is no edge to fall off. Azimuthal returns `[0]`: it is
  a disc centred on you and wrapping means nothing there.
- **`worldCopyJump` is off, deliberately.** It teleports the view back to the
  middle copy, which is precisely the pan you are making to reach a contact
  drawn in the one next door.
- Framing (`fit`, `fitSelected`, `plotLogged`) must use the primary copy only,
  or fitBounds spans three worlds and zooms out to nothing.
- Leaflet's canvas renderer only paints the padded viewport, so a marker on
  that boundary is drawn as half a dot. The log's dots use SVG unless there
  are thousands of them; its *lines* are always canvas - they have no such
  edge to fall foul of, and at 180 points each times three copies they are the
  bulk of the work.
- **Leaflet fixes a map's CRS at construction**, and the layer control is
  built once, so changing projection or supplying a weather key means
  `map.remove()` and build again (`needsRebuild`/`rebuild`), not adjusting in
  place.
- Azimuthal cannot use tiles: no service publishes tiles in aeqd, so the base
  map is forced to the built-in vector one, which reprojects like any other
  GeoJSON.
- Everything under `/api/space/*` is cached for five minutes by the host.
  That is not politeness alone - NOAA resets the connection after a handful of
  rapid requests, which is easy to hit while developing.
- D-RAP arrives as a text table, not JSON. All-zero is the normal, good case
  and is reported as `quiet`, not as an empty failure.
- `path_map` is Leaflet, vendored in the card (`vendor/`, BSD-2, ~190 kB with
  its images) rather than from a CDN, so it works offline and frozen. The
  offline basemap is a Leaflet GeoJSON layer, which is what lets it sit in the
  same picker as tile basemaps.
- **Leaflet writes colours into SVG presentation attributes**, where
  `var(--x)` does not resolve. Read the theme's values with `getComputedStyle`
  instead, or the map ignores the theme.
- Greyline is computed in the browser: solar position is trigonometry, and a
  round trip to ask a server where the sun is would be absurd. Checked against
  both solstices and the equinox.
- Aurora goes through `/api/space/aurora` (permission `space`) and not straight
  from the card: NOAA's grid is ~1.5 MB of mostly zero, they publish every five
  minutes, and a card fetching a third-party URL depends on that server's CORS
  headers, which are not ours.
- `path_map` used to carry its own coastlines instead of fetching tiles: no network,
  no third-party dependency, no tile-usage policy. SVG y is `-lat`, so
  anything computing a viewBox has to work in projected space — framing on
  latitude centres the view on the mirror of the path in the other hemisphere.
  Marker sizes are in degrees and must be scaled by the view width, or a pin
  becomes the size of Spain when the view zooms in.

## Flags and prefixes

- `services/dxcc.py` maps prefix -> (country, ISO alpha-2, rough locator),
  longest prefix first. Everything from it is `approximate`; a real provider
  always wins. `cty.dat` remains the proper answer and an open thread.
- The flag is derived from the **callsign**, never from a provider's country
  name: providers spell countries differently and some return none.
- `iso` is attached when rows are served, not stored on the QSO, so a log
  written last year gets today's table.
- A flag answers "where is this person", so DXCC entities collapse: GM/GW/GI
  all fly `gb`, KL7 and KH6 fly `us`. Places with their own flag keep it.
- Flags are bundled (`static/flags/*.svg`, flag-icons, MIT). Not a CDN: it
  would leak which countries the operator works, and break offline.

## Callsign lookup

- Default provider is `auto`: callook then hamdb, both free and needing no
  account. It reaches an account provider only when `callsign_lookup.account`
  says which one the credentials are for. It used to assume HamQTH, which
  posted a QRZ password to a company that never asked for it and spent a
  failed login doing it.
- `/api/lookup/cache` sits under `/api/lookup/<call>`. Werkzeug prefers the
  static rule, so it routes correctly, but that is a property of the router
  rather than anything the code says — `tests/test_lookup.py` pins it, because
  if it ever reversed, clearing the cache would quietly become a lookup of a
  station called CACHE.
- **A provider failing is not a fact about the callsign, so it is not
  cached.** It used to be: the prefix fallback was stored with its note for
  `cache_days`, so whatever was being typed during an outage went on reporting
  that outage for a month - which reads as "this callsign is broken" and sends
  you looking in the wrong place entirely. A genuine miss is cached, marked
  `miss`, for `MISS_TTL` (an hour) rather than the full term. `Lookup._repair`
  drops rows from before the distinction, once, at construction.
- **The logbook must not look up every prefix of a callsign.** It did, and
  with the typos that is five or six provider calls per contact - which is
  what rate limits and account locks are for. `CALLSIGN` in the card gates on
  the shape of a whole callsign, the debounce is 700 ms, and blur looks up at
  once so the wait is never felt.
- **A refused login is remembered, not retried.** The logbook looks a callsign
  up every few characters you type, so a wrong password used to cost a failed
  login per keystroke - and QRZ and HamQTH both lock an account after a
  handful. `Lookup._refused` holds the refusal until the provider's own
  `blocked until` time, or 15 minutes if it gives none; saving credentials
  rebuilds the `Lookup` and `retry_auth()` (the Test button, or `?refresh=1`)
  forgets it. Sessions are per provider and behind one lock, because several
  waitress threads miss the cache at once and would each open their own.
- **A provider that refuses for a reason the operator can act on raises
  `Refused`, and that text reaches the UI.** Falling back to the prefix table
  quietly would turn "your QRZ subscription lapsed" into a station sitting in
  the middle of its own country.
- `approximate: true` means the prefix table answered — a country centre.
  Anything that writes a grid somewhere must refuse it; the settings sheet
  does.
- `/api/grid/locator` is a POST because the body is the operator's real
  position. A query string is the one place it would get written down.
- Changing `callsign_lookup` rebuilds the `Lookup` in `put_config` rather than
  waiting for a restart: the point of changing provider is usually to look
  something up immediately afterwards.
- LoTW has no public per-callsign page, so `links_for` has no lotw entry and
  should not grow one. Its public user-activity list is a separate feature.

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
- LoTW user-activity list ("is this station on LoTW"), from the public CSV.
- `cty.dat` for real DXCC, replacing the short PREFIX table.
- Rig depth: VFO A/B, split, PTT timeout, sharing the rig with WSJT-X.
- Multiple boards (tabs) — `layout.json` becomes a list of boards.
- Linux builds. `build.yml` is Windows + macOS only: both have a
  pip-installable pywebview backend, Linux needs system GTK/WebKit packages
  and a build without them is silently browser-only. The `tests` matrix still
  covers Linux.
- Code signing. Builds are unsigned, so Windows shows SmartScreen and macOS
  needs right-click → Open. Needs a certificate and an Apple developer
  account; nothing in the repo is blocking it.
- An icon. `hamdash.spec` sets none, so the binary carries PyInstaller's.
- Bundling Hamlib (`rigctld`) rather than asking the operator to install it.

## Keeping this file true

This file is the recovery point for a new session: it is checked in, so a
fresh clone plus `CLAUDE.md` is enough to pick the work back up. Update it in
the same commit as the change it describes — an invariant that isn't written
down here has to be rediscovered by breaking it. The things that belong here
are the ones that cost a day: invariants, non-obvious failure modes, and what
was deliberately *not* done. Routine code changes go in `CHANGELOG.md`.

## House style

Terse commit messages and terse explanations. Lead with the answer. Flag
failure modes explicitly rather than discovering them in production. Prefer
fixing the cause over adding a retry.
