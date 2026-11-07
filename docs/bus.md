# The bus

One publish/subscribe channel for the whole board, and the rules about who may say what.

- [The bus, and who may say what](#the-bus-and-who-may-say-what)
- [Topics the built-in cards publish](#topics-the-built-in-cards-publish)

*[&larr; back to the README](../README.md)*

---

## The bus, and who may say what

Topics are dotted strings. The host publishes on reserved namespaces —
`card. config. log. lookup. radio. rig. ui.` — and **a card may publish only
`<card_id>.*`**. Anything else is refused with a message naming the fix:

```
'radio.state' is in the reserved radio.* namespace, which only the host
publishes. Publish my_card.* instead.
```

That matters because the board trusts the bus: a card that could publish
`radio.state` could make every tile believe the rig had moved.

Declaring `publishes` and `subscribes` in `card.json` narrows things further
and documents the card's data flow:

```json
"publishes":  ["my_card.tuned"],
"subscribes": ["radio.state", "log.qso_added"]
```

- **Undeclared means unrestricted.** No `publishes` and the card may use all
  of `<card_id>.*`; no `subscribes` and it receives everything. So adding
  either is opt-in and never silently breaks a card that predates it.
- **`publishes` catches typos.** Publishing `my_card.tunned` when the manifest
  says `my_card.tuned` fails loudly instead of leaving a subscriber waiting
  forever for an event that never comes.
- **Two things arrive regardless of `subscribes`:** `card.settings_changed`,
  which the SDK subscribes to on your behalf so `card.settings()` notices
  edits, and your own `<card_id>.*`, because two tiles of one card are
  separate pages served by one process and one tile must be able to hear the
  other.
- **A handler outside `subscribes` can never fire**, so the SDK says so at
  startup rather than letting you debug a card that runs but never reacts.

To reach another card, publish in your own namespace and have it subscribe;
there is no way to publish *into* someone else's.

**How far this goes:** the same caveat as [permissions](cards.md#honest-about-what-permissions-are). A card's server
calls carry its token and are checked properly. A card's *page* is identified
by `Referer`, which the page itself controls. Treat this as a namespace policy
that stops honest cards colliding, not as protection from a hostile one.

## Topics the built-in cards publish

Most of what crosses the bus is the host's (`radio.state`, `log.qso_added`,
`lookup.result`, ...). One built-in card publishes something meant for
others:

**`decoder.decoded`** - a slot's FT8, FT4 or WSPR decodes, once per slot
that decoded anything:

```json
{"mode": "FT8", "slot": "2026-11-03T07:28:15Z", "slot_ts": 1793690895.0,
 "dial_hz": 7074000, "rig": "d2bcaa58", "simulated": false,
 "source": "alsa_input.usb-...",
 "decodes": [{"text": "CQ VA7HDX CN89", "to": "CQ", "call": "VA7HDX",
              "grid": "CN89", "snr": -12, "dt": 0.1, "audio_hz": 1180.0,
              "rf_hz": 7075180, "kind": "standard"}]}
```

A WSPR decode carries the station's power and drift as well, and no `to`:

```json
{"text": "K1ABC FN42 37", "call": "K1ABC", "grid": "FN42", "dbm": 37,
 "drift": -1.2, "snr": -24, "dt": 0.3, "audio_hz": 1484.6,
 "rf_hz": 10140185, "kind": 1}
```

`kind` there is WSPR's message type: 1 a call, a grid and a power; 2 a
compound call and a power; 3 a six-character grid with the call as a hash,
whose `call` is empty until the full call has been heard.

**`decoder.picked`** - the operator clicked an FT8 or FT4 line, which is
saying "this station":

```json
{"call": "VA7HDX", "grid": "CN89", "snr": -12, "mode": "FT8",
 "slot": 1793690895.0, "rf_hz": 7075180}
```

The Decoder also looks the station up, so the host publishes
`lookup.result` and the map cards draw the path; the logbook fills its form
from the pick. The Spots card's **`spots.picked`** is the same idea for a
spot (its row as it appears in the list), and the logbook takes that too.

`call` is the station transmitting; `to`, `grid`, `report` and `ack`
(RRR, RR73, 73) are there when the message carries them. `dial_hz` and
`rf_hz` are null unless the audio is the radio's own and the radio sat on
one frequency for the whole slot. `simulated` is true for the mock input
or the simulated radio, and anything that sends decodes anywhere must skip
those. `osd` is present, with a distance, on a decode from the decoder's
less certain stage.
