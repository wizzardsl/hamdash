"""Logbook card.

Pre-fills a QSO from the current rig state plus a callsign lookup, so the
UI only has to collect what the operator actually types.
"""
from hamdash.sdk import Card

card = Card(__file__)


@card.get("/prefill/<call>")
def prefill(call):
    call = call.upper()
    try:
        info = card.host.lookup(call)
    except Exception as exc:
        info = {"ok": False, "error": str(exc), "callsign": call}
    try:
        rig = card.host.radio()
    except Exception:
        rig = {}
    station = {}
    try:
        station = card.host.station()
    except Exception:
        pass

    qso = {
        "call": call,
        "band": rig.get("band", ""),
        "freq": f"{(rig.get('freq') or 0) / 1e6:.6f}" if rig.get("freq") else "",
        "mode": rig.get("mode", ""),
        "gridsquare": info.get("locator", ""),
        "name": info.get("name", ""),
        "qth": info.get("qth", ""),
        "country": info.get("country", ""),
        "my_callsign": station.get("callsign", ""),
        "my_gridsquare": station.get("grid", ""),
    }
    return {"qso": qso, "info": info}


@card.post("/save")
def save():
    from flask import request
    body = request.get_json(silent=True) or {}
    try:
        row = card.host.log_qso(body)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 400
    card.publish("logbook.saved", {"call": row.get("call")})
    return {"ok": True, "qso": row}


if __name__ == "__main__":
    card.run()
