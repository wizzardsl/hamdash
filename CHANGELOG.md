# Changelog

Notable changes per release. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/). The version of record is
`hamdash/__init__.py:__version__`, which is logged at startup.

## [Unreleased]

### Added
- **A decoder card, with CW working.** Pick a mode and an audio input and it
  reads the radio: a scrolling window of text, the speed and tone it is
  hearing, a lamp that is lit while there is a signal, and Clear. The input
  can be **the radio's own**, so it follows you between rigs without the card
  having its own idea of which sound card to use. The mode dropdown lists
  RTTY, PSK31 and FT8 as well, disabled and labelled as not built: a dropdown
  with one entry does not look like a choice, and leaving them out would make
  the card look finished.
  The decoding is `plugins/decoder/cw.py`, which takes samples and returns
  text and knows nothing about sound cards - so `tests/test_cw.py` can drive
  it with synthesised Morse at known speeds, tones and noise levels. It
  finds the tone itself rather than being told, follows the operator's speed
  from 6 to 45 WPM, and at the edge degrades the way a human does. Below
  about 12 dB it prints nothing rather than filling the screen with letters
  taken out of the noise. Off the air it copied a real CQ first time.
- **Audio in and out on each radio.** Every rig entry can be given the sound
  devices it is wired to, picked from what the PC actually has — on a modern
  radio that is its own USB interface. The fields sit outside the
  backend-specific ones, because which sound card a radio is wired to has
  nothing to do with how its CAT is reached, so switching an entry between
  OmniRig and Hamlib keeps them. Devices are stored **by name**: PortAudio
  indices shuffle whenever anything is plugged in, and an index would quietly
  become a different device. A device that is not present stays selected and
  says `(not connected)` rather than silently becoming whatever is first in
  the list. The pair rides along on `radio.state`, so a digital-mode card can
  ask one question — what is the radio doing — and get the audio with it.
  Nothing opens a stream yet; this is the groundwork for decoder cards.
- **A frequency on the logbook entry pane, and it follows the radio.** With a
  rig under CAT control the frequency, band and mode track the dial live, so
  the pane is always ready to log the contact that is happening now — a chip
  beside **Log it** names the radio it is following. Typing in one of those
  boxes takes it over for that contact, the chip becomes **Follow the radio**
  to hand it back, and logging the contact hands all three back by itself.
  Emptying a box does the same.
  The band is a dropdown of every band rather than a text box, so an override
  is a choice from the list and not a typo: pick one and it stays — shown
  with an accent border — until the frequency moves out of it, at which point
  the dial wins again, because a band that disagrees with the frequency is a
  logged mistake. A frequency on no band clears the guess rather than leaving
  the last one standing.
- **Up and down tuning on the rig card**, with a step picker beside the dial:
  1 Hz through 1 MHz, including the 9 kHz and 12.5 kHz channel steps. The
  arrow keys do the same unless you are typing in the frequency box. The step
  is kept per tile, so two rig tiles can step differently. One press adds or
  subtracts the step rather than snapping to a grid, so up and then down puts
  the dial back exactly where it was.
- **Several radios, three ways of reaching one.** Settings → Radio is a list
  of up to ten rigs rather than one set of fields, each with a backend:
  **OmniRig** (Windows), **Hamlib** (hamdash starts `rigctld` for a model and
  a port you pick), **rigctld** already running, or a simulated rig. Test any
  entry without taking the radio you are using away from you, and switch
  which one is live with a press. What the form shows follows the backend,
  because the fields genuinely differ — an OmniRig entry is a slot number and
  nothing else, since OmniRig owns the port and the rig type, and offering
  them twice would be two places to disagree.
  The Hamlib model list comes from `rigctl -l`, so it is whatever your Hamlib
  supports rather than a copy here that would go stale; without Hamlib
  installed the card says so and takes a model number instead.
  An existing `radio.rigctld_host`/`mock` config becomes entries rather than
  being dropped.
- **A PSK Reporter card.** Who is hearing whom, on the same map machinery as
  the path card — a world that repeats, the azimuthal projection, greyline,
  aurora, absorption and ionosondes — with the filters their own map has:
  band, mode, signal, country, which side of the report, and how recently.
  Watch a callsign, or a grid square for everything around you.

  **Watch a Grid or a Call** — an explicit choice rather than one box guessing
  from what you typed. Clicking the map picks a grid square. The box commits
  itself on Enter or when you click away, so there is no Watch button, and a
  half-typed callsign is never sent. **Heard me / I heard** only exist in Call
  mode: watching a square, both ends of a report can be strangers, so the
  choice is gone rather than greyed — and the filter resets with it, because
  one that is hidden but still narrowing makes the map look broken with
  nothing on screen to say why. **Reset filters** is named for what it does:
  every filter back to everything, the place being watched left alone.

  **A legend** for the dot colours, with live counts. The colours are signal
  strength — not band, which is what most people guess — so the legend says
  so. Your own station is now marked by its ring rather than by its fill:
  colour means one thing on this map.

  **Every filter works on what is already fetched.** PSK Reporter asks for no
  more than one retrieval every five minutes and says plainly that heavy users
  may be rate limited or blocked; a filter bar is a machine for making requests
  if you let it be. So the card process holds the cache and enforces the floor
  — the Refresh button cannot jump it either — and changing band or mode or
  time costs nothing at all. The footer says how old the data is and when the
  next request is allowed. An outage keeps showing what it had rather than
  going blank.

  **Reports accumulate in SQLite, pulled incrementally.** Every reply carries
  the last sequence number in their database; passing it back as `lastseqno`
  asks only for what has arrived since — the "adaptive use of the retrieval
  mechanism" their developer notes ask for by name. Three things follow: each
  pull after the first is small; the window grows past what one reply can
  hold, where a busy square used to truncate at two thousand reports and the
  card had to admit it; and **the time filter stops costing requests
  entirely**, because the retained window is a query against a local file
  rather than a different upstream question. Correctness does not depend on
  `lastseqno` — rows are keyed on what makes a report unique, so an
  overlapping pull updates rather than duplicates.

  **The store outlives the process.** A restart must not spend a query —
  doing it three times inside five minutes would be three of them, and
  closing a window is not what anybody means by the rule. The reports, the
  clock and the sequence marker are all on disk, so the first pull after a
  restart is incremental too.

  **A failed fetch waits its turn too.** Found by causing it: several queries
  in quick succession while developing earned a 429, and with nothing cached
  there was nothing holding the clock — the page's own one-minute poll would
  have retried into the limit for as long as the tile was open. Against a rate
  limit, retrying is the thing that caused it. A failure is now remembered
  like an answer, their `Retry-After` is honoured, and the card says so in
  words rather than showing an HTTP error.
- **A card can keep things between runs.** `card.store` is a directory beside
  the host's own files, with `load_json`/`save_json` that never raise — losing
  a cache is not worth failing a request over. Cards are told where the host
  lives through `HAMDASH_HOME`.
- **`--home` with a relative path put a card's files in the wrong place.** A
  card process has its own working directory, so `--home ./dev` resolved
  against the plugin folder rather than the host's. The config home is
  absolute now.
- **Boundaries on the offline base map.** Country borders, and states and
  provinces for the nine countries Natural Earth publishes them for —
  Australia, Brazil, Canada, China, India, Indonesia, Russia, South Africa
  and the United States. Both are settings on either map card; borders are on
  by default, subdivisions off. Bundled rather than fetched, like the
  coastline, so they work offline and frozen: simplified and rounded the same
  way, which is 51 kB for both.
- **The map machinery is now shared** (`hamdash/static/js/hdmap.js`), so both
  map cards get the same world copies, projections, basemaps and overlays from
  one place. Leaflet moved to `hamdash/static/vendor/`.
- **World Radio League as a third sync service**, beside LoTW and QRZ: pull,
  push, per-service auto, and one API key. Their API is the one here that does
  not speak ADIF over the wire — it keeps a single instant where ADIF keeps a
  date and a time — so a pull is translated back into ADIF and goes through
  the same importer, which means it dedupes exactly like the others. It takes
  one contact per request and allows sixty a minute, so a push paces itself
  and a rate limit stops it cleanly rather than half way. `DELETE` exists in
  their API and is deliberately not implemented, the same as QRZ.
- **A `blue-hour` theme** — deep navy with cyan highlights, in the manner of
  World Radio League. Links now take the theme's accent instead of the
  browser's default blue, which was hard to read on any dark panel and all but
  invisible on a blue one; `daylight`'s amber is deepened so that it, too,
  clears 4.5:1 as a link colour.
- **Settings is in tabs** — Station, Display, Integrations, Radio — instead of
  one form that grew with every feature. Theme moved in beside distance units
  under Display; the quick picker in the top bar still works and the two stay
  in step.
- The **log table pages**, newest contact first, with a size picker (10 to 250,
  or all) that defaults to 30 and is remembered per tile. Paging and ordering
  both happen in the host, so a page is a real page rather than the top of an
  unsorted slice. `GET /api/log/page` returns the rows plus the total.
- **Test lookup** in Settings → Integrations: one deliberate attempt at the
  configured provider, which also clears a remembered refusal.
- **Clear remembered lookups**, beside it, saying how many there are. An
  answer is kept for a month, which is a long time to be shown a grid the
  station has since corrected with no way to say "ask again". It forgets a
  rejected login too. `GET`/`DELETE /api/lookup/cache`.
- Under **Automatic**, a field saying which service the username and password
  belong to. It used to guess HamQTH, which posted a QRZ password to a company
  that never asked for it and spent a failed login doing it.
- **Clicking a logged contact fills the callsign box and opens the station
  panel**, the same one typing the callsign gives you - so a click shows what
  is known about the station rather than a single summary line, and leaves you
  ready to work them again. It reuses the lookup the click already made, so it
  costs no extra request.
- A **station panel** above the log table, filled as you type a callsign:
  name, country and state with a flag, distance, bearing, how many times you
  have worked them, and the notes from those contacts.
- The log table now groups by date, sorts by when the contact happened, and
  shows signal reports, distance and confirmation status. **Columns** picks
  which to show (kept per tile) and a search box filters the whole log.
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

### Changed
- **One band table**, `hamdash/services/bands.py`, where there were three.
  `radio.py`, `logbook.py` and the PSK card each carried their own and they
  had drifted: 5.3 MHz was 60m to the logbook and out of band to the rig, 17m
  had two different pairs of edges, and only one of them had heard of 2200m,
  630m or 4m. A band is written into the log and drawn on the map, so two
  answers to the same question is two answers in front of the operator. The
  browser reads the table from `GET /api/bands` rather than keeping a fourth
  copy in JavaScript.

### Fixed
- **A card's own calls did not say which tile was asking**, so every one of
  them got the manifest defaults. `HD.card()` left the tile uid off the
  request and the proxy only forwards what it is given, so a per-tile setting
  saved correctly, reloaded correctly, and then did nothing - the card never
  saw it. The spectrum card's audio input had the same fault.
- **The settings sheet's Save discarded radio edits.** Radios are saved
  through `/api/rigs` rather than `/api/config`, and Save only sent the
  latter — so editing a COM port, a baud rate or a radio's name and pressing
  Save threw the edit away unless the operator happened to press Test or Use
  this one first, which saved as a side effect. Save now sends both.
- **`config.changed` published every secret to every card.** It carried
  `cfg.data` raw, and that topic is in `bus.ALWAYS` — a card cannot opt out of
  receiving it — so the operator's QRZ password, LoTW password and WRL key
  reached every iframe on the board. It is redacted the same way `/api/config`
  is now, and a test asserts the bus never carries one.
- **Config defaults were shared between `Config` objects.** The merge copied
  the top level only, so a default that is a list — `radio.rigs` — came back
  as the same object every time: appending a radio to one Config added it to
  the others and to `DEFAULTS` itself, where it outlived them both. One
  Config in the app hides it; two in a test or a card process do not.
- **A card that was slow to start never recovered.** `_wait_ready` gave up
  after twelve seconds, said "no response" and stopped looking; the card would
  bind its port a moment later and serve perfectly while the board went on
  reporting it dead until the app was restarted. A cold boot — a fresh
  python.exe being read off disk and scanned, several cards importing Flask at
  once — is easily slower than that, which is why it came right after a few
  restarts. It now keeps watching while the process is alive, and the tile
  fills itself in when the card is up.
- **The map drew a contact's dot but not the line to it**, for every contact
  over land — only the long path across the Pacific showed, because ocean is
  the part with nothing drawn on top of it. The built-in basemap shared the
  overlay pane, where Leaflet stacks a canvas below an SVG, so the lines were
  painted underneath the continents. The basemap now sits in its own pane
  under everything, where a tile basemap has always gone. The aurora and
  absorption layers were losing the same argument wherever they crossed land.
- **The flat map now repeats horizontally, the way a tile map does.** A
  contact reached across the Pacific is drawn in the world copy next door — a
  Japanese station lands at longitude -221, which is where Japan is one world
  west — but the built-in basemap was drawn once, from -180 to 180, so the
  contact sat in blank background in the middle of the ocean. Land, greyline,
  aurora, absorption, ionosondes, the station pair and every logged contact
  are now drawn in three copies, panning is bounded to where the land is, and
  the view no longer jumps back to the middle copy when you pan towards a
  contact. Centre the map on whichever continent you like and your contacts
  are on it.
- **A rejected callsign lookup login is no longer retried on every lookup.**
  Typing a callsign fires a lookup every few characters, and each one re-tried
  the same bad password: QRZ and HamQTH both lock an account after a handful of
  those, so one typo in the password locked you out for hours. A refusal is now
  remembered — until the provider's own `blocked until` time if it gives one,
  otherwise 15 minutes — and saving new credentials or pressing Test tries
  again. Logins are also serialised, so several cards missing the cache at once
  open one session between them instead of one each.
- HamQTH and QRZ no longer share one session id, which logged you out of
  whichever you used second.
- **A provider failing was cached against the callsign, for a month.** Whatever
  you happened to be typing during an outage kept reporting that outage long
  after it ended, so one callsign looked broken while others were fine. A
  refusal is no longer stored at all. "No such record" still is - it is a real
  answer - but for an hour rather than thirty days, so a new licensee appears.
  Rows written by the old behaviour are dropped the first time the cache opens.
- **A lookup no longer fires for every prefix of what you type.** Typing
  VE7STV asked the provider about VE7, VE7S, VE7ST and VE7STV, plus every typo
  on the way; the services rate-limit and then lock an account for exactly
  that. The field now waits for something shaped like a whole callsign, gives
  you 700 ms to finish it, and looks up at once when you tab away.
- **Pressing Clear on a password and then typing a new one wiped it instead.**
  Nothing reset the pending clear, so the value you typed was discarded and
  the save stored nothing - which then read as "needs a username and
  password". Typing now cancels the clear.
- **A pasted credential kept the whitespace the clipboard put around it.** A
  key copied from a web page carries a trailing newline or a zero-width
  character, nothing on screen shows it, and the service answers only
  "incorrect" - so you go looking at the password rather than at the paste.
  Usernames, passwords and API keys are trimmed on paste and on leaving the
  field, where it can be seen, and again on the server.
- **Saved passwords and API keys were destroyed by unrelated settings saves.**
  `/api/config` replaced the callsign lookup password with the literal `"***"`
  so the form had something to display; the form loaded it into the field and
  the next Save stored `***` as the password. Secrets are no longer sent to
  the browser at all — the form is told only that one exists — and a save only
  changes a secret when a new one is actually typed. The same rule now covers
  a card's own secret settings, such as the map's weather key.
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
