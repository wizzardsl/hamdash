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

**New from URL…** makes a card out of a web page without your writing
anything. Give it a name and an address and it joins the menu like any other
card, and goes straight onto the board. It is a `url` card — the kind that is
nothing but a `card.json` — and the menu just writes that file for you, in
your cards folder, so it is still there after a restart and still there if you
go and edit it by hand.

A bare `qrz.com` is enough; it gets `https://` the way an address bar does.
Only `http://` and `https://` addresses are taken. That is not fussiness:
a card page runs on the board's own origin, so a `javascript:` or `file:`
address would not be a broken tile, it would be code running with the board's
privileges.

Cards made that way carry a **×** in the menu, and nothing else does — they
are the ones that accumulate, and there is nothing in one of them but a name
and an address, so deleting it loses nothing. A card you *wrote* is your code:
the menu will not delete it and neither will the host, so remove its folder
yourself if you mean to. Deleting takes its tiles off with it and offers
**Undo**, which writes the card back under the same id and puts the tiles
where they were.

**Tidy up**, also in that menu, pulls every tile straight up into the empty
space above it. Taking a few cards off the board leaves holes — the board
does not close them by itself, because a layout that rearranged itself every
time you removed something would move the tiles you had placed on purpose —
and this closes them all in one press. It only moves tiles **up**, never
sideways, so how you arranged them left to right survives. It offers
**Undo**, and tells you if there was nothing to do.

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

## On a phone

The board is a grid you drag tiles around on. On a phone that is twelve
columns of thirty pixels, so hamdash shows a different shell there: one card
at a time, chosen from a bar along the bottom.

It is picked by the size of the window, not by what browser you are using —
so a narrow desktop window gets it too, and a tablet in landscape does not.
Nothing is configured, and nothing is stored on the server: open the board
from your phone and it is simply there.

The bar holds four cards and a **More**, which lists the rest. Which four
follows what you reach for away from the desk: the logbook first, then spots,
then rig control and receive audio, then whatever else is on your board in
board order. Every card that is on the board is reachable, including ones
stacked behind a tab — there are no tabs here, so each gets its own place.

**It is the same board.** There is no separate phone layout to keep in step:
add a card on the phone and it is on the board at the desk, in the next free
row. Per-tile settings are the tile's, so they travel too (⚙ in the strip
above the card, when that card has any).

To use the real board anyway — it works, it is just cramped — **More → Full
dashboard**. A **Phone view** button then appears in the top rail to go back.
That choice is remembered in the browser you made it in, so it never follows
you to another machine, and until you make one the shell follows the window:
turn the phone sideways and it changes with you.

Cards only load when you first open them, so a phone does not start every
card process on your station to show you one, and the card you switch away
from is told nobody is looking — which is what stops a decoder or receive
audio holding the radio's sound card from a tab you last looked at in the
morning.
