# Radios

Four ways to reach a rig, what each one can do, and the rules the transmitter follows.

- [Radios](#radios)
- [The transmitter](#the-transmitter)

*[&larr; back to the README](../README.md)*

---

## Radios

Settings → Radio is a list of up to ten, and each one picks how hamdash
reaches it:

| backend | what you set | notes |
|---|---|---|
| **Simulated** | nothing | the board works with the shack powered down |
| **OmniRig** | which slot, 1 or 2 | Windows. Needs `pip install pywin32` |
| **flrig** | host and port | an flrig already running; default `127.0.0.1:12345` |
| **Hamlib** | model, port, baud, data/stop bits, parity, handshake | hamdash starts `rigctld` for you; run `tools/get_hamlib.py` once |
| **rigctld** | host and port | a daemon you are already running |

### Getting Hamlib

Hamlib is a program, not a Python package, so `pip install -r
requirements.txt` cannot bring it. One command does:

```bash
python tools/get_hamlib.py
```

It downloads the official Windows build (3 MB), checks it against the
SHA-256 that Hamlib publishes beside it, and unpacks it into your hamdash
config directory, where hamdash looks for it. Then add a **Hamlib** radio in
Settings → Radio, pick your model, and set the port and baud.

hamdash does not ship Hamlib: `rigctld` is GPL and hamdash is MIT, so you
download it from the Hamlib project rather than from us. On macOS and Linux
the script prints the one-line package command (`brew install hamlib`,
`apt install libhamlib-utils`) instead, since Hamlib publishes binaries for
Windows only.

### Two VFOs, split, and the meter

The rig card shows **VFO A and B side by side** with the live one lit. Click
either to switch to it; **A⇄B** exchanges what the two hold without changing
which is live. **Split** transmits on the other one and marks it `TX`,
because split is the one setting that silently puts you on a different
frequency from the one on the dial.

Below them is **the radio's meter**: signal strength while receiving, power
and SWR while transmitting. It is one bar because the radio has one needle.

What appears depends on what the radio can actually do, which hamdash asks
rather than assumes — OmniRig has no meters at all (it is CAT control, not a
receiver), and whether a particular OmniRig rig file has two VFOs is a
property of that file. A control nobody can use is not drawn.

Where a radio will not say what full power is — the FT-890 does not — the bar
shows how hard it is working and no number claims to be watts. S-units come
from flrig itself, which knows its radio's meter table, rather than from a
scale invented here.

Every entry also takes an **audio in** and **audio out** — the sound card that
radio is wired to, which on a modern rig is its own USB interface. Those sit
outside the backend fields on purpose: which sound card a radio uses has
nothing to do with how its CAT is reached, so switching an entry from Hamlib
to OmniRig keeps them. The **decoder** card is what reads it — set to
*Radio's audio in*, it follows whichever rig is live.

Devices are remembered **by name**, never by index, because PortAudio
renumbers everything whenever a device is plugged in or Windows changes its
default — a stored index would quietly become a different sound card. One
that is not present stays selected and is marked `(not connected)`, so a
config carried from another machine is visible rather than silently changed.

**Test** asks one radio one question without taking the one you are using away
from you. **Use this one** makes it live.

The rig card tunes with ▲ and ▼ beside the dial, or the arrow keys, by
whatever the step picker says — 1 Hz up to 1 MHz. A press adds or subtracts
the step rather than snapping to a grid, so up then down returns the dial
exactly where it was.

An **OmniRig** entry has no port or baud, on purpose: OmniRig's own dialog
owns those, and two programs cannot share a serial port — which is the reason
to use it in the first place. The button opens OmniRig's settings rather than
offering a second set of fields that would disagree with them.

**flrig** is the same bargain and has the same fields: a host and the XML port
from its *Config → Xcvr* tab, 12345 unless you changed it. flrig holds the
serial port, so where it is running this is how to reach the radio. One thing
to know: a radio's modes through flrig are its own names, filter width and
all — an FT-890 has CW-W and CW-N rather than CW. hamdash logs that as CW and
asks for CW-W when you pick CW, and if the radio has no mode resembling the
one you asked for it says so rather than appearing to work.

The **Hamlib** radio list comes from `rigctl -l`, so it is whatever your
Hamlib supports rather than a copy here that would go stale. Without Hamlib
installed the tab says so and takes a model number instead.

### More than one radio at a time

The radio marked *selected* is the one the whole board follows: the
logbook's frequency and mode, the spots card's tune, the decoder and the
spectrum. The plain **Rig control** tile follows it too.

Every radio can also have a **rig tile of its own**. Tick it in the
**Cards** menu, where each radio is listed under Rig control, or tick
**Own tile on the board** beside it in Settings → Radio - the two are the
same switch. That tile is named after the radio and controls only it,
whichever radio is selected: it tunes, changes mode, reads the meters and
keys that radio, and its Listen "rig" input is that radio's sound card.
Put each in an ear with Listen's Left / Right. Renaming the radio renames
its tile; removing the radio removes its tile. (Any rig tile's settings
also have a **Radio** choice, if you would rather re-point one you have.)

- A radio a tile names is opened beside the selected one and closed again
  two minutes after no tile is asking for it - never while hamdash has it
  on the air.
- Two entries that reach the radio the same way - one COM port, one
  OmniRig slot, one flrig or rigctld address - cannot both be open. The
  second says which radio already has it, and opens once that one closes.
- Every radio keeps its own transmit watchdog, below.

## The transmitter

hamdash can key your radio, so it also takes the transmission off the air
when nobody is holding it. A card keys the rig and keeps saying it still
wants to transmit; if it stops saying so — tile closed, card crashed, browser
gone — the host unkeys within a few seconds. There is also a ceiling on any
one transmission, `radio.ptt_timeout` in `config.json`, two minutes by
default; set it to 0 to remove the ceiling (the keep-alive still applies).
Closing hamdash while it is transmitting unkeys on the way out.

Only transmissions **hamdash started** are ever ended. If you key the rig
from its mic, a footswitch or another program, hamdash leaves it alone.

**It cannot save you from everything.** If the host is killed outright —
Task Manager, a power cut on the computer but not the radio — nothing in
software runs, and the rig stays keyed. Set your radio's own time-out timer;
that is the backstop, and it is the only one that works when the computer
has stopped being part of the story.
