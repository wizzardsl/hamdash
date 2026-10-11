# Using the board

Arranging tiles, stacking them into tabs, popping one out into its own window, and where a card's settings live.

- [Moving cards around](#moving-cards-around)
- [Stacking cards](#stacking-cards)
- [Popping a card out](#popping-a-card-out)
- [Tiles, cards and settings](#tiles-cards-and-settings)
- [Themes](#themes)
- [Units](#units)

*[&larr; back to the README](../README.md)*

---

## Moving cards around

Drag a tile by its header, resize it from the bottom-right corner — no mode to
enter first. **Arrange** just turns on the grid guides and keeps the resize
handles visible. Overlaps are resolved by pushing tiles *down*, never sideways:
horizontal position is what people place deliberately, vertical order isn't.

**The board can be arranged without a mouse**: tab to a card's header and
the arrow keys move it, shift and the arrows resize it. The header says
where the card has landed as you go.

**Escape puts a drag back** where it started — nothing docks, nothing moves,
nothing is saved. **Hold a card near the top or bottom edge and the board
scrolls under it**,
faster the closer you get to the edge, so moving a card from the bottom of a
long dashboard to the top is one gesture rather than drop-scroll-repeat. While
there is still board above to reach, dragging up means scroll; only once there
is none does leaving the top mean pop the card out into its own window.

A card's status dot turns **amber** when the host could not make sense of
part of its `card.json` — the card still runs, but something its author
wrote is being ignored, and the dot says which. Hover it.

Adding and removing a card: the **Cards** menu lists everything installed with
a tick beside whatever is on the board. Tick to put a card on, untick to take
it off — and closing a tile with its own **✕** unticks it there, so the menu is
always a true picture of the board rather than a separate thing to keep in
sync. Either way a toast offers **Undo**: a card carries its own per-tile
settings, so closing one is not a thing to have to be sure about.

It is a dropdown rather than a dialog, because ticking a few cards on and off
is something you do in a run and a modal makes you reopen it each time.

A card can have several tiles (two `web_embed` tiles are two different pages),
so a row with more than one shows a count and unticking takes all of them off
at once. That would throw away per-tile settings, so instead of asking first
the toast offers **Undo**, which puts them back with their settings intact.

**Browse & drag…** in that menu opens the older sheet, where a row can be
dragged out and dropped exactly where you want it on the board.

## Stacking cards

Drag a tile **onto another tile's tab strip** — the header row where its tab
names sit — and it docks as a **tab**: one box, a strip of names across the
header, one card showing at a time. The strip lights up as you cross it. Drag
a tab off the strip and it comes back out as its own tile. Dropping a stack
onto another joins them.

Only the strip docks. Drop anywhere else on a tile and the card simply moves,
because on a full board every spot is over *something*, and making all of it a
dock zone turned ordinary rearranging into accidental tabs.

This is the board's doing, not the cards' — nothing in a card knows it is in
a stack, so anything you write stacks like everything else.

A tab you are not looking at **stays loaded and keeps running**, which is what
makes switching back instant and why a half-typed contact is still there when
you return. Cards that hold something are told when they go out of sight so
they can let go: the decoder and the spectrum give the sound card back, and
the spots card stops polling. They pick straight up again when you switch
back.

The card you drag reloads when it lands — moving an iframe to a new parent
restarts it, and there is no way round that in a browser. The cards already in
the tile are left alone.

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

## Units

One setting, in Settings: kilometres, miles or nautical miles. Every card that
shows a distance uses it — the path card, the map, the logbook readout — and
they all show **one** distance. Printing km beside miles looks generous and
is really the card declining to decide, leaving the reader to pick. Changing
it takes effect immediately, with no reload.
