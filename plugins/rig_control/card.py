"""Rig Control card.

Thin card: the host owns the rigctld connection, so this backend mostly
exists to show the pattern (own API route + bus subscription). The UI talks
to the shared radio service directly.
"""
from hamdash.sdk import Card

card = Card(__file__)

MEMORIES = [
    ("FT8 20m", 14_074_000, "USB"),
    ("FT8 40m", 7_074_000, "USB"),
    ("SSB 20m DX", 14_195_000, "USB"),
    ("CW 40m", 7_030_000, "CW"),
    ("WSPR 20m", 14_095_600, "USB"),
    ("2m calling", 146_520_000, "FM"),
]


@card.get("/memories")
def memories():
    return {"memories": [{"label": l, "hz": hz, "mode": m}
                         for l, hz, m in MEMORIES]}


@card.on("log.qso_added")
def announce(topic, payload):
    # Example of reacting to another card's work.
    print(f"logged {payload.get('call')} on {payload.get('band')}")


if __name__ == "__main__":
    card.run()
