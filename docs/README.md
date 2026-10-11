# hamdash documentation

*[&larr; back to the project](../README.md)*

## Using it

| | |
|---|---|
| [Using the board](board.md) | moving cards, stacking them into tabs, popping one out into its own window, tile settings, themes |
| [Radios](radios.md) | flrig, OmniRig, Hamlib and rigctld; VFOs, split, the meter, and the rules the transmitter follows |
| [Logging and callsigns](logging.md) | the log, syncing with LoTW, QRZ and World Radio League, lookups, grids and flags |
| [The cards that come with it](built-in-cards.md) | spectrum and waterfall, the CW decoder, PSK Reporter, spots |
| [When something goes wrong](troubleshooting.md) | where to look first, and the failure modes worth knowing about |

## Extending it

| | |
|---|---|
| [Writing a card](cards.md) | the whole contract: where a card lives, the manifest, settings, permissions, the SDK on both sides |
| [card-template/](card-template) | a card that runs as it stands — copy it, press **Rescan**, and it is on the board |
| [card-template/CLAUDE.md](card-template/CLAUDE.md) | the same contract, written for building a card with Claude Code |
| [The bus](bus.md) | one publish/subscribe channel for the board, and who may say what |

## Working on hamdash itself

| | |
|---|---|
| [Building and testing](building.md) | the test suite, and making a desktop application out of the tree |
| [../CLAUDE.md](../CLAUDE.md) | the invariants and the failure modes that cost a day each — the recovery point for a new session |
