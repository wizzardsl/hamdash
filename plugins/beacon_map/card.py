"""Path-to-station card.

Accepts either a callsign (resolved through the host's lookup service) or a
raw Maidenhead locator, and returns distance, bearing and both endpoints.
"""
import re

from hamdash.sdk import Card

card = Card(__file__)
LOCATOR = re.compile(r"^[A-R]{2}[0-9]{2}([A-X]{2})?$", re.I)


@card.get("/path/<target>")
def path(target):
    target = target.strip().upper()
    station = card.host.station()
    mine = station.get("grid", "")

    if LOCATOR.match(target):
        try:
            return {"ok": True, "input": target, "kind": "locator",
                    "path": card.host.get("/api/grid/distance", to=target)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    info = card.host.lookup(target)
    if not info.get("locator"):
        return {"ok": False, "input": target,
                "error": f"no grid known for {target}",
                "country": info.get("country", "")}
    return {"ok": True, "input": target, "kind": "callsign", "info": info,
            "path": info.get("path") or card.host.get(
                "/api/grid/distance", to=info["locator"], **({"from": mine} if mine else {}))}


@card.on("lookup.result")
def follow_lookup(topic, payload):
    """When any card looks a station up, remember it as the last target."""
    if payload.get("locator"):
        card.publish("beacon_map.target", {"callsign": payload.get("callsign"),
                                           "locator": payload["locator"]})


if __name__ == "__main__":
    card.run()
