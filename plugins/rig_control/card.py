"""Rig Control card.

Thin card: the host owns the rigctld connection, so this backend mostly
exists to show the pattern (own API route + bus subscription). The UI talks
to the shared radio service directly.
"""
from hamdash.sdk import Card

card = Card(__file__)

# The memory buttons are the tile's own, in its settings, and the page
# owns them (`memories.js`): nothing here needs to know about them.


@card.on("log.qso_added")
def announce(topic, payload):
    # Example of reacting to another card's work.
    print(f"logged {payload.get('call')} on {payload.get('band')}")


if __name__ == "__main__":
    card.run()
