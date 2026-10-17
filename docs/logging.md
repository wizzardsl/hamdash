# Logging and callsigns

The log, syncing it with the services that hold a copy, and working out where a station is.

- [The log](#the-log)
- [Logbook sync](#logbook-sync)
- [Following one station](#following-one-station)
- [Filling in your grid](#filling-in-your-grid)
- [Flags](#flags)

*[&larr; back to the README](../README.md)*

---

## The log

With a radio under CAT control the frequency, band and mode follow the dial,
so the pane is always ready to log the contact you are having — the chip
beside **Log it** names the radio it is following. Type in one of those boxes
and it is yours for this contact; the chip turns into **Follow the radio** to
hand it back, and logging the contact hands all three back for the next one.
So does emptying the box.

**Band** is a list of every band rather than a text box, so overriding it is a
choice rather than a typo. A band you pick by hand sticks, with an accent
border to say so, until the frequency moves out of it; then the dial wins
again, because a band that disagrees with the frequency is a mistake about to
be logged.

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

## WSJT-X

Tick **Settings → Integrations → WSJT-X** and every contact you log in
WSJT-X lands in the log as you make it. In WSJT-X: **File → Settings →
Reporting**, and set *UDP Server* to the same address and port (its own
default, `127.0.0.1` and `2237`, is what hamdash starts with).

**It is on by default**, listening on WSJT-X's own default address and port,
so pointing WSJT-X at hamdash is the only step there is. It binds
**loopback** — reachable only from the computer hamdash runs on, offering
nothing to the network — which is what makes on-by-default the right call
here and not for anything that listens outward.

Contacts go through the same ADIF importer as everything else, so they dedupe
the same way: the same QSO arriving twice — WSJT-X resending, or you logging
it in two places — updates one row rather than making two.

**The status line is the point of the panel.** A stored "enabled" cannot tell
you whether it is working: the port can be open and perfectly correct while
WSJT-X points somewhere else, and from hamdash's side those look identical
until something arrives. So the line says which of three states you are in —
off, listening but nothing heard, or connected with a version and a count —
and names the fix for the two that are not "connected". WSJT-X sends a
heartbeat every 15 seconds, so it settles within the minute.

**If another program already has the port**, hamdash keeps trying rather than
giving up, and says what is in the way. JTAlert and GridTracker get closed
during a session, and needing to restart hamdash to pick that up would be the
wrong answer — close the other program and the listener takes the port within
a quarter of a minute. It refuses to *share* it, though, rather than taking
it. JTAlert and GridTracker commonly
hold 2237, and on Windows two sockets *can* share a UDP port — with the
datagrams going to one of them unpredictably, so half your contacts would
reach each program. Refusing is better than that. The way to run several at
once is WSJT-X's multicast support: point them all at an address such as
`239.255.0.1` and put the same one here.

Only the ADIF message is logged. WSJT-X sends two datagrams per contact — a
structured record and the finished ADIF — and the ADIF is the complete one.
A version too old to send it (before 2.0) would log nothing, so the status
line says that outright instead of leaving you with contacts that never
appear.

Contacts logged from anywhere — WSJT-X, an ADIF import, a QSL pull, or a
second logbook tile — appear in every open logbook without touching
anything. The card goes back to the first page when one arrives, because
that is where a new contact sorts; if you have a search running it is left
alone, so the answer on screen stays the answer to the question you asked.

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

**A push sends only what that service does not already have.** hamdash keeps
track, per contact, of which services hold it: a pull marks everything it
brought in or matched, and a push marks what it delivered. So a log you
pulled from QRZ onto a new install is not offered back to QRZ, and a contact
on QRZ is still pending for World Radio League until it goes there too. The
**Push (n)** count on each button is that number.

**World Radio League is asked first.** QRZ refuses a QSO it already has;
World Radio League stores a second copy, flags it as a duplicate and reports
success. So before sending, a WRL push reads what your WRL logbook holds and
leaves out anything that matches, using the same rule as a pull. Copies that
earlier pushes already stored there have to be deleted on WRL's own site —
hamdash does not delete anything from an online logbook. Until they are, a
WRL pull says how many records were extra copies, so its count matches the
QSOs in your log rather than the records on WRL.

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

## Following one station

**Scrolling over a map scrolls the board, not the map.** A dashboard is
something you scroll down, and a map that grabs the wheel stops it dead
under the cursor. To zoom a map: **pinch on a trackpad, hold Ctrl and
scroll, or use the + / - buttons**. A hint says so the first time you scroll
across one.


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

The built-in base map draws **country borders**, and **states and provinces**
for the nine countries Natural Earth publishes them for (Australia, Brazil,
Canada, China, India, Indonesia, Russia, South Africa, the United States).
Both are per-tile settings; borders are on, subdivisions off. Anywhere Natural
Earth has no internal lines simply has none — that is the data, not a failure
to load. Tile base maps bring their own.

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
- **Band to here (MUF)** — the same path calculation swept across the whole
  map: for every place, the highest frequency that will carry a path from
  *your* station to it. Not the familiar MUF chart, which plots MUF(3000) at
  each point and says nothing about where you are standing. It fades out where
  the nearest sounding is far from the path, so the oceans look like the guess
  they are.
- **Clouds** — OpenWeatherMap, and the only layer here that needs an account.
  Paste a key in the tile's settings; without one the layer is hidden rather
  than offered and broken.
- **Aurora** — NOAA SWPC's OVATION forecast. Public data, no account. It comes
  through the host, which caches it for five minutes and drops the cells at
  zero: the raw grid is about 1.5 MB of mostly nothing, and every open tile
  polling NOAA directly would be both rude and slow.

## The top of the window is measured; the bottom is not

Click a station and the card gives you a path readout. The two halves of it
are different kinds of claim, and it says which is which rather than running
them together.

**Path MUF** is a measurement. Ionosondes physically sound the F2 layer and
publish foF2 and M(3000)F2; the card takes the nearest live one to each place
the path has to bounce, and tells you which station answered and how far away
it was. The worst control point governs — a path is only as good as the worst
place it has to refract.

**D-layer loss** is a model, and labelled as one on screen. Nothing sounds the
D layer: it is too low and too collisional to be sounded the way F2 is, so
there is no network of measurements to read and a floor has to be computed.
The shape of the model is solid physics — absorption goes as 1/(f + f_H)²,
follows the solar zenith angle, scales with solar activity, and is paid once
per hop — but the constant is calibrated rather than derived. **Read the dB as
an ordering, not as a measurement.** What it is reliably good for is that 160m
is shut in daylight while 20m is barely touched, and that is worth knowing.

**Daylight floor** turns those dB into a frequency, and says what it assumed
when it does. That assumption is unavoidable: *absorption is a property of the
path, but a LUF is a property of your station.* Five watts of FT8, a hundred
of CW and a linear have three different answers over the same path at the same
second. So nothing here is called a LUF, the dB are shown so you can apply
your own judgement, and the line says how much loss it treated as too much.

**Absorbed below** is measured again — NOAA's D-RAP, which covers the
*flare-driven* absorption only. On a quiet day it is zero, and that does not
mean 160m is open to Europe at noon; the modelled line above is what covers
ordinary daytime absorption. It is only reported when it reaches the HF bands,
because D-RAP reads a megahertz or two on a perfectly undisturbed day and
"absorbed below 1 MHz" is a line about nothing.

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
