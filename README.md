# hamdash

A card-based ham radio dashboard. Runs as a desktop app on Windows, macOS and
Linux: no server to configure, no localhost URL to remember, nothing installed
system-wide. Cards can be a URL in a tile or a full Python program — same board,
same event bus.

## Run it

```bash
pip install -r requirements.txt
python run.py                 # native window (pywebview)
python run.py --browser       # serve on http://127.0.0.1:8765 instead
python run.py --home ./dev    # use a throwaway config directory
```

Your config, log and cards live in one directory:
`%APPDATA%\hamdash` / `~/Library/Application Support/hamdash` / `~/.config/hamdash`
(or `HAMDASH_HOME`). Back it up by copying that folder.

Out of the box the radio is in **mock mode** so everything is usable with no
hardware. To talk to a real rig, start Hamlib's daemon and turn mock off:

```bash
rigctld -m 1035 -r /dev/ttyUSB0 -s 38400    # -m 1 is the Hamlib dummy rig
```

```json
// config.json
{ "station": { "callsign": "VE7ABC", "grid": "DO10" },
  "radio": { "mock": false, "rigctld_host": "127.0.0.1", "rigctld_port": 4532 } }
```

## Shape of the thing

```
run.py ──> hamdash/app.py            Flask: static shell, REST API, SSE bus, card proxy
             ├── bus.py              pub/sub, last-value cache, SSE stream
             ├── registry.py         reads card.json from plugins/ and <home>/cards/
             ├── proc.py             one OS process per python card, restart with backoff
             ├── config.py           config.json, layout.json, search paths
             ├── services/           radio (rigctld), callsign, logbook, grid math
             ├── sdk/                what a card author imports
             └── static/             board, drag/resize, themes, card palette
plugins/     rig_control, logbook, beacon_map, spectrum, web_embed, hello_card
```

Why processes and not imports: a card someone wrote at 1am shouldn't be able to
hang the UI, crash the host, or drag a conflicting dependency into it. The host
reverse-proxies `/cards/<id>/*` to the card's own loopback port, so cards are
same-origin to the browser and their ports are never exposed.

## Writing a card

Copy `plugins/hello_card` into `<HAMDASH_HOME>/cards/` and edit. Three kinds:

| kind | what it is | code needed |
|---|---|---|
| `url` | an address in a tile — cluster, propagation, WebSDR, RSS | none |
| `html` | static files served from the card directory | HTML/JS |
| `python` | its own process, its own HTTP API, full access to the bus | Python |

`card.json`:

```json
{ "id": "my_card", "name": "My card", "kind": "python",
  "entry": "card.py", "view": "index.html",
  "w": 4, "h": 3, "min_w": 2, "min_h": 2,
  "publishes": ["my_card.tuned"],
  "subscribes": ["radio.state"] }
```

`card.py`:

```python
from hamdash.sdk import Card
card = Card(__file__)

@card.get("/state")                      # served at /cards/my_card/api/state
def state():
    return {"rig": card.host.radio()}    # shared services, if you want them

@card.on("log.qso_added")                # bus events, if you want them
def logged(topic, payload):
    card.publish("my_card.saw", {"call": payload["call"]})

card.run()
```

In the card's own HTML, `HD` (`/static/js/hamdash.js`) is the client side of the
same contract:

```js
const s = await HD.radio.state();
HD.on('lookup.result', (info) => console.log(info.callsign, info.locator));
await HD.card('my_card', '/api/state');
```

Shared services are opt-in, not mandatory: `/api/radio/*`, `/api/lookup/<call>`,
`/api/grid/distance`, `/api/log`, `/api/publish`, `/api/events`. A card that
wants to ignore all of it and just draw a waterfall can.

## Moving cards around

Drag a tile by its header, resize it from the bottom-right corner — no mode to
enter first. **Arrange** just turns on the grid guides and keeps the resize
handles visible. Overlaps are resolved by pushing tiles *down*, never sideways:
horizontal position is what people place deliberately, vertical order isn't.

Adding and removing a card: the **Cards** menu lists everything installed with
a tick beside whatever is on the board. Tick to put a card on, untick to take
it off — and closing a tile with its own **✕** unticks it there, so the menu is
always a true picture of the board rather than a separate thing to keep in
sync.

It is a dropdown rather than a dialog, because ticking a few cards on and off
is something you do in a run and a modal makes you reopen it each time.

A card can have several tiles (two `web_embed` tiles are two different pages),
so a row with more than one shows a count and unticking takes all of them off
at once. That would throw away per-tile settings, so instead of asking first
the toast offers **Undo**, which puts them back with their settings intact.

**Browse & drag…** in that menu opens the older sheet, where a row can be
dragged out and dropped exactly where you want it on the board.

## Popping a card out

Hit **⧉** on a tile, or just drag the tile off the board. The card gets its own
window — resizable, and on top of other apps if the platform allows it. The
tile stays on the board as a placeholder with **Bring it back**; the card
process never restarts, because the window frames the same `/cards/<id>/` URL
the tile did.

The backend is chosen from what the host reports at `/api/shell`, not guessed
from the browser. That matters: inside a webview, `window.open` and
Picture-in-Picture don't degrade gracefully, they escape to the OS — on
Windows 11 you get *"Get an app to open this 'about' link"* with a button to
the Store. So in the desktop shell it is the pywebview bridge or an honest
error, never a fallback.

Three backends:

| you're running | window | always on top | more than one at a time |
|---|---|---|---|
| `python run.py` (pywebview) | real OS window | yes, toggleable per window | yes |
| Chromium browser | Document Picture-in-Picture | yes | **no — one only** |
| any other browser | `window.open` popup | no | yes |

Pop-out state is saved in `layout.json`. The desktop app reopens those windows
on next launch; browsers can't (see below), so the tile shows an **Open
window** button instead.

`hamdash/windows.py` is the whole native side: it hands pywebview a `js_api`
object, so the UI calls `pywebview.api.popout_open({...})` and Python creates
the window. Window size and position are remembered per tile for the session.

**If you edit that file, keep the api class methods-only.** pywebview builds
the JavaScript proxy by introspecting the object, and on the Windows winforms
backend that walk follows public attributes. A pywebview `Window` stored on
the api leads it into .NET's object graph — `Window.native` →
`Form.AccessibilityObject.Bounds` → `Rectangle.Empty` → `Rectangle.Empty` →
forever. All window handles live in module-level privates for that reason, and
`tests/test_js_api_shape.py` fails if anything data-shaped reappears on the
class:

```
python -m unittest discover -s tests
```

## Tiles, cards and settings

One card is one process; one card can be many tiles. Two `web_embed` tiles are
two different pages, two `hello_card` tiles two different greetings — served by
the same process, because every request carries the tile it came from:

```
GET /cards/hello_card/api/state?uid=h2      # and X-Hamdash-Tile: h2
```

In the card:

```python
uid  = card.tile_uid()          # which tile is asking
conf = card.settings(uid)       # its settings, over the manifest defaults
```

and in the card's own page, `HD.tileUid`, `HD.settings()`, and
`HD.onSettings(fn)` to repaint when the operator edits them.

Settings live on the tile in `layout.json`, not in `config.json` — they travel
with the tile when you drag it, pop it out or delete it. Declare them in
`card.json` and the ⚙ button renders the form; there is no per-card UI to
write:

```json
"settings": {
  "url":       {"type": "string", "label": "Page address", "default": ""},
  "show_rig":  {"type": "bool",   "label": "Show frequency", "default": true},
  "accent":    {"type": "select", "label": "Emphasis", "options": ["normal", "loud"]}
}
```

Five field types: `string`, `number`, `bool`, `select`, `secret`. `plugins/hello_card`
is the worked example — copy that folder to start your own.

`card.settings()` caches for a few seconds and invalidates on the
`card.settings_changed` bus event, so calling it per request is fine and an
edit shows up immediately.

## Spectrum and waterfall

The `spectrum` card draws what is in the receiver's passband. Feed the radio's
audio into a sound card input and pick it in the tile's settings; the card page
lists the inputs it can see. It defaults to `mock`, a synthesised test signal,
so the card is usable with no radio attached for the same reason the rig is.

Two things about it are worth knowing if you write something similar:

- **It polls; it does not stream.** A waterfall wants about 20 rows a second,
  and an SSE stream through the card proxy holds a *host* server thread for
  its whole life. The card keeps a ring of recent rows and hands over
  everything since the caller's sequence number in one request, so the page
  polls four times a second and still draws every row. Rows are bytes, base64,
  sliced to the bins the tile actually shows — about 12 kB a poll instead of
  82 kB.
- **Contrast is automatic by default.** Sound card levels vary by tens of dB
  between a USB codec, a line input and whatever the mixer was left at. A
  fixed noise floor is wrong on most machines: on this one the mock signal
  sits at −72 dB and a real microphone at −118 dB. The card reports a floor
  and ceiling tracked from recent history and the page uses them unless you
  turn auto contrast off.

It needs `numpy` and `sounddevice`, both in `requirements.txt` and both in the
frozen build.

## The bus, and who may say what

Topics are dotted strings. The host publishes on reserved namespaces —
`card. config. log. lookup. radio. rig. ui.` — and **a card may publish only
`<card_id>.*`**. Anything else is refused with a message naming the fix:

```
'radio.state' is in the reserved radio.* namespace, which only the host
publishes. Publish my_card.* instead.
```

That matters because the board trusts the bus: a card that could publish
`radio.state` could make every tile believe the rig had moved.

Declaring `publishes` and `subscribes` in `card.json` narrows things further
and documents the card's data flow:

```json
"publishes":  ["my_card.tuned"],
"subscribes": ["radio.state", "log.qso_added"]
```

- **Undeclared means unrestricted.** No `publishes` and the card may use all
  of `<card_id>.*`; no `subscribes` and it receives everything. So adding
  either is opt-in and never silently breaks a card that predates it.
- **`publishes` catches typos.** Publishing `my_card.tunned` when the manifest
  says `my_card.tuned` fails loudly instead of leaving a subscriber waiting
  forever for an event that never comes.
- **Two things arrive regardless of `subscribes`:** `card.settings_changed`,
  which the SDK subscribes to on your behalf so `card.settings()` notices
  edits, and your own `<card_id>.*`, because two tiles of one card are
  separate pages served by one process and one tile must be able to hear the
  other.
- **A handler outside `subscribes` can never fire**, so the SDK says so at
  startup rather than letting you debug a card that runs but never reacts.

To reach another card, publish in your own namespace and have it subscribe;
there is no way to publish *into* someone else's.

**How far this goes:** the same caveat as permissions below. A card's server
calls carry its token and are checked properly. A card's *page* is identified
by `Referer`, which the page itself controls. Treat this as a namespace policy
that stops honest cards colliding, not as protection from a hostile one.

## Following one station

Three cards show the same station without knowing about each other. The host
publishes `lookup.result` whenever anything looks a callsign up, and the map
and the path card draw from it:

- **Typing a callsign in the logbook** looks it up once the box holds
  something shaped like a whole callsign, so the map and the bearing are there
  before you have finished the QSO. It does not look up every prefix on the
  way: that was six requests per contact, which is what the services rate
  limit and then lock an account for.
- **Clicking a logged contact** switches all of them to that QSO, fills the
  callsign box, and opens the station panel. The grid that was logged wins
  over a fresh lookup — `?grid=` on `/api/lookup/<call>` overrides the locator
  for that one answer — because a portable activation or an operator who has
  since moved would otherwise plot in the wrong place.

An answer is remembered for `cache_days` (30). A provider refusing is never
remembered — it says nothing about the callsign — and "no such record" only
for an hour, so a new licensee appears. **Settings → Integrations → Clear
remembered lookups** (`DELETE /api/lookup/cache`) is the escape hatch for an
answer that has gone stale: a station that has moved, or a grid corrected at
the provider after we asked.

The `path_map` card is a Leaflet map. It repeats horizontally like any tiled
map, so you can centre it on whichever continent you please and your contacts
are there — a path across the Pacific is drawn continuously into the world
copy next door rather than stopping dead at the antimeridian.
Scroll to zoom, drag to pan, click
either pin for that station's details and public links, and the ⌂ button
returns to the default framing. A new station reframes the map unless you have
moved it yourself.

**Base maps.** Three choices in the tile's settings:

| | needs | notes |
|---|---|---|
| **built-in (offline)** *(default)* | nothing | Natural Earth 1:110m, bundled. Works with no internet and no account. |
| OpenStreetMap | a connection | Fine for occasional use. OSM's own tiles are not meant for heavy application traffic. |
| custom tiles | a connection, usually a key | Paste any `{z}/{x}/{y}` template and your own credit line. This is the one to use for satellite or terrain. |

Leaflet is bundled in the card, not loaded from a CDN, so the map works
offline and inside a packaged build. Google Maps is deliberately not an option:
it needs an API key with billing enabled and its terms restrict mixing with
other base maps, which would mean every user had to open a cloud account
before the map worked.

**Projection.** Flat, or **azimuthal centred on your station** — every bearing
leaves the middle as a straight line and every distance from the centre is to
scale, which is the geometry a beam heading actually lives in. It only works
with the built-in base map: no tile service publishes tiles in that
projection, so picking it switches the base map rather than drawing a broken
one.

**Layers**, from the control in the corner:

- **Greyline** — the day/night terminator and the subsolar point, worked out in
  the browser from the clock. No network, no service, no provider. Verified
  against the solstices and the equinox.
- **Logged contacts** — every contact in your log that has a grid, each with a
  line home. Ticking the layer fetches the log; unticking drops it. Click a
  dot for the contact, or a row in the logbook to frame that path.
- **Absorption (D-RAP)** — NOAA's D-region absorption, as the highest
  frequency being absorbed. An empty layer is the good news: most of the time
  the ionosphere is undisturbed, and the card says so rather than looking like
  it failed to load.
- **Ionosondes (MUF)** — measured MUF and foF2 from about a hundred
  ionosondes, via prop.kc2g.com and GIRO. Points, not a field: the familiar
  smooth MUF map is an interpolation of exactly these stations, and plotting
  the measurements says what is known and where without inventing the rest.
- **Clouds** — OpenWeatherMap, and the only layer here that needs an account.
  Paste a key in the tile's settings; without one the layer is hidden rather
  than offered and broken.
- **Aurora** — NOAA SWPC's OVATION forecast. Public data, no account. It comes
  through the host, which caches it for five minutes and drops the cells at
  zero: the raw grid is about 1.5 MB of mostly nothing, and every open tile
  polling NOAA directly would be both rude and slow.

## The log

Typing a callsign fills in a panel above the table before the QSO is even
finished: who they are, their country and state with a flag, how far and on
what bearing, how many times you have worked them and what you wrote down
about them last time. It costs no extra request — it is the same lookup that
prefills the form.

The table groups by date, newest first, and carries signal reports, frequency,
distance in your units and what each contact has been confirmed by. **Columns**
chooses which of those to show and the choice is kept on the tile, so two
logbook tiles can show different things. The search box searches the whole log,
not the page on screen.

The table pages: 30 contacts at a time by default, and the picker beside
**Columns** goes from 10 to 250 or all. Ordering and paging both happen in the
host — `GET /api/log/page?q=&limit=&offset=` returns the rows and the total —
because sorting a page in the browser sorts only that page, which silently
drops contacts off the end of every page but the last.

## Logbook sync

Settings → Integrations → Logbook sync. Credentials there, then Pull or Push.

| | pull | push |
|---|---|---|
| **LoTW** | username + password | **only through TQSL** |
| **QRZ logbook** | API key | API key |
| **World Radio League** | API key | API key, one contact at a time |

Each service has its own **Pull** and **Push**, and there is an **Import
ADIF** button for a file from anywhere else — another logger, a contest, a
club's export. All three go through the same importer, so they all dedupe the
same way.

**Pulling never duplicates what you already have.** An incoming QSO is matched
against the log on callsign, date, band, mode and a start time within 30
minutes — two logs almost never agree to the second, and matching on callsign
alone would merge every contact you have ever had with a regular. A match is
updated with whatever the other log knows and you don't, so a confirmation
lands on the QSO it belongs to rather than beside it. Run the same pull twice
and the second says "nothing new".

**LoTW uploads cannot be done with a password.** ARRL only accepts QSOs signed
with your station certificate, and that certificate lives inside
[TQSL](https://www.arrl.org/tqsl-download) and does not leave it. So "Push to
LoTW" hands your log to TQSL to sign and upload if it is installed, and
otherwise tells you to export ADIF and drop it on TQSL yourself. Anything
claiming to upload to LoTW with a username and password would be lying about
what it was doing.

**The QRZ logbook API key is not your QRZ password.** It is per logbook, from
that logbook's own settings page at logbook.qrz.com, and QRZ gates the API
behind a subscription.

**Auto sync runs to one service, not both.** Ticking auto on one unticks the
other. Two automatic syncs would push the same new contacts to both and pull
from both into the same log, and a confirmation arriving from one would be
uploaded to the other on the next round.

**Uploads default to a dry run.** Push counts what it would send and sends
nothing until you untick it, and "Only QSOs from" narrows it to a date. There
is no undo at the far end, and "push my log" to a logbook with ten years in it
is not something to discover you meant differently afterwards. **Test
connection** is read-only and is the safe first thing to press.

A sync runs off the request thread and reports on the `log.sync` bus topic:
a year of QSOs is a slow download, and a request handler blocking on it would
hold one of the server's threads for the duration.

## Flags

Every callsign is shown with the flag its prefix implies — in the log, in a
contact's details, on the map and in the path card. `services/dxcc.py` holds
the prefix table; the flags are SVGs served from this app, not from a CDN,
because which countries you work is nobody else's business and a shack with no
internet should still see them.

It is a prefix table, not `cty.dat`, so treat it the way you treat the rest of
the prefix fallbacks: it is right about where somebody is, and it is not a
DXCC reference. Scotland, Wales and England are three DXCC entities and one
flag; Alaska and Hawaii fly the US flag. Places with their own flag — Puerto
Rico, Bermuda — keep it. A prefix it doesn't know shows no flag rather than a
guess.

## Units

One setting, in Settings: kilometres, miles or nautical miles. Every card that
shows a distance uses it — the path card, the map, the logbook readout — and
they all show **one** distance. Printing km beside miles looks generous and
is really the card declining to decide, leaving the reader to pick. Changing
it takes effect immediately, with no reload.

## Filling in your grid

Settings has two buttons under the grid field.

**Use my location** asks the browser for a position and turns it into a
locator. The coordinates go to the host in a POST body, never a query string,
so they don't end up in a log or in history, and nothing leaves the machine.
In the desktop app this usually fails — a webview has no location provider —
and it says so rather than spinning.

**From my callsign** looks your callsign up and takes the grid from the
result. It refuses to fill anything in when the answer is approximate: the
built-in prefix table replies with the middle of a country, and a station grid
that is silently 300 km out makes every distance the dashboard ever reports
wrong.

### Lookup providers

| provider | account | covers |
|---|---|---|
| **Automatic** (default) | none | callook then HamDB, then your account if set |
| callook | none | US |
| HamDB | none | US and Canada |
| HamQTH | free registration | worldwide |
| QRZ.com | paid XML subscription | worldwide |
| Offline | none | built-in prefix table only |

**Automatic needs no account and is the default.** Between them callook and
HamDB cover US and Canadian callsigns for free. Anywhere else needs an
account, and a free HamQTH one is enough — QRZ is the only one that charges,
and only for its XML interface. Without that subscription QRZ refuses the
session, and hamdash shows you what QRZ said instead of quietly falling back
to a guess.

**LoTW is not in that list on purpose.** It is a QSL confirmation service, not
a callsign directory: there is no public per-callsign page and no lookup API,
so it cannot fill in a grid. What it does publish is a list of who has
uploaded recently, which would answer "is this station on LoTW" — a different
feature, not done yet.

Lookup results carry `links` to the public pages for that callsign (QRZ,
HamQTH, callook) for a card to offer.

## Permissions

A card gets the shared services it declares, and no others:

```json
"permissions": ["radio", "log", "lookup", "space"]
```

Declare nothing and the card can't touch `/api/radio/*`, `/api/log*`,
`/api/lookup/*` or `/api/space/*`. The refusal names what's missing rather than returning a bare
403, so a card author can act on it.

**How far this actually goes:** a card's *server* calls carry that card's
token and are checked properly. A card's *web page* runs in the dashboard
origin with no token, so those calls are matched on `Referer` — a speed bump,
not a sandbox. Any card page can reach any host API if it wants to, and can
reach other cards' pages through the DOM. Real isolation means serving card
pages from their own origin with a CSP, which is a bigger change. Treat
permissions as "declared intent, enforced on the backend," not as protection
from a hostile card.

## Themes

A theme is a JSON file of CSS custom properties in `<HAMDASH_HOME>/themes/`.
Copy one from `hamdash/static/themes/` — `night-watch` (dark teal),
`blue-hour` (deep navy, cyan highlights), `sixty-hertz` (warm amber),
`daylight` (light) — and change the values; it shows up in the theme picker on
next load. Light and dark are just themes; there is no separate light/dark
switch to keep in sync.

Ten properties, and every card reads the same ten, so a theme only has to be
written once. Keep `--accent` at 4.5:1 against `--panel`: it is the link and
heading colour, and everything else in the palette is chosen around it.

## If something goes wrong

There is a log: `<config dir>/hamdash.log` (rotated, 4 files). It records the
Python and pywebview versions, every card's stdout, and any unhandled
exception including ones in background threads — which is what you want on
Windows, where the app usually has no console to print to.

```
%APPDATA%\hamdash\hamdash.log
~/Library/Application Support/hamdash/hamdash.log
~/.config/hamdash/hamdash.log
```

`GET /api/bus/stats` shows how many event streams are open, and
`GET /api/health` lists wedged cards. Both are useful when the UI looks stuck.

## Failure modes worth knowing

- **Card shows "not answering"** — its process died or never bound its port.
  The tile shows the last 20 lines of its stdout; `/api/cards/<id>/restart`
  resets the restart counter. A card that is merely slow to start is waited
  for, not failed.
- **`wedged` is not `crashed`** — a card can bind its port and then hang on a
  serial read or a deadlock, and an open socket will happily report that as
  healthy forever. The supervisor polls each card's `/_health` and reports
  `wedged` separately. Wedged cards are deliberately *not* auto-restarted:
  restarting something that's hung while holding a device handle usually makes
  it worse. Restart it yourself once you know why.
- **Removing a tile stops the card** — when a card's last tile goes, its
  process is reaped on the next layout save. Previously they accumulated for
  the life of the session.
- **Restart storm** — a card that crashes on boot is retried 5 times with
  backoff, then left stopped. Fix it, then restart it from the card menu.
- **Lookups quietly approximate** — callook is US-only and rate limits; when a
  provider is unreachable the result falls back to a prefix table, flagged with
  `"approximate": true` and a `note`. Don't treat those coordinates as real.
- **Distance needs your grid** — `/api/grid/distance` 400s if the station grid
  isn't set in settings.
- **Framed pages may refuse to load** — many sites send `X-Frame-Options`, and
  a `url` card can't override that. Use a `python` card that fetches and
  re-renders instead.
- **"Get an app to open this 'about' link" (Windows 11)** — fixed, but worth
  knowing why: `window.pywebview` is injected asynchronously and fires
  `pywebviewready`. Checking for it at page load is a race, and losing it used
  to mean falling through to Picture-in-Picture inside WebView2, which handed
  the new-window request to the Windows shell. The UI now waits for the bridge
  and refuses to fall back in a native shell. If pop-out reports the bridge
  never loaded: `pip install -U pywebview`.
- **Links inside cards** — in the desktop shell, any `target="_blank"` or
  `window.open()` would escape to the OS the same way. `hamdash.js` intercepts
  both and routes them through `HD.openExternal()`, which opens the user's real
  browser. If you write a card, use `HD.openExternal(url)` rather than
  `window.open`; plain `<a href>` links are handled for you.
- **Pop-ups get blocked** — browsers only open windows from a click, which is
  why popped tiles show a button after a reload instead of silently failing.
  The desktop app has no such limit.
- **Only one Picture-in-Picture window** — the browser API allows exactly one,
  so popping out a second card in Chromium pops the first one back in. If you
  want several floating cards, run the desktop app.
- **Always-on-top isn't universal** — it's a no-op on some Linux window
  managers and under Wayland. The toggle reports the failure instead of lying
  about it.
- **Closing a popped window returns the card to the board** — deliberate: a
  tile marked "popped" with no window would be unreachable. Removing the tile
  (✕) closes its window too.
- **"maximum recursion depth exceeded" spam, then a frozen window** — that
  was the js_api introspection bug described above, fixed in 0.3.3 and
  covered by a test. If you ever see a repeating attribute chain in the log
  (`.Empty.Empty.Empty…`), something data-shaped got attached to the bridge.
- **Runaway log lines** — one record is capped at 2000 characters. A pywebview
  object walk or a card dumping a huge payload would otherwise fill the log
  and flood the console, burying the actual error.
- **Thread budget, if you add a lot of cards** — the server is blocking, so
  every open event stream holds one thread: the board, each card page, and
  each card process keeps one. Streams are capped at 55 seconds and clients
  reconnect, which is how a thread held by a client that vanished ever comes
  back; the pool is 64 (`--threads`) and the bus refuses more than 24
  concurrent streams with a 503 rather than starving it. If you see
  "too many event streams open", that's the ceiling, not a bug in your card.
  A window that opens empty and then stops responding is the old version of
  this bug.
- **rigctld is single-socket** — the host serialises access. If you also run
  WSJT-X against the same rig, point both at rigctld, not at the serial port.

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

**The builds are unsigned.** Windows shows a SmartScreen warning and macOS
needs right-click → Open the first time. Signing needs a certificate and an
Apple developer account.

## Not done yet

ADIF import dedupe, per-card settings UI (manifest `settings` is read but not
rendered), a real map tile layer in `beacon_map`, LoTW/QRZ upload, cty.dat for
proper DXCC. On the layout side: an option to free the grid slot while a card
is popped out (right now it holds its place), multi-monitor placement memory
across restarts, and snapping popped windows to screen edges.

On packaging: Linux builds (CI builds Windows and macOS only — both have a
pip-installable pywebview backend, while Linux needs system GTK/WebKit
packages and a build without them would silently be browser-only), code
signing, an application icon, and bundling Hamlib so `rigctld` doesn't have to
be installed separately.
