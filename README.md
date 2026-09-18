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
plugins/     rig_control, logbook, beacon_map, web_embed, hello_card
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

Adding a card: open **Add card** and either hit Add, or drag the row out of the
sheet and drop it where you want it on the board.

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

## Permissions

A card gets the shared services it declares, and no others:

```json
"permissions": ["radio", "log", "lookup"]
```

Declare nothing and the card can't touch `/api/radio/*`, `/api/log*` or
`/api/lookup/*`. The refusal names what's missing rather than returning a bare
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
Copy one from `hamdash/static/themes/` (`night-watch`, `daylight`,
`sixty-hertz`) and change the values — it shows up in the theme picker on next
load. Light and dark are just themes; there is no separate light/dark switch to
keep in sync.

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

## Not done yet

ADIF import dedupe, per-card settings UI (manifest `settings` is read but not
rendered), a real map tile layer in `beacon_map`, LoTW/QRZ upload, cty.dat for
proper DXCC, packaging to a single binary. On the layout side: an option to
free the grid slot while a card is popped out (right now it holds its place),
multi-monitor placement memory across restarts, and snapping popped windows to
screen edges.
