# Picking this up on another machine

**Read `CLAUDE.md` first and treat it as the source of truth.** It is the
recovery point for the *code*: the invariants, the failure modes that cost a
day each, and the things that were deliberately **not** done. This file is
the other half — the state of the work and the things about *this machine*
that a clone cannot carry. Where the two ever disagree, CLAUDE.md wins.

Deliberately **not** repeated here: invariants, architecture, and the
to-do list. There is one list, `## Open threads` in CLAUDE.md, and a second
copy of it would go stale and send you to build something that already
exists.

## What it is, in a paragraph

hamdash is a card-based ham radio dashboard. A Python/Flask host runs in a
native webview window (pywebview); each card is an **independent OS process**
reverse-proxied under `/cards/<id>/`. Cards are processes on purpose, so one
that crashes or hangs takes only itself down. Primary platform is Windows 11 —
that is where every bug so far has been. MIT licensed, public repo at
`github.com/wizzardsl/hamdash`.

## State as of this handoff

- **v0.8.1**, tagged and released. `main` is clean and fully pushed.
- **815 tests pass** (`python -m unittest discover -s tests`). That is the
  baseline — if a fresh clone gives you fewer or any failures, something about
  the environment is wrong, not the code.
- Last four pieces of work, newest first:
  - `rigs`: nothing may end the rig polling thread (the PTT watchdog rides on
    it — the most consequential fix in the recent run)
  - `qsl`: a long push reports progress instead of three silent minutes
  - `rx_audio`: a lost stream no longer holds the sound card for the life of
    the card process
  - `psk_reporter`: filter lists carry their counts
- Before those, v0.8.0 added **the phone shell** (`hamdash/static/js/mobile.js`).

## Getting it running

```bash
pip install -r requirements.txt
python run.py                 # native window
python run.py --browser       # http://127.0.0.1:8765, no webview
python run.py --home ./dev    # throwaway config dir — use this on a new machine
python -m unittest discover -s tests
```

Python 3.13 here. `requirements.txt` covers it, including the card-only deps
(`numpy`, `sounddevice`) — those are listed because a frozen card runs inside
the hamdash binary, so a card's dependency is in every build.

**Start with `--home ./dev`** on a new machine. The real config dir has a
station, radios and credentials in it that will not exist there, and a scratch
home keeps you from writing into something you have not got.

**`node --test tests/js/` needs Node, which is NOT installed on this machine.**
CLAUDE.md documents a browser-harness workaround written for that fact — if
the new machine *has* Node, you can just run the JS tests directly and that
workaround is no longer needed. Check before assuming.

## What does not travel with the repo

Everything below lives in `%APPDATA%\hamdash\` (Windows) and is **not** in
git. On a new machine it is all absent or empty:

| | what it is | to recreate |
|---|---|---|
| `config.json` | station, radios, credentials | Settings sheet |
| `layout.json` | the board: tiles, stacks, per-tile settings | arrange it again |
| `logbook.sqlite3` | **87 QSOs** + 33 cached lookups | copy the file, or import ADIF |
| `cards/` | user-installed cards | empty here — nothing to move |
| `card-data/` | per-card caches (psk spots, satellite elements) | rebuilds itself |
| `hamlib/` | fetched by `tools/get_hamlib.py` | re-run it if you want Hamlib |
| `themes/` | custom themes | none beyond the bundled four |
| `hamdash.log` | diagnostics | n/a |

**Credentials must be re-entered by hand and are not in this document.**
Currently set here: the QRZ lookup password, the QRZ logbook API key, and the
World Radio League API key. LoTW is **not** configured (no account on this
machine), so `Lotw.push`/`pull` have never run against the live service — the
HTTP shapes come from ARRL's docs and the tests mock them.

Also stored: `qrz_pushed_id` and `wrl_pushed_id`, both 87 — i.e. the whole log
has been pushed to both. On a fresh config those reset to 0, so **a first push
from a copied logbook would re-send everything.** Push is dry-run by default,
which is the guard; respect it.

## This station, so you know what the code is talking to

- Callsign **VA7SLL**, grid **DO00HA**, units km.
- **Three rigs configured**, active one is `flrig` on `127.0.0.1:12345` —
  a real **Yaesu FT-890**. A mock rig and a `rigctld` entry are also defined.
  An **FT-900** on COM13 has been used for the OmniRig and Hamlib proving runs.
- Audio in/out on that rig is a **USB Audio Device** (`Microphone (4- USB Audio
  Device)` / `Speakers (4- USB Audio Device)`) running at 44100.
- **flrig 2.0.12** runs on this machine; **WSJT-X 3.0.2** broadcasts to
  `127.0.0.1:2237` and hamdash's listener is enabled by default.
- `radio.mock` is `true` in config, but the active rig entry is the real flrig
  backend — the mock flag is not what decides it.

## What you cannot verify on another machine

This is the important limitation, and it is large. Everything proven on
hardware here — and CLAUDE.md records a lot of it in detail — rests on
equipment that will not be present:

- **The radio backends.** flrig, OmniRig and Hamlib are each proven against a
  real FT-890/FT-900. On a machine without one you get the mock rig, which
  exercises the shape and none of the quirks. CLAUDE.md's radio notes are
  full of measured rig behaviour you must not "fix" from first principles.
- **PTT and the watchdog.** Proven into a dummy load with the operator
  present. **Do not key a transmitter without the operator's explicit
  go-ahead, present at the radio, with a dummy load.** That rule held through
  every session here and should hold on the next machine.
- **The audio cards.** `spectrum`, `decoder`, `rx_audio` all want a real
  sound card; they default to `mock` for exactly this reason. The sound-card
  bugs in CLAUDE.md were all found with real PortAudio on Windows.
- **Only one rig model has ever been tested.** Everything is one FT-890/900,
  so anything that looks like a rig quirk should be suspected of being *this
  rig's* quirk.

The DSP is the deliberate exception: `decoder/cw.py`, `rx_audio/dsp.py` and
`path_map/prop.js` import nothing from their cards and are driven by tests
with synthesised signals, so they are fully testable anywhere.

## How the work has been going

Worth matching, because it is why the tree is in the state it is:

- **Measure before concluding.** Most of the hard bugs here presented as
  something other than what they were. CLAUDE.md is largely a list of those.
- **Check that a regression test fails against the old code.** Every test
  added recently was run against the pre-fix file to see it named the fault.
  A test that passes both ways is not testing anything.
- **Synthetic events lie.** Two separate bugs were wrongly declared fixed
  because `dispatchEvent` bypassed the real problem. Drive the UI with real
  input, or read `elementFromPoint`.
- **Write the CHANGELOG entry when the fix lands**, not at release time, and
  keep one `### Added`/`### Fixed` per section — a repeated heading fails the
  release job, and a tag cannot be edited afterwards.
- Releasing is in CLAUDE.md under `## Releasing`: bump `__version__`, move the
  `## [Unreleased]` section under the new number, commit, tag, push the tag.

## Where to read next

- `CLAUDE.md` — invariants, failure modes, open threads. Start here.
- `README.md` — the landing page; `docs/README.md` indexes the rest.
- `docs/cards.md` — the card author's contract, generated-and-guarded.
- `docs/board.md` — the board UI, including the phone shell.
- `docs/troubleshooting.md` — when something will not start.
- `SECURITY.md` — the known-by-design holes, written down on purpose.

Note this file is not covered by `tests/test_docs_links.py`, which scans
`README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `SECURITY.md` and `docs/**` only.
Links here are not checked, so keep them few and obvious.
