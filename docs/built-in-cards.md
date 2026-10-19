# The cards that come with it

What each bundled card does, and the decisions inside the ones that have a few.

- [Spectrum and waterfall](#spectrum-and-waterfall)
- [Decoding CW](#decoding-cw)
- [PSK Reporter](#psk-reporter)
- [Spots](#spots)

*[&larr; back to the README](../README.md)*

---

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

## Receive audio

Plays the radio's receive audio in the tile. On the same machine that is
a convenience; on another machine on your network it is the thing that
makes the board usable from the sofa, since the rig control card already
works from anywhere the board is reachable.

| setting | |
|---|---|
| **Audio input** | `rig` follows the audio input set on the active radio, which is usually what you want. `mock` is a test tone, so the card works with no radio attached. Otherwise part of an input's name. |
| **Quality** | Samples per second sent to the browser. A receiver's audio is about 3 kHz wide, so 8000 carries all of it at 128 kbit/s and 12000 leaves headroom at 192. |
| **Buffer** | How much audio to hold back against network jitter. 120 ms is comfortable on a LAN; raise it on patchy Wi-Fi if you hear dropouts. |

Audio cannot start without a click - every browser requires it - so the
tile waits on a **Listen** button. The readout beside it shows the delay,
the dropout count and the bandwidth, which is what tells you whether a
bigger buffer would help.

**Stop really does let go of the sound card**, immediately, because the
usual reason to press it is to give the input to another program. The
card also lets go when its tab is hidden, and after 20 seconds if a tile
disappears without saying so.

One input can only be open once. If a spectrum or decoder tile is already
using the radio's audio, this card will say so and name the likely
culprit rather than just failing.

### Listening from another machine

The board binds to `127.0.0.1` by default. `--host` changes that — give it
the address of the interface you want to be reachable on:

```bash
python run.py --host 192.168.1.50 --port 8765
```

`--host 0.0.0.0` listens on every interface, which is simpler but also
offers the board on any network the machine is attached to, including the
next coffee shop's. Naming one address is the better habit.

> **There is no authentication of any kind.** Anyone who can reach that
> address can key your transmitter, retune the radio, delete log entries
> and overwrite your configuration. On a network you do not fully
> control, put it behind a VPN such as Tailscale or an SSH tunnel rather
> than opening the port.

Transmitting from the browser is not supported. A microphone needs a
secure context, which means HTTPS with a certificate; receiving has no
such restriction.
## Decoding CW

The `decoder` card reads digital modes out of the radio's audio. CW works;
RTTY, PSK31 and FT8 are in the dropdown, disabled, so the card says what it
is rather than looking finished.

Pick the input at the top of the tile. **Radio's audio in** uses whatever
sound device the rig in use is set to in Settings → Radio, which is the one
worth choosing: it follows you when you switch radios. **Mock** synthesises
Morse, so the card works with no radio attached. Anything else is a sound
card by name.

**Stop** gives the sound card back, rather than only going quiet: the usual
reason to stop decoding is to let WSJT-X or fldigi have the input. The tile
remembers, so one left stopped is still stopped after a restart, and two
tiles listening to the same input are counted separately — stopping one
leaves the other running.

The lamp is lit while there is a signal to hear. Beside it are the speed and
the tone the decoder has settled on, and the signal-to-noise ratio it is
working with. **Clear** empties the window and leaves the speed estimate
alone — clearing is about the screen, not the signal. Text newly arrived is
shown in the accent colour for a few seconds so the live end is easy to find,
and the window stays pinned to the bottom unless you scroll up, in which case
it leaves you where you are reading.

What to expect of it: a clear signal is copied cleanly from about 6 to 45
words a minute, and it finds the tone and the speed by itself — there is
nothing to tune. A marginal signal comes out the way a human copies one, with
letters wrong. Below about 12 dB signal-to-noise it prints nothing at all,
which is deliberate: a decoder that fills the screen with letters taken out
of a dead band teaches you to ignore it. An unknown pattern is printed as the
dots and dashes that were heard, `<-.-.-.>`, because a silent drop looks
exactly like a quiet band.

## PSK Reporter

The `psk_reporter` card draws reception reports from
[pskreporter.info](https://pskreporter.info/): who heard whom, where, on what
band and mode, and how well. Filter by band, mode, signal, country, which side
of the report you were on, and how recently — all of it on the same map as the
path card, with the same layers and projections.

Type a **callsign** to watch one station, or a **grid square** (CN89, CN89kg)
for everything around it — or just **click the map**, which drops a pin on
that square and watches it. The box commits on Enter or when you click away.
**Reset filters** puts every filter back to everything and leaves the place
being watched alone.

**The dot colours are signal strength**, and the legend in the corner says so
with a live count per band of signal. They are not band colours, which is the
usual guess: a map is a form where any two marks can sit side by side, and
eight band hues measure far below the readable floor there (worst pair
ΔE 1.6 under deuteranopia) — a one-hue ramp manages only four steps that
clear both a light and a dark theme, which is coarser than 20m versus 17m.
The band and mode selectors isolate one exactly, which is the honest way to
ask that question. Their query API has no "whole world" endpoint; every
query is anchored on a callsign or a grid.

**Reports accumulate locally and are pulled incrementally.** Each reply
carries a sequence number; the card passes it back so the next pull asks only
for what has arrived since — their own mechanism, and what their developer
notes ask for. Reports go into a small SQLite file per watched target, pruned
to the 24 hours their API could refill, so the window deepens as the card runs
instead of truncating at whatever one reply can hold. **Every filter, the time
window included, is a query against that file or the page** and costs no
request at all.

**The five minute rule is enforced in the card.** PSK Reporter asks that data
be retrieved no more often than once every five minutes and reserves the right
to rate limit or block anyone who loads their system. So the card process
fetches once per query and serves everything else from memory; every filter
works on what is already in the browser and makes no request at all. Refresh
waits its turn like everything else, and the footer says how old the data is
and how long until the next request is allowed. The cache is written to disk,
so restarting hamdash does not spend a query either, and a rate limit or an
outage is remembered rather than retried into.

`appcontact` — the address PSK Reporter asks heavy users for so they can get
in touch rather than simply blocking — is a tile setting, empty by default.
Nothing is sent unless you fill it in.

## Satellites

Where the amateur satellites are now, which are above you, and when the
next pass is. The list puts whatever is up at the top, in teal, with its
elevation and azimuth; everything else is sorted by how soon it rises.
Click one — in the list or on the map — for its footprint, its ground
track for the next orbit, and the numbers you would point an antenna with.

| setting | |
|---|---|
| **Which satellites** | Celestrak's own groupings. `amateur` is the ham satellites and is what most operators want; `stations` is the ISS and friends. |
| **Lowest useful elevation** | Passes that never climb above this are not listed. Below about 10 degrees most stations are into the trees. |
| **Look ahead** | How far ahead to search for passes. |

Pass times need to know where you are, so set your grid square in
**Settings**. Without it the map still works and the pass list does not.

The orbital elements come from [Celestrak](https://celestrak.org) and are
fetched at most every six hours — they are published a few times a day and
go stale slowly — then kept on disk, so restarting hamdash is not a fresh
request. Through an outage the card keeps using the set it has and says
so. The orbital arithmetic (SGP4) runs in your browser, so it costs
nothing after the elements are loaded and works with no network at all.

## Spots

The `spots` card answers "who is on, and why is the band full" — and, if you
ask it to, "who can hear *me*". It puts six sources on one list:

| source | what it is | where from |
|---|---|---|
| **POTA** | Parks on the Air activations | `api.pota.app` |
| **SOTA** | Summits on the Air activations | `api2.sota.org.uk` |
| **Cluster** | human DX spots | [DX Summit](https://www.dxsummit.fi/) |
| **RBN** | what the CW/FT8 skimmers are hearing | `telnet.reversebeacon.net` |
| **Cluster (telnet)** | a live DXSpider or CC-Cluster node | whichever you point it at |
| **Contests** | what is running and what is next | [QSO Dates](https://www.qsodates.com/) |

The first three and the contests are polled over HTTP. The last two are live:
one TCP connection held open, spots arriving as they happen.

The chips along the filter bar are the legend as well as the switch — the
colour beside a row is the colour on its chip. Beside them: band, mode, and a
box that searches callsign, park, summit, spotter and comment at once.
**Type a frequency however you think of it** — `7.041`, `7041` and `7041000`
all mean the same place. Nothing is refused for sitting outside a ham band;
the band simply reads *out of band*, which is worth seeing before you log a
contact from there.

**Clicking a frequency tunes the radio** (and sets the mode when the spot
carries one). It is the frequency rather than the whole row on purpose, so a
mis-click cannot move the rig.

**A cluster spot's mode comes from its comment, and says so.** The DX cluster
has no mode field, so when the spotter's comment names one outright — "FT8
-14 dB", "CW 25 wpm", "LSB" — that becomes the mode, shown in *italics* with
a tooltip saying where it came from, and the mode filter uses it. A comment
naming two modes, or asking for one ("PSE SSB"), gives none, and a spot with
no mode is kept whatever mode you filter on. Clicking the frequency of such a
spot tunes but does not change the radio's mode: a comment is good enough to
filter on, not to switch the rig.

The **Contests** tab leads with what is on *now* and how long is left, because
a contest that started yesterday is exactly the one that explains the band and
exactly the one a list sorted by start time has already scrolled past.

All the filtering happens in the browser, over what was last fetched. Each
source is asked at most once a minute, contests every six hours, and the cache
is on disk so closing the app is not a way to ask again sooner. **Refresh**
means "don't wait for the next poll"; it cannot ask more often than the floor
allows. A source that is down keeps showing what it last gave, with its age.

### Who is hearing me

Tick **Reverse Beacon Network** in the tile's settings and the card logs in to
RBN with your station callsign and watches the skimmers. With **Watch one
callsign** left at its default — `ME` — the list is every automated receiver
that can hear *you* right now, with its signal report and the speed it read
you at. That is a question nothing else here answers: `psk_reporter` does it
for FT8 and friends, and RBN does it for CW and RTTY, from machines listening
all the time rather than from whoever happened to spot you.

So: call CQ, and watch where you are landing. An empty list is an answer too,
and the card says which it is — "nothing has heard you yet" with a count of
how many other stations' spots have come in, so a quiet list is never confused
with a dead feed.

Clear **Watch** and you get the whole feed, which on CW is about five spots a
second. The band, mode and search filters apply to it like anything else.

**Cluster (telnet)** is the same machinery pointed at a DXSpider or
CC-Cluster node — `dxc.ve7cc.net` and the like. Every node has its own
commands (`set/ft8`, `set/skimmer`); they go in **Commands to send after
login**, one per line.

Both of these log in with the callsign from Settings → Station, and the card
refuses to connect without one rather than inventing or omitting it — these
are accounts on somebody else's public network. Nothing is ever sent except
the login and the commands you configured: hamdash does not spot, announce or
talk on either network. A feed no tile is asking for is disconnected within a
minute and a half.
