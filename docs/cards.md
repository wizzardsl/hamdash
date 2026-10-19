# Writing a card

A card is a directory with a `card.json` in it. That is the whole contract.
Drop one in your cards folder, press **Rescan**, and it is on the board.

Cards are deliberately small to write and hard to break the app with: a
python card is its **own process**, so one that crashes, hangs, or wants a
library nothing else wants takes only itself down.

- [Where your card lives](#where-your-card-lives)
- [The three kinds](#the-three-kinds)
- [card.json](#cardjson)
- [Settings](#settings)
- [Permissions](#permissions)
- [The python SDK](#the-python-sdk)
- [The browser side: HD](#the-browser-side-hd)
- [The bus](#the-bus)
- [Things that will bite you](#things-that-will-bite-you)
- [When it does not work](#when-it-does-not-work)

---

## Where your card lives

| | |
|---|---|
| Windows | `%APPDATA%\hamdash\cards\<your_card>\` |
| macOS | `~/Library/Application Support/hamdash/cards/<your_card>/` |
| Linux | `~/.config/hamdash/cards/<your_card>/` |

Or wherever `--home` points, with `cards/` under it.

The quickest start is to copy a working one — `docs/card-template` is a card
that runs as it stands:

    cp -r docs/card-template <HAMDASH_HOME>/cards/my_card

If you installed the application rather than the source, the template ships
with it: look in the hamdash folder under `_internal/docs/card-template`.

Then **Cards → Rescan**. No restart: the host re-reads the directory and your
card appears in the menu.

Bundled cards are read first and yours second, so a card of yours with the
same `id` as a bundled one **replaces** it. That is how you fork `logbook`,
keep its tile, and change one thing.

---

## The three kinds

| `kind` | what it is | what you write |
|---|---|---|
| `url` | an address in a tile — a cluster page, a WebSDR, propagation charts | nothing but `card.json` |
| `html` | static files served from your card directory | HTML, CSS, JS |
| `python` | its own process, its own HTTP API, full access to the bus | Python, plus a page |

A `url` card does not need a text editor at all: **Cards → New from URL…**
on the board writes the manifest for you, and the **×** beside it in that
menu deletes it again. Only `http://` and `https://` addresses are accepted,
because a card page is served on the board's own origin.

Start at `url` and move up only when you need to. An `html` card can still
use every shared service, because those are HTTP endpoints on the host and
`HD` is a wrapper around them. You need `python` when you want to do work the
browser should not: hold a serial port or a sound card, cache somebody's API,
poll on a schedule, or keep state shared between tiles.

---

## card.json

```json
{
  "id": "my_card",
  "name": "My card",
  "kind": "python",
  "version": "0.1.0",
  "author": "VA7XYZ",
  "description": "One line, shown in the Cards menu",

  "entry": "card.py",
  "view": "index.html",

  "w": 4, "h": 3, "min_w": 2, "min_h": 2,

  "permissions": ["radio"],
  "publishes": ["my_card.tuned"],
  "subscribes": ["radio.state", "lookup.result"],

  "settings": {
    "watch": { "type": "string", "label": "Watch callsign", "default": "" }
  }
}
```

| field | meaning |
|---|---|
| `id` | directory-safe and unique. It is your bus namespace and your URL prefix. |
| `kind` | `url`, `html` or `python`. |
| `entry` | python only: the file to run. Default `card.py`. |
| `view` | the page shown in the tile. Default `index.html`. |
| `url` | `url` cards only: where to point. `http`/`https` only. |
| `created_by` | set to `hamdash` on a manifest the board wrote from **New from URL…**. It is what makes that card deletable from the menu; leave it off a card you write and nothing in the UI can remove it. |
| `w`, `h` | first size in grid cells. `min_w`, `min_h` the smallest it is useful at. |
| `permissions` | which shared services you may call. See below. |
| `publishes` | bus topics you will send. Omit for "all of `<id>.*`". |
| `subscribes` | bus topics you want. Omit for "everything". |
| `settings` | the per-tile form the host renders for you. |

**`publishes` and `subscribes` are optional and narrowing.** Leaving them out
means "my whole namespace" and "everything"; adding them restricts you. A bad
entry does not stop the card — the host drops it and the tile's status dot
turns amber with the reason.

---

## Settings

You declare them; **the host renders the form**. You never write a settings
UI, and the operator gets one that looks like every other card's.

```json
"settings": {
  "watch":   { "type": "string", "label": "Watch callsign", "default": "",
               "help": "Leave empty for everything" },
  "minutes": { "type": "number", "label": "Show the last (minutes)", "default": 30 },
  "loud":    { "type": "bool",   "label": "Only strong signals", "default": false },
  "band":    { "type": "select", "label": "Band", "default": "any",
               "options": ["any", "20m", "40m"] },
  "api_key": { "type": "secret", "label": "Service key", "default": "" }
}
```

Types: `string`, `number`, `bool`, `select`, `secret`, `audio_in`.

**`audio_in` is a `select` the host fills in.** A card cannot know what sound
cards the machine has, so listing them is the host's job: whatever you put in
`options` is offered first — `"options": ["rig", "mock"]` — and this machine's
real inputs follow. The value you get back is a device *name*, never an index,
because PortAudio indices shuffle whenever something is plugged in or a
Bluetooth headset wakes. A stored device that has since disappeared stays in
the list marked *not found now* rather than the sheet quietly selecting
something else.

**Settings belong to a tile, not to a card.** Two tiles of your card are two
sets of settings, and they travel with the tile — docking it into a stack or
popping it out keeps them. That is why anything per-instance hangs off the
tile uid, which arrives on every request as `?uid=` and `X-Hamdash-Tile`.

```python
conf = card.settings()          # this tile's, merged over your defaults
```

```js
const conf = await HD.settings();            // the same thing, in the page
HD.onSettings((values) => redraw(values));   // the operator edited them
```

**`secret` is handled for you.** The card that owns the tile gets the real
value because it has to use it; everyone else gets an empty string and a
`secrets_set` marker. Do not log it and do not echo it back into a form —
anything a form is shown, it will send back.

---

## Permissions

Shared services are opt-in. Ask for what you use, in `permissions`:

| permission | unlocks |
|---|---|
| `radio` | `/api/radio/*` — state, meters, frequency, mode, VFO, split, PTT |
| `log` | `/api/log*` — read, add, delete, ADIF, stats, sync |
| `lookup` | `/api/lookup/*` — callsign lookup and its cache |
| `space` | `/api/space/*` — aurora, D-RAP, MUF |

Everything else is open to any card: `/api/bands`, `/api/grid/distance`,
`/api/config` (station details, never secrets), `/api/audio/devices`,
`/api/themes`, `/api/publish`, `/api/events`.

Ask for a service you did not declare and you get a 403 that names the fix.

### Honest about what permissions are

For your card's **server** calls they are real: the SDK sends a token, the
host checks it, and a python card cannot reach a service it did not declare.

For calls from your card's **page** they are a guard against mistakes and
nothing more. Not because of the `Referer` header — a page cannot forge
that — but because **every card page shares one origin with the board and
with every other card**. A card page can read `parent.document`, find any
other card's iframe, and run code inside it. Measured, with a card that
declares no permissions:

| | |
|---|---|
| `fetch('/api/radio/state')` as itself | **403**, as intended |
| the same fetch inside the rig card's window | **200**, and the frequency |

That reach also covers the logbook, the operator's config and the settings
sheet, because those live in the board's page.

**What this means for you as an author:** declare your permissions honestly
and treat a 403 as a bug in your manifest rather than something to route
around. The guard exists to tell you what you forgot to declare. If you ever
find yourself reaching into `parent.document`, you are writing something the
operator would not want installed.

**And as somebody installing a card:** read it first. A card is a program on
your machine with your logbook in reach. Fixing this properly means one
origin per card plus CORS, which is an open thread rather than a promise.

---

## The python SDK

```python
from hamdash.sdk import Card

card = Card(__file__)
```

### Serving

```python
@card.get("/state")             # served at /cards/my_card/api/state
def state():
    return {"ok": True}

@card.post("/do")
def do():
    ...

@card.route("/thing", methods=["PUT", "DELETE"])    # anything else
def thing():
    ...

card.run()                      # last line; blocks
```

Routes are mounted under `/api` for you, and the host proxies
`/cards/<id>/...` to your process. `GET /api/_health` is provided.

### Shared services — `card.host`

| call | what it gives you |
|---|---|
| `card.host.radio()` | the live rig: frequency, mode, band, PTT, VFOs, split |
| `card.host.set_freq(hz)` | tune it |
| `card.host.lookup(call)` | callsign lookup, through the host's cache |
| `card.host.log_qso(qso)` | add a contact |
| `card.host.station()` | the operator: callsign, grid, name |
| `card.host.audio_devices()` | sound devices, already folded to one row per device |
| `card.host.tiles(card_id)` | which tiles of a card exist |
| `card.host.tile_settings(uid)`, `save_tile_settings(uid, values)` | another tile's settings |
| `card.host.get(path, **params)`, `card.host.post(path, payload)` | anything else on the host API |

Two modules are safe to import directly in a card process, because they drag
nothing in with them:

```python
from hamdash.services.bands import for_hz      # one band table, everywhere
from hamdash.services import audio             # device name matching
```

### Settings and tiles

```python
card.settings()            # this tile's values, over your manifest defaults
card.settings(uid)         # a particular tile's
card.defaults()            # just the manifest defaults
card.save_settings(values) # write them back
card.tile_uid()            # which tile is asking, inside a request
```

### Keeping things between runs

```python
card.store                     # a directory you own, survives restarts
card.load_json("cache", {})    # read it back, or the default
card.save_json("cache", data)  # atomic write
```

Use it for anything you should not have to fetch twice. A cache that lives
only in memory is not a cache: "no more than one request every five minutes"
is not honoured by restarting the app.

### The bus

```python
@card.on("radio.state")
def tuned(topic, payload):
    ...

card.publish("my_card.tuned", {"hz": 14074000})
```

A handler is called once per event. When the card starts it is also handed
the latest event on each topic it subscribes to, so it begins from the
current state (`radio.state` tells it where the radio is now). After that the
stream reconnects about once a minute and resumes where it left off: nothing
already delivered comes again, and nothing published during the reconnect is
lost. The same holds for `HD.on` in a page.

---

## The browser side: HD

Load it and the rest follows — theme, events, settings, units:

```html
<script src="/static/js/hamdash.js"></script>
<script>HD.cardId = 'my_card';</script>
```

| | |
|---|---|
| **your card** | `HD.card('my_card', '/api/state')`, `HD.cardRaw(id, path)`, `HD.cardPath(path)`, `HD.cardId`, `HD.tileUid` |
| **settings** | `HD.settings()`, `HD.saveSettings(v)`, `HD.onSettings(fn)` |
| **radio** | `HD.radio.state() .freq(hz) .mode(m, w) .ptt(on) .modes() .meters() .vfo(a) .swapVfo() .split(on)` |
| **log** | `HD.log.recent(n) .add(qso) .remove(id) .stats()` |
| **lookup** | `HD.lookup(call)` |
| **station** | `HD.station()` |
| **bands** | `HD.bands()`, `HD.bandFor(mhz)` |
| **distance** | `HD.distance(to, from)`, `HD.fmtDistance(km)`, `HD.units`, `HD.distanceUnit()` |
| **bus** | `HD.on(topic, fn)`, `HD.publish(topic, payload)` |
| **visibility** | `HD.visible`, `HD.hidden`, `HD.onVisible(fn)` |
| **raw** | `HD.get`, `HD.post`, `HD.put`, `HD.del`, `HD.postText` |
| **odds** | `HD.fmtHz(hz)`, `HD.bearingName(deg)`, `HD.flag(iso)`, `HD.openExternal(url)` |

**`HD.card()` puts the tile uid on every call**, which is what makes
per-tile settings work. Reach your own API through it rather than with
`fetch`.

It parses the reply as JSON. When your card serves something else - audio,
an image, a file - use **`HD.cardRaw(id, path)`**, which makes the same
call with the same uid and hands back the `Response`, so you can take
`arrayBuffer()` or `blob()` off it. For a URL you are not fetching at all,
such as an `<img src>` pointing at your own card, **`HD.cardPath(path)`**
adds the uid and nothing else. All three exist so nothing has to hand-roll
the uid, which is the part that is easy to leave out and silently wrong:
the call works, and the card answers about the wrong tile.

**Distance is formatted in one place.** Use `HD.fmtDistance`; never print
`km` yourself, and listen for `hamdash:units` if you hold a rendered string.

**Open external links with `HD.openExternal`.** A plain `target="_blank"`
inside the desktop shell is not a degraded tab — Windows offers to find an
app in the Store for it.

---

## The bus

One publish/subscribe channel for the whole board.

- The host owns `card.`, `config.`, `log.`, `lookup.`, `radio.`, `rig.` and
  `ui.`. **You own `<your_id>.*` and nothing else.** Publishing outside it
  gets a 403 that names the fix.
- Two things reach you whatever your `subscribes` says:
  `card.settings_changed` and `config.changed`. The SDK uses the first.
- The topic for the radio is **`radio.state`**, not `rig.state`.

Worth listening for: `radio.state`, `lookup.result` (what the board is
looking at now), `log.qso_added`, `config.changed`.

**Do not call a lookup in response to `lookup.result`.** A card did exactly
that and one click became 200 events.

---

## Things that will bite you

**Poll; do not open your own event stream from a card process.** Every SSE
stream holds one server thread for its whole life and the pool is finite.
Ring your recent results and serve everything since a sequence number.

**A hidden tab is still running.** A card that is not the one on top stays
loaded on purpose, so switching back is instant. Nothing in the browser tells
it, so the board does:

```js
HD.onVisible((on) => { if (!on) release(); else resume(); });
if (HD.hidden) return;      // inside a polling loop
```

If your card holds the sound card, a serial port, or somebody's rate limit,
honour it.

**Nothing tells a card that a tile was deleted.** If you hold a resource, mark
it wanted on every request and reap anything unwanted after a timeout. Count
the wants *per tile*, not per device, or one tile letting go closes something
another tile is still using.

**Be a good guest.** If you read somebody else's API, the rate floor belongs
in the card, not in the page — a filter bar invites a request per keystroke.
Fetch once, serve the rest from memory, filter in the browser, and let a
Refresh button mean "do not wait for the next poll" rather than "ask now".

**A failure has to wait its turn as surely as a success does.** Store failures
with their own retry time, or your page's poll will hammer whatever just
rate-limited you.

**A sound device is stored by name, never by index.** PortAudio indices
shuffle when anything is plugged in or a Bluetooth headset wakes up, so a
saved index silently becomes a different device.

---

## When it does not work

**The tile shows the error.** A card that will not start paints its own
traceback where the card would have been — you do not have to go and find a
log to see a typo. The page reloads itself while the host is still going to
retry, so **fixing the file brings the card back without touching the
dashboard**; once the host has given up retrying it says so and stops.

| symptom | what it means |
|---|---|
| the tile shows a traceback | that is your card's output; fix it and the tile fills itself back in |
| the tile says it has *stopped trying* | too many failed restarts; fix it, then restart hamdash |
| the tile says "Starting…" for ever | the process is alive but never bound its port |
| the status dot is **amber** | your manifest has something the host ignored; hover it |
| a 403 with a message | add that permission to `card.json` |
| the card is not in the menu | **Cards → Rescan**, and check `card.json` parses |

The log is at `<HAMDASH_HOME>/hamdash.log` — on Windows,
`%APPDATA%\hamdash\hamdash.log`. Everything your card prints goes there.

`GET /api/health` gives the card count, what is running and what is wedged.

---

## Using Claude to write one

`docs/card-template/CLAUDE.md` is written for that: copy the template folder,
open it with Claude Code, and it already knows the contract on this page —
the SDK surface, the permission names, the bus rules, and the handful of
things that are easy to get wrong.
