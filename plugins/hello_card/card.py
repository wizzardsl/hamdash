"""Template card, and the worked example of per-tile settings.

One process serves every hello_card tile on the board. Each request carries
the tile it came from, so two tiles can show two different greetings without
two processes.
"""
from hamdash.sdk import Card

card = Card(__file__)


@card.get("/state")
def state():
    uid = card.tile_uid()
    conf = card.settings(uid)               # cached, invalidated on edit
    out = {"uid": uid, "greeting": conf.get("greeting", "Hello"),
           "accent": conf.get("accent", "normal"), "freq": None}
    if conf.get("show_rig"):
        try:
            out["freq"] = card.host.radio().get("freq")
        except Exception as exc:            # noqa: BLE001
            out["error"] = str(exc)         # e.g. no 'radio' permission
    return out


@card.on("card.settings_changed")
def settings_edited(_topic, payload):
    if payload.get("card") == card.id:
        print(f"tile {payload.get('uid')} settings changed: {payload.get('values')}")


if __name__ == "__main__":
    card.run()
