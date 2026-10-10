"""The PC's sound devices, so a radio can be told which ones are its own.

A rig entry carries `audio_in` and `audio_out` alongside its CAT settings.
Nothing here opens a stream - this is the list the settings form offers and
the names a future digital-mode card (a CW or FT8 decoder, a waterfall tied
to one radio) will resolve when it wants the audio for the rig in use.

Two things shape the whole module:

* **A device is stored by name, never by index.** PortAudio indices shuffle
  whenever something is plugged in, a Bluetooth headset wakes up or Windows
  changes its default, so a saved index silently becomes a different device -
  which presents as "the decoder is deaf" rather than as a settings problem.
  `plugins/spectrum` already stores names for this reason; this is the same
  rule in the host.
* **Enumerating can block, and a waitress thread is scarce.** PortAudio on
  Windows can take seconds over `query_devices`, or hang on a driver that is
  misbehaving. Enumeration therefore happens on a worker thread that the
  request waits on with a timeout, and the answer is cached - a request that
  times out says so and leaves the worker to finish, rather than holding a
  server thread against a driver that may never return.
"""
from __future__ import annotations

import threading
import time

# PortAudio is not thread-safe across enumerate and open, and the spectrum
# card holds its own lock for the same reason inside its own process.
_LOCK = threading.Lock()

# How long an answer stands before it is asked for again. Devices do change
# while the app is running - that is the USB rig interface being plugged in -
# so this is short, and the settings form has a Rescan that ignores it.
CACHE_SECONDS = 30.0

# How long a request will wait for PortAudio before giving up on it.
ENUM_TIMEOUT = 6.0

_cache: dict = {}
_cached_at = 0.0
_worker: threading.Thread | None = None
_done = threading.Event()
# Bumped per probe, so a slow one that lands late cannot overwrite a
# newer answer with older news.
_generation = 0


# MME cannot return a device name longer than this, so it hands back a
# truncated one for exactly the device the other host APIs name in full.
MME_NAME_LIMIT = 31


def _label(name: str) -> str:
    """A device name fit for a dropdown.

    Windows puts newlines inside Bluetooth headset names - the raw string
    really does contain a CRLF - which turns one option into three lines of
    nonsense. The label is cleaned; `id` keeps the raw name, because that is
    what has to match when something opens the device later.
    """
    return " ".join(str(name).split())[:120]


def _fold_truncated(rows: dict[str, dict]) -> list[dict]:
    """Fold MME's truncated names into the full ones.

    "Microphone (4- USB Audio Device" and "Microphone (4- USB Audio Device)"
    are one sound card listed twice - MME cuts the name at 31 characters and
    DirectSound does not. Two rows that differ by a missing bracket is the
    operator being asked a question with no right answer, and whichever they
    pick the device is the same one.
    """
    names = sorted(rows, key=len, reverse=True)
    out: list[dict] = []
    for name in sorted(rows):
        if len(name) == MME_NAME_LIMIT:
            full = next((n for n in names
                         if len(n) > MME_NAME_LIMIT and n.startswith(name)),
                        "")
            if full:
                keep, drop = rows[full], rows[name]
                keep["channels"] = max(keep["channels"], drop["channels"])
                keep["default"] = keep["default"] or drop["default"]
                for api in drop["hostapis"]:
                    if api not in keep["hostapis"]:
                        keep["hostapis"].append(api)
                continue
        out.append(rows[name])
    # The default first: it is the one answer most shacks want.
    out.sort(key=lambda r: (not r["default"], r["name"].lower()))
    return out


def _probe() -> dict:
    """Ask PortAudio. Runs on the worker thread, never on a request."""
    try:
        import sounddevice as sd
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "inputs": [], "outputs": [],
                "error": f"sounddevice is not available ({exc}). "
                         "Install it with: pip install sounddevice"}
    try:
        with _LOCK:
            found = list(enumerate(sd.query_devices()))
            apis = {i: a["name"] for i, a in enumerate(sd.query_hostapis())}
            default_in, default_out = (list(sd.default.device) + [-1, -1])[:2]
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "inputs": [], "outputs": [], "error": str(exc)}

    # One entry per device, not per host API. Windows publishes the same
    # sound card under MME, WASAPI, DirectSound and WDM-KS, and since the
    # stored value is the name they would be several rows that select the
    # same thing - a choice the operator cannot act on. Which API gets used
    # is a latency question for whoever opens the stream, not for this form.
    def collect(side: str) -> list[dict]:
        key = f"max_{side}_channels"
        default = default_in if side == "input" else default_out
        rows: dict[str, dict] = {}
        for i, d in found:
            if d[key] < 1:
                continue
            row = rows.setdefault(d["name"], {
                "id": d["name"], "name": _label(d["name"]), "channels": 0,
                "hostapis": [], "default": False})
            row["channels"] = max(row["channels"], int(d[key]))
            api = apis.get(d["hostapi"], "")
            if api and api not in row["hostapis"]:
                row["hostapis"].append(api)
            if i == default:
                row["default"] = True
        return _fold_truncated(rows)

    return {"ok": True, "inputs": collect("input"),
            "outputs": collect("output"), "error": ""}


def _refresh_in_background() -> bool:
    """Start a probe unless one is already running. True if this call did.

    A probe that has not come back is wedged inside PortAudio holding
    `_LOCK`, so starting another would only queue a second thread behind it.
    """
    global _worker, _generation
    if _worker and _worker.is_alive():
        return False                # PortAudio would serialise them anyway
    _done.clear()
    _generation += 1
    mine = _generation

    def run():
        global _cache, _cached_at
        got = _probe()
        # A probe that timed out is still running and may land after a later
        # one has already answered. Older news must neither overwrite newer
        # nor release a caller that is waiting on the newer one.
        if mine != _generation:
            return
        if isinstance(got, dict):
            _cache, _cached_at = got, time.time()
        _done.set()

    _worker = threading.Thread(target=run, daemon=True, name="audio-devices")
    _worker.start()
    return True


def devices(refresh: bool = False) -> dict:
    """The sound devices, or an honest account of why there are none.

    Never raises and never blocks for longer than `ENUM_TIMEOUT`: a settings
    form that cannot list devices must still open.
    """
    if _cache and not refresh and (time.time() - _cached_at) < CACHE_SECONDS:
        return dict(_cache)

    started = _refresh_in_background()
    if not _done.wait(ENUM_TIMEOUT) or not _cache:
        if _cache:
            # Stale beats empty: these are device names, and they were right
            # a moment ago.
            return {**_cache, "stale": True}
        # "Try Rescan" is only honest the first time. Once a probe is wedged
        # in PortAudio no later one can start, so saying press it again
        # sends the operator at the wrong thing - it is a driver, and that is
        # what to say.
        return {"ok": False, "inputs": [], "outputs": [],
                "error": (f"the sound system did not answer in "
                          f"{ENUM_TIMEOUT:.0f}s. Try Rescan." if started else
                          "the sound system is still not answering. A sound "
                          "driver has stopped responding; it usually clears "
                          "when the device is unplugged.")}
    return dict(_cache)


def clean_device(raw) -> str:
    """A device name as it is stored: trimmed, bounded, never an index.

    An index here would be a saved setting that rots the next time something
    is plugged in, so a number is taken as a name and will simply not match.
    """
    return str(raw or "").strip()[:120]
