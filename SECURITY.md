# Security

hamdash drives a transmitter. That is unusual for a hobby web app and worth
being plain about, so this page says what the design actually is before it
says how to report something.

## Reporting something

Use GitHub's **[Report a vulnerability](https://github.com/wizzardsl/hamdash/security/advisories/new)**
button, which opens a private advisory only the maintainers see. If it is not
sensitive, an ordinary [issue](https://github.com/wizzardsl/hamdash/issues) is
fine and usually faster.

This is a hobby project maintained by one operator. There is no guaranteed
response time and no bounty. Please do not test against anyone else's station.

## Known by design — please do not report these as vulnerabilities

All three are deliberate, measured and documented. A report of one is not news,
but a report that one is *worse than described* very much is.

**There is no authentication, anywhere.** No accounts, no password, no token.
With `--host` set, anyone who can reach the address can tune the radio,
transmit with it, delete log entries and read or overwrite the configuration,
including stored service passwords. The default is `127.0.0.1`, which is
reachable only from the machine it runs on. Keeping it simple for a
single-operator station was chosen over a login; the honest mitigations are a
network you trust or a VPN such as Tailscale.

**Card pages are not isolated from each other.** Every card's page shares one
browser origin with the board and with every other card, so a card page can
reach `parent.document`, find another card's frame and make requests from it.
Measured, not assumed: a card declaring no permissions got `403` asking as
itself and `200` — with the radio's frequency — asking from the rig card's
window. `permissions` in a card manifest is a declaration of intent that the
host enforces for a card's *server* calls, where it is real, and matches on
`Referer` for its *page* calls, where it is a speed bump. It is not a sandbox.

**A card is a program on your machine.** Installing one is as consequential as
installing any other program. Read cards before you install them.

## What is actually guarded

- A card's server-side calls carry a token and genuinely cannot reach a
  service the card did not declare.
- The bus namespace is enforced: a card may publish under its own id and
  nowhere else, and cannot label its events as coming from the host.
- Stored passwords are never sent to the browser, and never travel on the
  event bus — `config.changed` is redacted the same way the endpoint is.
- Stopping the app (`POST /api/shutdown`) is accepted only from the machine
  hamdash is running on, so a browser elsewhere cannot shut a station down.
- A transmission nobody is holding is taken off the air by a watchdog, and a
  clean exit unkeys the radio.

## Transmitting

You are the licensed operator and the control operator. hamdash will key your
radio when something asks it to, and the safeguards above are conveniences,
not a substitute for your own time-out timer and your own attention. A forced
kill of the process cannot be caught by any program, so the radio's own
time-out is the real backstop.

## Versions

Fixes go to `main` and into the next release. There are no long-term support
branches.
