# Changelog

Notable changes per release. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/). The version of record is
`hamdash/__init__.py:__version__`, which is logged at startup.

## [Unreleased]

### Fixed
- **FT4 contacts from WSJT-X were logged as MFSK.** ADIF has no FT4 mode:
  it is `MODE=MFSK` with `SUBMODE=FT4`, which is what WSJT-X sends, and
  the logbook had no submode field, so the FT4 was dropped. The submode
  is kept now - for FST4, Q65, JS8, PSK31 and the rest of ADIF's
  family-and-member modes too - written back out as ADIF, shown as the
  mode, and sent to World Radio League as FT4. A copy already pushed as
  bare MFSK is recognised as the same contact, so a pull does not bring
  it back as a duplicate.

## [0.9.0]

### Fixed
- **The last event on every topic was sent again once a minute.** An event
  stream reconnects every 55 seconds by design, and each reconnect began
  with the newest event on every topic, as a new page does - so the last
  contact logged arrived again every minute. The logbook card jumped back
  to page one, the path map reloaded its contacts, and the rig card printed
  one contact some 150 times in an evening. Events now carry an id and a
  reconnecting page or card resumes after the last one it had: nothing is
  repeated, and anything published while it was reconnecting is delivered
  rather than lost. A new page still starts from the current state.

### Changed
- **Cluster spots show a mode when the spotter's comment names one.** The
  DX cluster has no mode field, so every cluster spot used to show none,
  even with "FT8 -14 dB" in its comment. A comment naming exactly one mode
  now supplies it, shown in italics with a tooltip saying it came from the
  comment, and the mode filter uses it. Two modes, or a request such as
  "PSE SSB", give none. Clicking such a spot's frequency tunes without
  changing the radio's mode. The telnet cluster follows the same rule
  instead of taking the comment's first word unmarked. A request is not a
  report: "PSE SSB", or "PSE 18 SSB" with a band in between, gives none.
- **Spots on FT8's or FT4's usual frequency show that mode** when neither
  the source nor the comment gave one - marked as coming from the
  frequency, filterable, and not sent to the radio. Checked against 500
  live cluster spots first: where the comment also named a mode, the two
  agreed every time. CW and phone segments are not guessed.

## [0.8.3]

### Fixed
- **Test connection tested the saved key, not the one just typed.** The
  logbook sync buttons in Settings - Test connection, Pull and Push for
  LoTW, QRZ and World Radio League - ran from stored config, so a key
  pasted and tested straight away was reported missing, and the only way
  through was Save, reopen Settings, Test. They now save what is on
  screen first, as the callsign lookup's Test always has, and the sheet
  stays open with the result.
- **A contact logged after a push could not be pushed.** The logbook
  card's `Push QRZ (n)` / `Push WRL (n)` counts were read when the card
  loaded and when a sync finished, so a push left them at "nothing to
  send", disabled, and the next contact stayed unpushable until the card
  was reloaded. They now follow every contact logged, arriving or
  deleted - including a delete made in another tile or browser, which
  the card had not been listening for at all.
- **Contacts from WSJT-X had no name, QTH, country or state.** A contact
  typed into the logbook card is filled in from the callsign lookup;
  one logged by WSJT-X went in as WSJT-X sent it - call, grid, reports.
  It showed as Canadian contacts with a blank province the lookup
  plainly knew. WSJT-X contacts are now filled the same way, blanks only:
  the grid that came over the air is kept, and a lookup that takes more
  than five seconds is not waited for - the contact is logged without it.

## [0.8.2]

### Fixed
- **A pulled log was offered straight back to the service it came
  from.** A push chose what to send by row id against a per-service
  marker that a pull never moved, so a new install that pulled 87 QSOs
  from QRZ showed `Push QRZ (87)` - and a dated push sent everything in
  the range regardless. Each contact now records which services hold
  it: a pull marks what it brought in or matched, a push marks what it
  delivered (a QRZ duplicate refusal included), and only what a service
  lacks is pending or sent. The old marker in config still counts, so
  an existing install does not re-push anything.
- **World Radio League collected duplicate copies of the log.** It
  stores a QSO it already has, flags it `isDuplicate` and answers
  success, so every full push added another copy - one account held 193
  records of 87 QSOs. A WRL push, dry run included, now reads what the
  logbook holds first and sends only what does not match. Copies already
  stored have to be removed on WRL's site; hamdash deletes nothing from
  an online logbook.
- **A pull counted records, not contacts.** The same account pulled as
  "193 already had" against a log of 87, which reads as the log having
  grown. Extra copies of one contact are counted apart now and the
  message says so: `... 87 already had - World Radio League sent 193
  records, 106 of them extra copies of the same QSOs`.
- **Restarting on Linux said the port was taken for a minute.** The
  check that runs before the server starts refused a port whose old
  connections were still in TIME_WAIT, though nothing was listening and
  waitress itself would have bound it. The check now allows that on
  macOS and Linux, as waitress does; a port somebody is really listening
  on is still refused. Windows is unchanged.

### Changed
- **The JS tests run as `node --test 'tests/js/*.test.js'`, on Node 24
  in CI.** `node --test tests/js/` fails on Node 22 and later, which
  read a directory argument as a module and report one failed test
  (`Cannot find module`) rather than running any. The glob needs Node
  21+, so CI moves off Node 20, which is end-of-life.

## [0.8.1]

### Fixed
- **The PTT watchdog could be silently killed off.** The rig polling
  thread is where the watchdog that takes an unheld transmission off
  the air lives, and an error during a poll ended that thread outright
  — leaving the app looking healthy with nothing left to unkey the
  radio. Seen for real: deselecting a radio while a poll was in flight
  raised inside the status read and stopped the poller three times in
  one session. The race is fixed and the loop now survives anything a
  poll can throw.
- **A logbook push says how far along it is.** World Radio League
  takes one contact per request and allows sixty a minute, so pushing
  87 QSOs takes about three minutes — during which the panel said
  "working…" and nothing else, which is indistinguishable from stuck.
  It now counts them off as they go. Nothing was wrong with the push
  itself.
- **Receive audio could end up holding the sound card against itself.**
  If the input stream failed to start, or failed while being closed,
  the card lost its handle to it — and because the tap is dropped from
  the table before it is stopped, nothing could ever close it again.
  The device stayed held for the life of the card process and every
  later attempt reported "another program may be holding that input",
  pointing at WSJT-X or fldigi when the holder was hamdash. Closing is
  now attempted for any stream that was built, cannot be skipped by an
  error while closing, and Stop waits for the device to actually be
  released before it returns.

### Changed
- **The PSK Reporter filters say how much is behind each choice.** The
  band list is built from what has actually been heard, so on a quiet
  evening it read "Any band / 40m / 80m" — which looks exactly like a
  card that only knows two bands. Nothing was being dropped; those were
  the only two on the air. Each band, mode and country now carries its
  count, and the counts follow the time window while the list itself
  does not, so narrowing the window shows you a band at (0) rather than
  removing the one you had selected.

## [0.8.0]

### Added
- **A phone shell.** The board is a grid you drag tiles around on, and on
  a phone that is twelve columns of thirty pixels — a six-card board came
  out as 190-pixel windows two abreast, each scrolled inside itself. Open
  the board on a phone and you now get one card at a time, chosen from a
  bar along the bottom: the logbook first, then spots, then rig control
  and receive audio, then whatever else is on your board, with **More**
  for the rest. It is the same board and the same per-tile settings —
  there is no second layout to keep in step — and nothing to configure.
  Cards load only when you first open them and are told when you switch
  away, so a phone does not start every card on your station to show you
  one, and nothing holds the radio's sound card from a tab you last
  looked at this morning. **More → Full dashboard** gives you the real
  board anyway; the choice is remembered in that browser, and until you
  make one the shell follows the window, so turning the phone sideways
  changes it with you.

### Fixed
- **Receive audio no longer stops when you look at another card.** It
  released the sound card as soon as its tile went to the background —
  defensible for the spectrum and the decoder, which produce something
  you read, and backwards for the one card whose whole point is being
  listened to while you do something else. On a phone, where every card
  but the one on screen is hidden, it meant you could not listen and
  change frequency at the same time; on the board it silenced a stacked
  tile the moment you looked at one of its neighbours. Stop is what ends
  listening now — which is still what hands the sound card to another
  program.
- **The logbook and spots tables no longer wrap into five-line rows when
  the card is narrow.** A phone, or a two-column tile on a crowded board:
  the Where column is free text from somebody else's API, and left to
  wrap it meant six spots filled the screen where twenty now do.

## [0.7.1]

### Fixed
- **Stopping receive audio in one browser no longer stops it in
  another.** The card counted listeners by *tile*, and every browser
  showing a tile sends the same id — so with the board open in two
  places, which is the whole point of playing the radio over the
  network, either one pressing Stop took the sound card from the other,
  whose next poll took it straight back. It reads as audio that will not
  start again after a Stop. Each page now identifies itself, so Stop
  releases only your own listening and the device closes when the last
  real listener goes.
- **A decoder tile in a background tab no longer takes the sound card
  from another browser showing it.** The decoder counted listeners by
  tile too. A hidden tile hands its input back — that is what stops a
  parked decoder holding the radio's audio against WSJT-X — but it was
  releasing the whole tile, so with the board open in two places and the
  Decoder tab selected in only one of them, the hidden one's release
  deafened the one on screen. Its Stop button was never the problem:
  `running` is a tile setting, so pressing Stop legitimately stops every
  browser showing the tile.
- **The receive audio card says why there is no sound.** It printed
  "Listening." whether or not a single sample reached the speakers,
  which is the one thing you actually care about — so a silent card and
  a working one looked identical. It now names the three things that
  produce silence with everything else working: the browser having
  paused audio, the volume slider at zero (stored per browser, so
  reloading the page cannot clear it), and audio arriving that is itself
  silent. None of them is a failure; each is a second's work once you
  are told which it is.

## [0.7.0]

### Added
- **The receive audio card's input is a dropdown now**, not a box you
  type a device name into from memory. It lists `rig` and `mock` first,
  then every sound input this machine has. `audio_in` is a new settings
  type a card can ask for and the *host* fills in — a card cannot know
  what sound cards the machine has. A stored device that has since
  disappeared stays in the list marked *not found now*, rather than the
  sheet quietly selecting something else.
  It still defaults to `mock`: a card that needs hardware to show
  anything cannot be demonstrated, and on a fresh install `rig` has no
  radio audio to follow, so it would fall through to the system default
  input — which is to say it would open somebody's microphone unasked.
- **WSJT-X logging.** Every contact you log in WSJT-X lands in the log
  as you make it — and only then. A live FT8 session broadcasts hundreds
  of decodes and status updates a minute; none of them touch the log,
  because only the record WSJT-X sends when you press **Log QSO** does.
  Verified against a live WSJT-X 3.0.2: 200 datagrams in, nothing
  written. Point its
  *UDP Server* at the same address and port. Contacts go through the
  same ADIF importer as everything else, so the same QSO arriving twice
  updates one row rather than making two.
  **On by default**, on WSJT-X's own default address and port, so
  pointing WSJT-X at hamdash is the only step. It binds loopback, so it
  is reachable only from the machine hamdash runs on and nothing is
  offered to the network.
- **A status line that says whether it is actually working**, which a
  stored "enabled" cannot: the port can be open and perfectly correct
  while WSJT-X points somewhere else, and from here those look identical
  until a heartbeat arrives. Three states — off, listening but nothing
  heard, connected with a version and a count — and the two that are not
  "connected" name the fix.
- **A port somebody else holds is retried, never shared.** JTAlert and
  GridTracker commonly have 2237, and on Windows two sockets *can* share
  a UDP port, with datagrams going to one of them unpredictably — half
  your contacts to each program. So hamdash will not take it, and keeps
  asking instead of giving up: close the other program and the listener
  has the port within a quarter of a minute, with no restart. The
  message names the likely culprit and points at WSJT-X's multicast
  support, which is the way to run several at once on purpose.
- **The path readout has a bottom as well as a top.** It gave the MUF and
  the highest band and nothing about what the ionosphere was taking away
  underneath, so a daytime path offered 80 m as readily as 20 m. There are
  now three lines under it: **D-layer loss** in dB for the bands in play,
  a **Daylight floor** in MHz, and the existing flare line.
  The loss model is in `prop.js` beside the MUF, with no DOM and no clock,
  and `tests/js/prop.test.js` drives it.
- **It says which numbers are measured and which are modelled, because
  they are not the same kind of claim.** The MUF comes from ionosondes
  that physically sounded the layer. Nothing sounds the D layer — it is
  too low and too collisional — so the floor is computed, and the line
  says so. The shape is solid physics (1/(f + f_H)², solar zenith angle,
  solar activity, once per hop); the constant is calibrated. Read the dB
  as an ordering rather than a measurement: what it is reliably right
  about is that 160 m is shut in daylight and 20 m is barely touched.
- **Nothing is called a LUF, deliberately.** Absorption is a property of
  the path; a LUF is a property of your station. 5 W of FT8, 100 W of CW
  and a linear have three different answers over the same path at the
  same second, which is why VOACAP asks for power and antenna gain. So
  the dB are shown and you apply your own judgement, and the one line
  that does turn them into a frequency says how much loss it assumed.

### Fixed
- **"Something else is already listening on UDP 2237" now names another
  copy of hamdash first.** A second instance — a `--home` one left over
  from testing, say — claims that port on startup like anything else,
  and the message sent you looking at JTAlert instead. Close whatever
  holds it and the listener takes the port by itself within a quarter of
  a minute; no restart.
- **A long device name no longer stretches the settings sheet off the
  screen.** A `<select>` is as wide as its widest option unless told
  otherwise, and a Windows sound device name runs to eighty characters,
  so the sheet scrolled sideways and carried the dropdown arrow off the
  edge — which made a perfectly good dropdown read as a text box.
- **Map markers went dead on a busy band.** The shared space-weather
  layer adds its own canvas to the map, and it landed on top of the one
  a card had already made for its own markers — a Leaflet canvas does
  not pass mouse events through, so every hover and click was swallowed
  by an empty overlay. It only showed when there were enough spots to
  push the card onto a canvas in the first place, so a quiet band worked
  and a busy one did not, which is what made it look intermittent.
  Nothing drawn on that canvas is interactive, so it no longer takes the
  mouse at all. Fixed in the shared map machinery, so every map card
  gets it.
- **Clicking the PSK map no longer changes what the card is watching
  when you are watching a callsign.** In Call mode a click did double
  duty: it switched the card to Grid mode and rewrote the box with the
  square under the pointer, so a click anywhere — including a near miss
  on a spot you were trying to read — silently stopped answering "who is
  hearing me". A click re-aims the card only in Grid mode, where
  pointing at a place is the question being asked. The mode buttons are
  how you change the question.
- **You can read a single reception report again.** Clicking a spot on
  the PSK Reporter map opened its popup and then immediately threw it
  away: the click reached the *map* as well as the dot, and a click on
  the map means "watch that grid square", so the card switched to grid
  mode and reloaded. It read as the card refusing to show you a spot.
  Measured in a browser rather than guessed at — Leaflet fires a click
  on the layer **and** on the map, on the canvas renderer and the SVG
  one alike, so the renderer in use was no defence.
- **Hovering a spot shows the signal, not just the callsign.** The
  number you want off a reception report was two clicks away. A station
  with one report shows it — `JG3RPL · -13 dB · 20m FT8 · 2m ago`; one
  with several shows its best and the count, matching the dot's colour,
  which is the best report rather than the latest.
- **The logbook shows a contact logged elsewhere, instead of refreshing
  the page you were on.** A contact arriving from WSJT-X, an ADIF import
  or a QSL pull sorts onto the *first* page — so if you had paged back
  through the log, the card dutifully reloaded page three and nothing
  appeared to happen. It goes back to the first page now. An active
  search is left alone: a search is an explicit question, and silently
  clearing it would answer a different one.
- **Importing a file no longer disconnects every open page.** An ADIF
  import announced each contact separately, and subscriber queues are
  256 deep — so importing a few thousand QSOs dropped every open
  board's event stream as a stalled consumer, part-way through the
  import that was doing it. One event per import now, carrying a contact
  that was added *and* the counts, so a card reading the contact keeps
  working and a file also gets its totals. An import that changed
  nothing says nothing.
- **A flare floor below the HF bands is no longer reported.** D-RAP reads
  a megahertz or two on a perfectly quiet day, and "absorbed below 1 MHz"
  is a line about nothing — there is no band down there to lose. Harmless
  while it was the only floor on screen, clutter now there are two.

## [0.6.0]

### Added
- **Cards → New from URL…** makes a card out of a web page from the board.
  `kind: url` has been in the manifest from the beginning and the only way
  to get one was to write the JSON by hand, which is a strange thing to ask
  of somebody who wants their cluster page in a tile. Give it a name and an
  address and it joins the Cards menu like any other card and goes onto the
  board. A bare `qrz.com` gets `https://`, the way an address bar does.
- **A card made that way can be deleted from the Cards menu**, with a
  **×** on its row that nothing else has, and **Undo** on the toast like
  everything else destructive here. Easy to make means they accumulate, so
  there has to be a way to take one off the list. A card you *wrote* is
  refused — by the menu and by the host — because there is nothing in a URL
  card but a name and an address and there is your code in yours.
- **A propagation layer centred on your station.** “Band to here (MUF)”
  on the path map colours the whole world by the highest frequency that
  will reach it *from you* — not the usual MUF map, which says nothing
  about where you are standing. It fades out where the nearest ionosonde
  is far from the path, so the oceans look like the guess they are
  rather than like a measurement.
- **What band will reach them.** Click a station on the path map and it
  now tells you the MUF for that path, the highest band that will carry
  it, and — this is the point — which ionosonde the answer rests on and
  how far that was from the path. Worked out from real soundings rather
  than a model, so when there is nothing measured near your path it says
  so instead of making a number up.
- **A satellites card.** Where the amateur satellites are now, which are
  above you, and when the next pass is — with the footprint and ground
  track of whichever you pick. Elements come from Celestrak a few times a
  day and are kept on disk; the orbital arithmetic runs in your browser,
  so it costs nothing afterwards and works with no network at all. Set
  your grid square in Settings for pass times.
- **Space weather along the top bar.** Solar flux, sunspot number, A and K
  indices and the current X-ray flare class, beside the clocks — the
  numbers you read before deciding what band to try. K and the flare class
  turn amber and then red when they start to matter, and stay plain when
  they do not. The narrower the window the fewer are shown, so the rail
  never grows a second row and pushes the board down.
- **The front page says what putting the board on a network means.** There
  is no login, so anyone who can reach the address can transmit with your
  radio — that was only written down in a page about audio cards, two
  clicks from where somebody would look. Now the repository is public it is
  on the README, with `SECURITY.md` saying plainly which parts are
  deliberate design and which are actually guarded.
- `CONTRIBUTING.md`, and issue forms that ask for the log, the platform and
  the rig backend — the three things every radio report turns on.
- **`python run.py --stop`**, and a way for hamdash to close itself
  properly. Running without the native window left no safe way to stop
  it: there is no window to close, and a forced kill cannot be caught, so
  the cards were left running and the radio was never closed down — which
  is the step that takes a transmitter off the air. It can now be asked
  to stop, and does it in the right order. The request is only accepted
  from the machine hamdash is running on, so a browser elsewhere in the
  house cannot shut your station down by loading a URL.
- **A Tidy up button**, in the Cards menu. Unticking a few cards leaves
  holes in the board, and it does not close them by itself on purpose — a
  layout that rearranged whenever something was removed would move cards
  you had placed deliberately. The cost was rows of nothing to scroll
  past, and closing them meant dragging every remaining tile. This does
  the lot in one press, pulling tiles straight up into the space above
  them and never sideways, so your left-to-right arrangement survives. It
  offers Undo, and says “Already tidy” rather than pretending to work.
- **Hear the radio in the browser, including from another machine.** A
  new **Receive audio** card plays the receiver's audio in a tile, which
  is what was missing for operating from another PC on the network — the
  rig control card already worked from anywhere the board was reachable.
  Measured against a real sound card: nothing dropped over a minute, 31 ms
  from the radio to the browser, 235 kbit/s. It uses ordinary polling, so
  there is no new dependency and nothing new is bundled into the build.
  Stop genuinely releases the sound card, so it can be handed to another
  program. **Note that the board still has no authentication** — see the
  warning in the docs before binding it to a network.
- `HD.cardRaw()` and `HD.cardPath()` for cards that serve something other
  than JSON. `HD.card()` parses every reply, so audio, images or files
  previously meant hand-rolling `fetch` — and hand-rolling the tile uid
  with it, which is easy to leave out and silently wrong.

### Changed
- **PSK Reporter is live now.** The card used to poll their query API every
  five minutes, which could time out and truncated a busy square at 2000
  reports — barely ten minutes of a good opening. It subscribes to their
  live feed instead, so reports arrive as they happen, there is no limit to
  run into and nothing to wait for. Reports still accumulate on disk, so
  the window keeps growing and a restart does not begin at nothing. The
  Refresh button is gone: there is nothing left to refresh.
- **Clicking a contest opens its rules.** The name is the link now. The
  calendar this comes from has no rules pages in it — the `website` field
  is empty on all 1000 rows and the site has no per-contest page — so for
  almost every contest the link is a web search, and it says so: a dotted
  underline and a tooltip, rather than a search dressed up as the
  organiser's own page. Where a contest does carry a real address the link
  goes straight there and the underline is solid. A `rules` link had been
  in the card since contests were added and had never once appeared,
  because that field has never had anything in it.
- **The contests pane credits QSO Dates.** Their export is CC0 “free to
  use with attribution”; only the docs carried it.

### Fixed
- **A `url` card's address is escaped into the page it serves.** It went
  into a `<meta http-equiv="refresh">` attribute raw, so a quote in it
  closed the attribute and the rest was markup on the board's own origin.
  It only ever came from a hand-written manifest before; it comes from a
  form now, which is what made it worth fixing rather than noting.
- **`javascript:`, `file:` and `data:` addresses are refused by name**,
  with what to use instead. A card page is served on the board's origin, so
  one of those in a tile is not a broken tile — it is code with the board's
  privileges, or a read of your disk.
- **The absorption (D-RAP) overlay is smooth now too**, the same way the
  aurora is. Its grid is coarser than the aurora's — four degrees of
  longitude — which is exactly where a field drawn as dots looked worst.
  The dayside absorption lobe reads as the shape it actually is.
- **The map pans east and west for ever now.** It used to stop after about
  a world and a half in either direction, because the world was drawn
  exactly three times and panning was fenced in to match. The copies
  follow where you are instead, so there is always land under you — and
  the overlays follow too, so the aurora and the greyline are there when
  you arrive. It costs no more than before at rest, and no more than
  before however far you drag.
- **The aurora overlay looks like an aurora now.** It was drawn as one dot
  per grid cell at a fixed three pixels, which could only line up at a
  single zoom — everywhere else it was stipple — with three hard colour
  steps that drew visible contour lines through it, and a cut-off that
  ended the oval square instead of letting it fade. It is one image per
  world copy now, built at the data's own resolution and smoothed by the
  browser, with colour and transparency both following the intensity. It
  is also about 30% less data and three map layers instead of 22,908.
- **A port already in use now says so.** Passing `--port` is how you pin
  the address a remote browser bookmarks, and picking one something else
  already has gave a traceback ending in “an attempt was made to access a
  socket in a way forbidden by its access permissions” — which names
  neither the port nor, on Windows, the actual problem. It is two lines
  now, before anything else starts.
- **Unhandled exceptions in threads never reached the log.** The handler
  that was supposed to record them read a field that does not exist, so
  it raised on every call and logged its own error instead of the one it
  was called about — since the day it was written. Most of this app is
  threads: the rig poller, the transmit watchdog, each card's output,
  the bus readers. A thread that dies takes its job with it while the
  app stays up looking healthy, which is exactly what that log line
  existed to catch.
- **`--host 0.0.0.0` half-worked.** It bound every interface as asked, so
  a browser elsewhere on the network could reach the board — but the same
  value was handed to every card process as the address to call the host
  back on, and to the native window as the page to open, and `0.0.0.0` is
  not an address anything can connect *to*. Cards came up unable to read
  their tile's settings or hear the bus, which looks like settings that
  silently do nothing rather than like a network fault. Binding a named
  address always worked and still does.
- **Every card got about fifteen times faster to talk to.** A card
  request was 34.5 ms at the median and 43.7 ms at the 95th percentile;
  it is 2.3 and 3.5 now. Four separate causes, each costing roughly a
  Windows timer tick: the card proxy built a fresh HTTP session for every
  call, it streamed replies that were small enough to just send, a failed
  tile-settings lookup was retried on every single request (and logged
  every time), and cards were served by Flask's development server rather
  than the one the app already ships. Every polled card is affected — the
  spectrum, the decoder, PSK Reporter, spots — and the 95th percentile is
  the half that showed, because a polled card turns jitter into stutter.

## [0.5.1]

### Added
- **A flag beside every spot.** The Where column now starts with the
  country's flag, worked out from the callsign the same way the log does it
  — never from what a source calls the country, since the three sources
  spell them differently and the cluster sends none at all.
- **Each spot source has its own mark.** POTA, SOTA, the cluster and the
  Reverse Beacon Network were four coloured dots, which is a legend you
  have to learn and a distinction that fails for anybody who cannot tell
  the colours apart. They are small drawings now — a tree, a summit, a
  speech bubble, a pair of radio waves — keeping the colour as well, and
  the same mark sits on the filter chip so the chip still reads as the
  legend for the column.

### Changed
- **The "not done yet" list is true again.** It still claimed ADIF import
  dedupe, the per-card settings UI and LoTW/QRZ upload were outstanding; all
  three have been done for a while, and a stale list of missing features is
  worse than none because it sends somebody off to build what already
  exists. There is one list now, in `CLAUDE.md`, and the README points at it
  rather than keeping a copy that drifts.

- **The README is a landing page again.** It had grown to 1071 lines — the
  entire manual on the front page, which is a scroll rather than something
  you read. The detail moved into `docs/`, indexed by `docs/README.md` and
  grouped the way you would look for it: using the board, radios, logging,
  the bundled cards, the bus, troubleshooting, building. Nothing was thrown
  away. A test keeps every link and anchor resolving and the front page
  short.

### Fixed
- **Skimmer spots said nothing about where the station was.** An RBN or
  cluster spot carries no park or summit, so its Where column was a flag on
  its own with the country hidden in a tooltip — 258 of 400 rows on a busy
  band. It shows the country as text when there is nothing more specific to
  say, which is the whole job of that column.
- **The Falklands and Cameroon have flags now**, so their prefixes are in
  the table rather than deliberately blank. They were waiting on a flag file
  rather than on knowing the answer.
- **The bundled flags ship with their licence.** 180 MIT-licensed SVGs were
  being redistributed without the notice that MIT asks travel with them.
  Leaflet and proj4 already kept theirs; the flags do now, and the README
  says what is bundled and under what.
- **Countries are recognised from their whole callsign block.** The prefix
  table held whichever prefixes somebody had needed along the way — Germany
  twelve of its eighteen letters, Sweden four of thirteen — so ordinary
  callsigns showed no country and no flag. 106 prefixes added across 24
  countries, every one reusing an entry already there. Measured against 45
  seconds of live spots: 1409 of 1409 sightings now resolve, where seven
  everyday callsigns had nothing before.
- **Prefixes the table could not place.** Found by measuring rather than
  reading: Japan is allocated 7J–7N and 8J–8N and only three were listed, so
  a board watching JA spots showed blanks; Brazil had PP–PY but not ZV–ZZ;
  Madeira's CS3 was missing, which is the NCDXF beacon that transmits every
  three minutes. Svalbard and Jan Mayen are there now too — they fly the
  Norwegian flag, so that is a right answer rather than a convenient one.
  99% of live spots carry a flag now, up from none.

## [0.5.0]

### Added
- **Documentation for writing your own cards.** `docs/cards.md` is the whole
  extension contract in one place: where a card lives, the manifest, the
  settings schema the host renders for you, what each permission unlocks, the
  python SDK, the browser API, and the handful of rules that are easy to get
  wrong. `docs/card-template/` is a card that runs as it stands — copy it,
  press **Cards → Rescan**, and it is on the board — and it carries a
  `CLAUDE.md` written for building cards with Claude Code.
  A test keeps the reference honest: every public SDK method, every `HD` key,
  every permission and every settings type has to appear in it.
- **`card.host.audio_devices()`.** Sound was the one shared service a card
  could not reach: the radio and the logbook had helpers and audio did not,
  so a card that wanted a device list re-derived it. The host's list is
  already cached and folded to one row per device, which is what matching a
  device *by name* needs.
- **The board can be arranged from the keyboard.** Focus a card's header
  and the arrow keys move it, shift and the arrows resize it. Dragging was
  the only way before, so anyone not using a pointer could add cards and
  never place them.
- **A card whose manifest the host could not use now says so.** Its status
  dot goes amber and names the problem — that has been computed and
  returned by the API all along, and shown to nobody, so a card with a bad
  `publishes` entry simply did not publish and never explained why.
- **`tools/get_hamlib.py` fetches Hamlib for you.** Hamlib is a native
  program rather than a Python package, so `pip install -r requirements.txt`
  cannot bring it. The script downloads the official 3 MB Windows build,
  verifies it against the SHA-256 Hamlib publishes beside it, and unpacks it
  where hamdash looks — after which a Hamlib radio in Settings just works.
  It is fetched rather than bundled because rigctld is GPL and hamdash is
  MIT; on macOS and Linux it prints the one-line package-manager command
  instead. The "rigctld was not found" error now names the script.
- **Dragging a card to the edge scrolls the board.** Moving a card from the
  bottom of a long dashboard to the top used to mean dropping it, scrolling,
  picking it up again, over and over. Hold it near the top or bottom and the
  board scrolls under it, faster the closer to the edge, with the card
  staying under the cursor. Dragging up now means "scroll" while there is
  still board above to reach, and only means "pop out" once there is not —
  so hauling a card to the top no longer throws it into its own window.
- **Docking a card as a tab now needs its target's tab strip.** The whole of
  a tile used to be a dock zone, so on a full board nearly anywhere you
  dropped a card made it somebody else's tab, and simply rearranging the
  board became the awkward case. Drop onto the strip where the tabs already
  live — the same gesture as dropping a browser tab on a tab bar — and that
  strip is what lights up. Everywhere else just moves.
- **The rig card shows both VFOs, split, and a meter.** VFO A and B side by
  side with the live one lit, click either to switch, A&#8646;B to exchange
  what they hold, and a Split button that marks which VFO is actually
  transmitting — the one setting that silently puts you on a different
  frequency from the one on the dial. The meter is the radio's: signal
  while receiving, power and SWR while transmitting.
  **What the card offers comes from the rig, not from the backend's name.**
  OmniRig has no meters at all, and whether a given OmniRig rig file has
  two VFOs is a property of that file, so it is asked rather than assumed
  and a control nobody can use is not drawn.
  flrig is asked for the S-units and the dBm rather than a scale being
  invented here — it has the radio's own meter table. Where a radio will
  not say what full power is (the FT-890 does not) the bar says how hard it
  is working and no number pretends to be watts.
  Verified on an FT-890 through flrig: both VFOs read, switching moves the
  dial and the band, split reports back through flrig, a swap exchanges A
  and B and leaves the live VFO alone, and the radio was returned exactly
  as found.
- **The spots card can watch the Reverse Beacon Network, and a DX cluster,
  over telnet.** Both are live connections rather than polls: one socket held
  open, spots arriving as they happen. The point of RBN is the one question
  nothing else here answered — *who can hear me* — so **Watch one callsign**
  defaults to `ME` and the list becomes every skimmer receiving you, with its
  signal report and the speed it read you at. Clear it for the whole feed
  (about five spots a second on CW). The telnet cluster is the same machinery
  pointed at a DXSpider or CC-Cluster node, with whatever setup commands that
  node wants.
  Both log in with the station callsign and refuse to connect without one —
  these are accounts on somebody else's public network — and nothing is ever
  sent but the login and the configured commands. A feed no tile has asked
  for in 90 seconds is disconnected, one connection is shared by every tile
  wanting the same feed, and what it keeps is bounded.
  Verified against the live service: the login prompt carries no newline, so
  it is answered out of the buffer rather than waited for as a line; a
  frequency can fill its column with no space after the spotter's colon
  (`DX de JI1HFJ-#:430051.30`), which whitespace splitting drops; and spots
  carry HHMM and nothing finer, so one just before midnight is dated
  yesterday rather than a day into the future.
- **A transmission nobody is holding is taken off the air.** Nothing else in
  the shack would: a card that keys the rig and then crashes, a tile closed
  mid transmission, a browser that dies — the transmitter just keeps going.
  The watchdog runs on the host's polling thread, which is still there when
  whatever keyed the rig is not. Two clocks: the card has to keep saying it
  still wants the transmission (it stops saying so within a few seconds of
  going away), and there is a ceiling on any one transmission —
  `radio.ptt_timeout`, two minutes by default, 0 for no ceiling. Closing
  hamdash while transmitting unkeys too.
  **Only transmissions hamdash started are ever ended.** A rig keyed from
  its own mic, a footswitch or another program is left alone; cutting
  somebody off mid-over because a dashboard thought it knew better is worse
  than the thing being guarded against. The card says why a transmission
  ended rather than it just stopping.

### Fixed
- **A crashing card's traceback was being eaten by the browser.** The tile
  has always shown a card's own output when it will not start, but it was not
  escaped — and a traceback is full of angle brackets, so `in <module>`
  was read as an unknown tag and silently disappeared, taking the most useful
  word on the line with it. Anything a card printed containing a script tag
  would have run. The page is escaped and themed now, says where the log is,
  and reloads itself while the host is still going to retry — so fixing the
  file brings the card back without touching the dashboard.
- **Dragging a card downwards stopped scrolling at the bottom of the
  board.** The board now makes room underneath a card being carried past
  the end of it.
- **A drag could only be finished, never called off.** Press **Escape** and
  the tile goes back where it started, docking nothing and saving nothing.
  Dropping is the step that is hard to undo, so the way out matters most.
- **Closing a card with ✕ now offers Undo**, like unticking it in the Cards
  menu already did. ✕ is the more destructive of the two — it takes that
  tile's own settings with it — so it was the wrong one to leave without a
  way back.
- **The frequency box only understood megahertz.** Typing `7041`, which is
  how most of the world writes 40m, asked the radio for 7041 MHz — not an
  error, just somewhere else entirely. `7.041`, `7041` and `7041000` now all
  mean the same place. Nothing is refused for being outside a ham band.
- **Outside the ham bands the band simply went blank**, which reads as the
  card failing rather than as an answer. It says *out of band* now, quietly,
  which is worth seeing before a contact is logged from there.
- **A radio that cannot measure transmit power made the meter disappear**
  the moment it went on the air, which looks like a fault. It says so
  instead.
- **Scrolling the dashboard no longer zooms a map you pass over.** A map
  card grabbed every scroll wheel event, so moving down a long board stopped
  dead the moment the pointer crossed one and the map zoomed instead. Plain
  scrolling now goes to the board; **pinch on a trackpad, or hold Ctrl and
  scroll, to zoom the map**, and a hint says so the first time. The zoom
  buttons are unchanged.
- **Every mode change failed on some Hamlib radios.** hamdash asked for the
  rig's default passband, which is what a 0 means in Hamlib's protocol, and
  some backends refuse it outright — an FT-900 and an FT-890 both do. It
  now falls back to "leave the passband alone" when no width was asked for,
  while a width you *did* ask for still reports its error.
- **The second VFO was dropped on Hamlib radios.** rigctld answers the
  query with bare values and hamdash was looking for labelled ones, so VFO B
  never appeared even though the number was in the reply.
- **A radio that cannot report PTT disarmed the transmit watchdog.** Some
  key perfectly and go on reporting "receiving" — an FT-900 through OmniRig
  does. The watchdog read that as "the transmission ended without us", so
  half a second after key-down it stopped watching: neither the keep-alive
  timeout nor the ceiling could fire, and a transmitter whose card had
  crashed would have stayed on the air with only the radio's own timeout
  behind it. A radio only gets to say it stopped if it ever said it
  started. Until a radio confirms a transmission, hamdash's own key-down is
  what the state reports, so the Transmit button shows on-air instead of
  offering to transmit on a radio that already is.
  Verified on the radio that broke it, dummy load on: it now comes off the
  air after the six-second hold. The same radio through Hamlib *does*
  report its transmission, so both halves of the rule were watched working
  on one rig — it confirms, hamdash stops second-guessing, and the watchdog
  still ends an unheld transmission.
- **A radio that cannot say which VFO it is on no longer has one guessed
  for it.** Plenty can be *told* which VFO to use and cannot read it back,
  and OmniRig still answers the question — with the last thing it was told.
  That echo was being drawn as fact, lighting the wrong VFO. The card now
  lights neither and says the radio does not report it.
- **A radio that reported "not split" was being shown as split.** The
  cross-VFO forms mean split on a radio whose CAT has no split flag, but
  they were overriding radios that have one and had plainly answered
  "off". Found on an FT-900 through OmniRig, where it also made a working
  `set_split(False)` look broken.
- **Popping a card back in left the tile showing "Bring it back" instead of
  the card.** The card *did* come back — it was mounted correctly and pushed
  out of sight underneath the "Running in its own window" panel, which the
  pop-in never removed. The panel is full height, so the tile looked like a
  pop-in that had failed, and its "Bring it back" button did nothing because
  the tile was already back. Restarting the app was the only way out. Found
  on the map card, but it affected every card.
- **Live spots arriving in the same burst came out oldest first.** The feeds
  carry HHMM and nothing finer, so a tie-break is needed, and it was stamping
  the wall clock — which ticks about every 15.6 ms on Windows, so a burst all
  got the same stamp and the sort fell back to insertion order. It counts now
  instead, with one counter shared by every feed so they also order against
  each other. Neither a live RBN session nor the local suite showed it; CI's
  Windows 3.11 runner did.
- **A log with less than one page in it drew an empty Prev/Next bar.** The
  pager is hidden in code, but an author `display: flex` beats the browser's
  own `[hidden]` rule outright, so it stayed on screen offering to page
  through nothing. Found by a new test rather than on screen: this is the
  fourth time the same CSS trap has been walked into, so it is now checked —
  anything hidden in code whose stylesheet gives it a `display` fails the
  suite with the one-line rule that fixes it.
- **The Transmit button could get out of step with the radio.** It kept
  whether it was transmitting in the page rather than reading it from the
  rig, so reloading the board while keyed brought the button back up saying
  **Transmit** while the radio was still on the air — and pressing it told an
  already-transmitting radio to transmit. The one control that takes a
  transmitter off the air was the one that would not. It follows
  `radio.state` now, so anything that keys the rig moves the button, and it
  blinks while on the air. Tested on an FT-890 into a dummy load: keyed,
  reloaded, and one press took it off.
- **Clicking a CW spot tuned 700 Hz away from the station.** A radio applies
  its CW offset when it *enters* CW, so setting the frequency and then the
  mode leaves the dial that offset from where the spot said — which reads as
  the spot being wrong rather than as hamdash tuning wrong. The spots card
  and the rig card's memory buttons set the mode first now. Measured on an
  FT-890 through flrig: frequency-then-mode landed on 7.037700 for a
  7.037000 spot, mode-then-frequency lands on 7.037000.
- **Normalising a layout was not idempotent.** The values matched but the keys
  came out in a different order on each pass, so every save rewrote
  `layout.json` with the lines shuffled — noise in the one file an operator
  might open or keep in git. Both normalisers now build a tile in a fixed
  order, and both sides assert it.

### Changed
- **The board's layout model is testable.** What a board *is* — normalising,
  docking, undocking, overlap, what reaches `layout.json` — moved out of
  `grid.js` into `hamdash/static/js/layout.js`, with no DOM in it, the same
  way `cw.py` is split out of the decoder card. `grid.js` keeps the pixels and
  the event handlers. `tests/js/` drives the model under `node --test`: no
  npm, no package.json, nothing to install.
  The two normalisers — the host's and the board's — now have a contract
  rather than an assumption. `tests/fixtures/layouts.json` is a set of
  awkward layouts, `layouts.expected.json` is what the host makes of them,
  and both sides must match it. They both rewrite `layout.json`, so a
  disagreement is a card coming back in the wrong box.

## [0.4.0]

### Added
- **Cards can be stacked.** Drag one tile onto another and it docks as a tab;
  the tile grows a strip and shows one card at a time. Drag a tab off the
  strip to pull it back out into a tile of its own. It is board machinery, not
  card machinery — nothing in any card knows about it, so a card somebody
  writes tomorrow stacks like the rest.
  **A background tab stays loaded and running**, so switching back is instant
  and nothing on screen is lost. Because a CSS-hidden iframe gets no
  `visibilitychange` of its own, the board tells it instead: `HD.visible`,
  `HD.hidden` and a `hamdash:visible` event. The spectrum and decoder cards
  use it to let go of the sound card while nobody is looking, and the spots
  card to stop asking four hobby servers for a list nobody is reading. A card
  that only draws can ignore it and behaves exactly as before.
  Popping out a tab takes it out of its stack and puts it back there when it
  returns. `layout.json` gains the shape to hold this, and old layouts upgrade
  on load — every tile keeps its uid, so per-tile settings come through
  untouched.
- **A spots card.** Who is on the air now, from four sources on one list —
  **Parks on the Air**, **Summits on the Air** and the **DX cluster** (via DX
  Summit) — with a second tab for **contests**: what is running right now
  with a countdown to the end of each, and what starts in the next fortnight.
  Filter by source, band, mode and free text over callsign, park, summit,
  spotter or comment; clicking a frequency tunes the radio.
  Filtering happens entirely in the browser over what was already fetched,
  because every one of these is somebody's hobby server: one fetch per source
  per minute whatever the tile does, the cache is on disk so restarting does
  not reset the floor, Refresh means "do not wait for the poll" rather than
  "ask again now", and a source that is down is a note beside the rows it
  last gave us rather than an empty card.
  Contests come from QSO Dates' bulk export, fetched every six hours: their
  keyed API allows twenty calls a day, which is not a dashboard, and the big
  contest calendar everyone links to forbids automated access outright.
- **flrig as a way into the radio.** A rig entry can be an flrig already
  running — host and port, nothing else, because flrig owns the rig type and
  the serial port exactly as OmniRig does. Worth having for the same reason:
  where flrig is running, going through it is the only way to talk to the
  radio at all.
  Two things about its XML-RPC interface are not what you would guess, and
  both were read off a running flrig rather than assumed. Frequencies come
  back as **strings**. Modes are the **radio's own names** with the filter
  width welded on — an FT-890 offers CW-W and CW-N, not CW — so the width is
  taken off for the log and put back on when setting, and a mode the radio
  does not have is refused with the list of what it does offer. It has to
  be: `rig.set_mode` with a name the radio lacks returns success and changes
  nothing.
- **Start and Stop on the decoder**, which releases the sound card rather
  than just going quiet — the usual reason to stop decoding is to hand the
  input to WSJT-X or fldigi, and waiting out a 30 second idle timer is not
  handing it over. The state is kept on the tile, so a card left stopped is
  still stopped tomorrow, and two tiles sharing one input are counted
  separately: stopping one leaves the other listening.
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
- **Two requests at once started a card twice.** A board loading a tile makes
  several at the same moment — the iframe, its settings, the card's own first
  call — and every one goes through `Supervisor.ensure`. Two threads passing
  the "is it running" check together each spawned a process: the second took
  the port the first had been told to use, the host kept one process handle
  and the other's port, and the tile sat on **Starting…** for ever while a
  stray process held the sound card or the serial port. It presents as a card
  that will not load, never as a race. Starting is now under a lock.
- **Every card stopped hearing the bus about a minute after it started.** The
  host ends each SSE stream after ~55 seconds on purpose and signs off with
  an empty `event: reconnect` frame; the SDK read a topic out of it and threw
  `KeyError`, which is not a `RequestException`, so it escaped the reader's
  loop and ended the thread. The card went on serving requests and looked
  perfectly healthy while receiving nothing ever again — including the
  `card.settings_changed` it uses to notice its own tile being edited.
  Nothing arriving on a socket can end that thread now.
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
