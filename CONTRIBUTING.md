# Contributing

Thanks for looking. This is a hobby project for a hobby that is itself about
building things, so patches are welcome — including from people whose first
language is a soldering iron rather than Python.

## The quickest useful thing

**Write a card.** The whole point of the design is that you can add one
without touching the app. [docs/cards.md](docs/cards.md) is the contract and
[docs/card-template](docs/card-template) is a card that runs as it stands —
copy it into your cards folder, press **Rescan**, and it is on the board. A
card does not need to live in this repository to be useful to you.

## Running it

```bash
pip install -r requirements.txt
python run.py --home ./dev        # a throwaway config directory
python run.py --browser --home ./dev
```

The radio defaults to **mock mode**, so everything works with no hardware.
Cards that use audio default to a test tone for the same reason.

## Tests

```bash
python -m unittest discover -s tests
node --test 'tests/js/*.test.js'  # the board's layout model; Node 21+
```

Both run in CI on Linux, macOS and Windows, and on Python 3.11 and 3.13. CI
installs only what the host needs, so a test that imports `numpy` has to skip
when it is absent — see `tests/test_cw.py` for the pattern.

If you are fixing a bug, a test that **fails before your change and passes
after** is worth more than the fix. Several tests here exist because a
plausible-looking guard turned out to pass against the broken code too.

## Before changing something that looks odd

[CLAUDE.md](CLAUDE.md) is the long version of "why is it like that". It is the
invariants and the failure modes that each cost a day — one event stream per
page, cards as separate processes, mode before frequency, a sound device
stored by name and not by index. Most of the surprising code there is load
bearing, and the file says what breaks.

If your change makes something in that file untrue, **update it in the same
commit**. An invariant that is not written down has to be rediscovered by
breaking it.

Routine changes go in [CHANGELOG.md](CHANGELOG.md) under `## [Unreleased]`,
written when the change lands rather than at release time.

## House style

Terse commit messages that say *why*. Lead with the answer. Name failure modes
explicitly rather than discovering them in production, and prefer fixing a
cause over adding a retry. Errors shown to the operator should say what to do
about them, not only what failed.

## Radios

Every rig backend is proven against real hardware, but all of it is one
FT-890/900. **If something looks like a rig quirk, suspect it is this rig's
quirk** — reports from other radios are genuinely valuable, especially where
VFO, split, the meters or PTT behave differently.

Anything that can key a transmitter gets tested into a dummy load with the
operator present. Please do the same.

## Security

Please read [SECURITY.md](SECURITY.md) first — there is no authentication and
card pages are not isolated, both on purpose and both documented. A report
that one of those is *worse than described* is welcome; a report that they
exist is already known.
