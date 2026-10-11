# Writing a hamdash card — notes for Claude

You are helping write a **card**: a small app that lives in a tile on a ham
radio dashboard. This file is the contract. The full reference is
`docs/cards.md` in the hamdash repository; everything here is the short
version plus the things that are easy to get wrong.

Ask what the card should *do* before writing it, and keep it small. A card
that draws one thing well is the point.

## What you are working with

A card is a directory holding `card.json` and whatever it needs:

```
my_card/
  card.json      the manifest: id, size, permissions, settings
  card.py        optional: a python process, if the card needs one
  index.html     the page shown in the tile
```

Three kinds, in `card.json`:

- `url` — an address in a tile. No code at all.
- `html` — static files. HTML and JS only; still gets every shared service.
- `python` — its own process, its own HTTP API, the bus.

**Pick the simplest that works.** Reach for `python` only when the card must
hold something (a serial port, a sound card), cache somebody else's API,
poll on a schedule, or share state between tiles. Most cards are `html`.

## The manifest

```json
{
  "id": "my_card",
  "name": "My card",
  "kind": "html",
  "version": "0.1.0",
  "description": "One line, shown in the Cards menu",
  "view": "index.html",
  "w": 4, "h": 3, "min_w": 2, "min_h": 2,
  "permissions": [],
  "settings": {}
}
```

`id` must match the directory name, be unique, and is the card's bus
namespace and URL prefix.

## Settings: declare, never build

Declare them in `card.json` and **the host renders the form**. Never write a
settings UI; the operator gets one that matches every other card.

```json
"settings": {
  "watch":   { "type": "string", "label": "Watch callsign", "default": "" },
  "minutes": { "type": "number", "label": "Minutes to show", "default": 30 },
  "loud":    { "type": "bool",   "label": "Strong signals only", "default": false },
  "band":    { "type": "select", "label": "Band", "default": "any",
               "options": ["any", "20m", "40m"] },
  "api_key": { "type": "secret", "label": "Service key", "default": "" }
}
```

Read them with `await HD.settings()` in the page, or `card.settings()` in
python. Redraw on `HD.onSettings(fn)` — the operator can edit them while the
card is running.

**Settings belong to a tile, not to the card.** Two tiles are two sets of
values. Anything per-instance hangs off `HD.tileUid` / `card.tile_uid()`.

## Permissions

Declare only what the card calls:

| permission | unlocks |
|---|---|
| `radio` | `/api/radio/*` — frequency, mode, meters, VFO, split, PTT |
| `log` | `/api/log*` — the logbook |
| `lookup` | `/api/lookup/*` — callsign lookup |
| `space` | `/api/space/*` — aurora, D-RAP, MUF |

Open to every card without asking: bands, grid distance, station config,
sound device list, themes, the bus.

A missing permission returns 403 with a message naming the fix. If you see
one, add it to `card.json` rather than working around it.

## In the page

```html
<script src="/static/js/hamdash.js"></script>
<script>
HD.cardId = 'my_card';
const conf = await HD.settings();
const rig = await HD.radio.state();          // needs "radio"
HD.on('radio.state', (s) => draw(s));
HD.onSettings((v) => redraw(v));
</script>
```

Useful: `HD.radio.*`, `HD.log.*`, `HD.lookup(call)`, `HD.station()`,
`HD.bands()`, `HD.bandFor(mhz)`, `HD.distance(to, from)`,
`HD.fmtDistance(km)`, `HD.fmtHz(hz)`, `HD.bearingName(deg)`,
`HD.flag(iso)`, `HD.openExternal(url)`, `HD.get/post/put/del`.

Call your own card's API with `HD.card('my_card', '/api/thing')` — it adds
the tile uid, which is what makes per-tile settings work. Do not use `fetch`
for it.

## In python

```python
from hamdash.sdk import Card
card = Card(__file__)

@card.get("/state")                   # -> /cards/my_card/api/state
def state():
    conf = card.settings()            # this tile's settings
    return {"rig": card.host.radio()} # needs "radio"

@card.on("log.qso_added")
def logged(topic, payload):
    card.publish("my_card.saw", {"call": payload["call"]})

card.run()                            # last line, blocks
```

Also on `card.host`: `set_freq(hz)`, `lookup(call)`, `log_qso(qso)`,
`station()`, `audio_devices()`, `tiles(card_id)`, `tile_settings(uid)`,
`get(path, **params)`, `post(path, payload)`.

For keeping things: `card.store` (a directory that survives restarts),
`card.load_json(name, default)`, `card.save_json(name, value)`.

Safe to import directly: `hamdash.services.bands` (one band table for the
whole app) and `hamdash.services.audio` (sound device name matching).

## Rules worth more than they look

**Use the shared thing rather than writing a second one.** Bands, distance
formatting, callsign lookup, flags and the band table all exist already, and
a second copy is a second answer in front of the operator. If you find
yourself writing a band table, stop.

**`HD.fmtDistance`, never your own `km`.** The operator chooses the units.

**Honour visibility.** A tile behind another tab stays loaded and keeps
running. If the card holds the sound card, a serial port or somebody's rate
limit, let go when hidden:

```js
HD.onVisible((on) => { if (!on) release(); else resume(); });
if (HD.hidden) return;        // inside any polling loop
```

**Poll; never open an event stream from a python card.** Each stream holds a
server thread for its whole life. Serve "everything since sequence N".

**Be a good guest with other people's APIs.** Put the rate floor in the card,
not the page. Fetch once, filter in the browser, and let Refresh mean "do
not wait for the next poll" rather than "ask now". Store failures with their
own retry time, or a poll will hammer whatever just rate-limited you.

**Nothing tells a card that a tile was deleted.** If the card holds a
resource, mark it wanted on each request and reap what is unwanted after a
timeout — counting wants per *tile*, not per device.

**A sound device is stored by name, never by index.** Indices shuffle when
anything is plugged in.

**Never `confirm()` or `alert()`.** This runs inside a native window where
browser chrome looks wrong and `window.open` escapes to the operating
system. Use `HD.openExternal(url)` for links out.

**The bus namespace is yours alone.** Publish `my_card.*` and nothing else;
`radio.`, `log.`, `lookup.`, `config.`, `card.` and `ui.` belong to the
host. The radio topic is `radio.state`, not `rig.state`.

**Do not call a lookup in response to `lookup.result`.** That is a feedback
loop; a card did it and one click produced 200 events.

## Installing and trying it

Put the directory in the hamdash cards folder:

- Windows `%APPDATA%\hamdash\cards\`
- macOS `~/Library/Application Support/hamdash/cards/`
- Linux `~/.config/hamdash/cards/`

Then **Cards → Rescan** in the app. No restart needed.

When it misbehaves: the tile's status dot is red if the process died and
amber if the manifest had something the host ignored (hover it). Everything
the card prints goes to `<HAMDASH_HOME>/hamdash.log`.

## What you cannot do from here

This card runs on the operator's radio. Do not write anything that keys the
transmitter without the operator asking for it, and do not invent frequencies
or callsigns to "test with" against a live rig — the mock rig exists for
that, and the app ships with it on by default.

Do not reach outside the card directory, do not touch the operator's config
or logbook except through the documented API, and do not add network calls
to services the card's purpose does not need.
