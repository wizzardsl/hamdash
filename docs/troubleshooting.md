# When something goes wrong

Where to look first, and the failure modes that are worth knowing about before they happen.

- [If something goes wrong](#if-something-goes-wrong)
- [Failure modes worth knowing](#failure-modes-worth-knowing)

*[&larr; back to the README](../README.md)*

---

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
