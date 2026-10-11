# hamdash

A card-based ham radio dashboard. Runs as a desktop app on Windows, macOS and
Linux: no server to configure, no localhost URL to remember, nothing installed
system-wide. Cards can be a URL in a tile or a full Python program — same board,
same event bus.

**Want your own card on the board?** [docs/cards.md](docs/cards.md) is the
whole contract, and [docs/card-template](docs/card-template) is a card that
runs as it stands — copy it into your cards folder, press **Rescan**, and it
is there. It carries a `CLAUDE.md` if you would like Claude Code to write it
with you.

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
hardware. For a real rig, add it in Settings → Radio — hamdash speaks
**flrig**, **OmniRig**, **Hamlib** and a **rigctld** you are already running,
and starts Hamlib's daemon for you.

Hamlib is a program rather than a Python package, so `pip` cannot bring it:

```bash
python tools/get_hamlib.py     # downloads the official build, checksum-verified
```

Or point hamdash at a `rigctld` you run yourself:

```bash
rigctld -m 1035 -r /dev/ttyUSB0 -s 38400    # -m 1 is the Hamlib dummy rig
```

```json
// config.json
{ "station": { "callsign": "VE7ABC", "grid": "DO10" },
  "radio": { "mock": false, "rigctld_host": "127.0.0.1", "rigctld_port": 4532 } }
```

## What is on the board

Ten cards ship with it, and a tile can hold any of them:

- **Rig control** — the dial, mode, both VFOs, split, and a meter that shows
  signal while receiving and power while transmitting.
- **Logbook** — log a contact, search it, import and export ADIF, and sync
  with LoTW, QRZ or World Radio League.
- **Spots** — POTA, SOTA, the DX cluster and contests on one list, plus live
  telnet feeds from the Reverse Beacon Network, which answers the question
  nothing else here does: *who can hear me*.
- **PSK Reporter** — who is receiving whom, on a map.
- **Path to station** — great-circle path, greyline, aurora and space weather.
- **Beacon map** — bearing and distance to the station you are looking at.
- **Spectrum** — a waterfall from the radio's sound card.
- **Decoder** — live CW off the air, with the DSP tested against synthesised
  Morse from 6 to 45 WPM.
- **Web embed** — any page in a tile.
- **Hello card** — the example, in about thirty lines.

Everything is usable with no radio attached: the rig is in mock mode until
you tell it otherwise.

## Documentation

| | |
|---|---|
| **[Using the board](docs/board.md)** | moving cards, stacking them into tabs, popping one out, tile settings, themes |
| **[Radios](docs/radios.md)** | flrig, OmniRig, Hamlib and rigctld; VFOs, split, the meter, and the transmitter rules |
| **[Logging and callsigns](docs/logging.md)** | the log, syncing it with LoTW, QRZ and WRL, lookups, grids and flags |
| **[The cards that come with it](docs/built-in-cards.md)** | spectrum, CW decoder, PSK Reporter, spots |
| **[Writing a card](docs/cards.md)** | the whole extension contract, with a [template](docs/card-template) you can copy |
| **[The bus](docs/bus.md)** | one pub/sub channel for the board, and who may say what |
| **[When something goes wrong](docs/troubleshooting.md)** | where to look, and the failure modes worth knowing |
| **[Building and testing](docs/building.md)** | the test suite, and making a desktop app out of the tree |

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
plugins/     rig_control, logbook, beacon_map, spectrum, path_map, decoder,
             psk_reporter, web_embed, hello_card
```

Why processes and not imports: a card someone wrote at 1am shouldn't be able to
hang the UI, crash the host, or drag a conflicting dependency into it. The host
reverse-proxies `/cards/<id>/*` to the card's own loopback port, so cards are
same-origin to the browser and their ports are never exposed.

## Not done yet

`cty.dat` for proper DXCC instead of the short prefix table; freeing a tile's
grid slot while its card is popped out; remembering where popped-out windows
were across a restart; RTTY and PSK31 in the decoder; multiple boards as tabs.

On packaging: Linux builds (CI builds Windows and macOS, which both have a
pip-installable pywebview backend — Linux needs system GTK/WebKit packages
and a build without them would silently be browser-only), code signing, and
an application icon.

The full list, with the reasoning and what was measured, is under
[Open threads in CLAUDE.md](CLAUDE.md#open-threads) — kept there rather than
here so there is one list to keep true rather than two that drift. This
paragraph used to claim ADIF import dedupe, the per-card settings UI and
LoTW/QRZ upload were outstanding; all three had been done for a while.

## A word about installing cards

A card is a program on your machine, and a card's *page* is not sandboxed
from the rest of the dashboard: every card shares one origin with the board,
so a card that wanted to could read your logbook or use a shared service it
never declared. Measured, not assumed — the detail is in
[docs/cards.md](docs/cards.md#honest-about-what-permissions-are).

A card's *server* calls are properly enforced and cannot reach an undeclared
service. But `permissions` in a manifest is a declaration of intent, not a
sandbox. **Install cards you trust, and read them first.**

## Licence

MIT — see [LICENSE](LICENSE). Hamlib, which the Hamlib backend runs as a
separate program, is GPL and is **not** bundled: `tools/get_hamlib.py`
fetches it from the Hamlib project on request.
