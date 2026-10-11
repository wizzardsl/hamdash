# The bus

One publish/subscribe channel for the whole board, and the rules about who may say what.

- [The bus, and who may say what](#the-bus-and-who-may-say-what)

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
