"""Config, layout and the search paths for cards and themes.

Everything the user can change lives in one directory (HAMDASH_HOME, or the
platform config dir), so a shack PC can be backed up by copying one folder.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

PKG = Path(__file__).resolve().parent

# True when running from a PyInstaller build rather than a source checkout.
FROZEN = bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """Where the app's own files live: the repo root, or the unpacked bundle.

    PyInstaller sets sys._MEIPASS and rewrites __file__ to point inside it, so
    the plain relative paths would happen to keep working - but only while the
    spec lays the tree out exactly like the repo does. Deriving it from
    _MEIPASS says that out loud, so a spec change breaks here rather than as a
    card that mysteriously isn't found.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else PKG.parent


BUNDLED_PLUGINS = bundle_root() / "plugins"
BUNDLED_STATIC = bundle_root() / "hamdash" / "static"
BUNDLED_THEMES = BUNDLED_STATIC / "themes"

DEFAULTS = {
    "station": {"callsign": "", "grid": "", "name": "", "qth": ""},
    "theme": "night-watch",
    "grid_cols": 12,
    "row_px": 80,
    # One entry per radio, up to rigs.MAX_RIGS, with `active` naming the live
    # one. rigctld_host/port survive from the single-rig shape so an upgrade
    # keeps working; services.rigs.ensure_rigs turns them into an entry.
    "radio": {"rigctld_host": "127.0.0.1", "rigctld_port": 4532, "mock": True,
              "rigs": [], "active": "", "rigctld_path": "",
              # Seconds before a transmission hamdash started is taken off
              # the air. 0 removes the ceiling - the keep-alive still
              # applies, so a card that dies still unkeys the rig.
              "ptt_timeout": 120},
    "callsign_lookup": {"provider": "auto", "user": "", "password": "",
                        "account": "", "cache_days": 30},
    "logbook_sync": {"lotw_user": "", "lotw_password": "",
                     "qrz_api_key": "", "wrl_api_key": "",
                     "wrl_logbook_id": "", "tqsl_path": "",
                     "station_location": ""},
    # WSJT-X's UDP broadcast, on by default and listening on WSJT-X's own
    # default address and port, so that pointing WSJT-X at hamdash is the
    # only step. It binds **loopback**, so it is reachable only from this
    # machine and nothing is offered to the network - which is what makes
    # on-by-default the right call here and not elsewhere. If the port is
    # taken it retries rather than giving up, so closing JTAlert is
    # enough and restarting hamdash is not needed.
    "wsjtx": {"enabled": True, "bind": "127.0.0.1", "port": 2237},
    "units": "km",   # km | mi | nmi
}


# Values that must never be sent to a browser, and must never be overwritten
# by a save that did not mean to touch them. (section, key)
SECRETS = (
    ("callsign_lookup", "password"),
    ("logbook_sync", "lotw_password"),
    ("logbook_sync", "qrz_api_key"),
    ("logbook_sync", "wrl_api_key"),
)


# Fields that must never keep the whitespace a clipboard adds to them. A
# credential with a trailing newline is rejected by the service as simply
# "incorrect", which sends you looking at the password rather than at the
# paste. Includes the zero-width characters a rich-text copy carries.
CRUFT = "".join(chr(c) for c in (
    9, 10, 11, 12, 13, 32, 0xa0, 0x200b, 0x200c, 0x200d, 0xfeff))
CREDENTIALS = SECRETS + (
    ("callsign_lookup", "user"),
    ("logbook_sync", "lotw_user"),
)


def tidy_credential(value):
    """Strip surrounding cruft, leaving None and non-strings alone."""
    return value.strip(CRUFT) if isinstance(value, str) else value


def default_home() -> Path:
    env = os.environ.get("HAMDASH_HOME")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "hamdash"


def _merge(base: dict, over: dict) -> dict:
    """`over` on top of `base`, without either sharing anything.

    `dict(base)` is shallow, so a default that is a list or a dict - like
    `radio.rigs` - came back as *the same object* every time. Two Configs in
    one process then shared it, and appending a radio to one added it to the
    other and to DEFAULTS itself, where it outlived both.
    """
    out = json.loads(json.dumps(base))
    for k, v in (over or {}).items():
        out[k] = (_merge(out[k], v)
                  if isinstance(v, dict) and isinstance(out.get(k), dict)
                  else json.loads(json.dumps(v)))
    return out


# --------------------------------------------------------------- stacking
# A tile is a box on the grid holding one or more cards, shown one at a time
# behind a tab strip. A plain tile is the same thing with one card in it, so
# there is one shape in the file and one code path rather than two:
#
#   {"uid": "t1", "x": 0, "y": 0, "w": 5, "h": 4, "active": "t1",
#    "cards": [{"uid": "t1", "card": "logbook", "settings": {}},
#              {"uid": "a9f", "card": "spots",  "settings": {}}]}
#
# **A card keeps its uid wherever it goes.** Per-instance state hangs off it
# (invariant 5) - the spectrum's audio input, the decoder's source, a
# web_embed's URL - so docking a card into a stack or pulling it back out
# must not change it, or the operator's settings disappear at the exact
# moment they are rearranging the board.
#
# **A tile's own uid is the uid of the card it was born with**, which is what
# lets every existing layout upgrade without rewriting a single id: a flat
# tile becomes a stack of one and nothing keyed on that uid notices. The two
# meanings stay distinct afterwards - pull that first card out and the tile
# keeps the uid while the card takes its own elsewhere - so they are looked
# up by different methods: `tile()` finds a card, `stack()` finds its box.

# What each may carry. Anything else is dropped, so a hand-edited or older
# file cannot smuggle through a field that something later reads as geometry.
ENTRY_KEYS = ("uid", "card", "settings", "title", "url")
STACK_KEYS = ("uid", "x", "y", "w", "h", "active", "cards", "popped",
              "pop_w", "pop_h", "on_top", "from_stack")


def new_uid() -> str:
    return uuid.uuid4().hex[:7]


def _entry(raw) -> dict | None:
    """One card in a tile, or None if it is not one."""
    if not isinstance(raw, dict) or not raw.get("card"):
        return None
    kept = {k: raw[k] for k in ENTRY_KEYS if k in raw}
    out = {"uid": str(kept.get("uid") or "") or new_uid(),
           "card": str(kept["card"])}
    for key in ("title", "url"):
        if key in kept:
            out[key] = kept[key]
    out["settings"] = dict(kept.get("settings") or {})
    return out


def normalise_tile(raw) -> dict | None:
    """One tile from the file in the stack shape, or None if it is not one.

    Reads both the old flat tile and the new stack, because a layout written
    by an older hamdash is what every upgrade starts from.
    """
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("cards"), list):
        entries = [e for e in (_entry(c) for c in raw["cards"]) if e]
    else:
        one = _entry(raw)                       # the old flat shape
        entries = [one] if one else []
    if not entries:
        return None

    kept = {k: raw[k] for k in STACK_KEYS if k in raw and k != "cards"}

    def whole(key, fallback):
        try:
            return int(kept.get(key, fallback))
        except (TypeError, ValueError):
            return fallback

    active = kept.get("active")
    # An `active` naming a card that is not here is worse than none at all:
    # the board draws an empty box under a working tab strip.
    if active not in [e["uid"] for e in entries]:
        active = entries[0]["uid"]

    # Built in a fixed order, not by copying whatever order the file had.
    # Normalising is not idempotent otherwise - the values match but the keys
    # come out shuffled - so every save rewrites layout.json with the lines
    # moved around, and the one file an operator might read by hand or keep
    # in git churns for no reason.
    out = {"uid": str(kept.get("uid") or "") or entries[0]["uid"],
           "x": whole("x", 0), "y": whole("y", 0),
           "w": whole("w", 4), "h": whole("h", 3),
           "active": active, "popped": bool(kept.get("popped"))}
    for key in ("pop_w", "pop_h", "on_top", "from_stack"):
        if key in kept:
            out[key] = kept[key]
    out["cards"] = entries          # last: it is the long one to read
    return out


# What a retired Listen tile chose, and the rig card now asks for.
LISTEN_SETTINGS = ("device", "rate", "buffer_ms")


def listen_moves_to_rig(tiles) -> list[dict]:
    """Carry a Listen tile's input, quality and buffer to the rig tiles.

    Listening moved into the rig card and the Listen card was retired, so
    its tiles are taken off the board - and with them whatever input the
    operator had picked, which would then quietly become the default.
    Copied from the first Listen tile found, into each rig tile that has
    not chosen its own; nothing a rig tile already set is overwritten.
    """
    tiles = normalise_layout(tiles)
    entries = [e for t in tiles for e in t["cards"]]
    listen = next((e for e in entries if e.get("card") == "rx_audio"), None)
    if not listen:
        return tiles
    chosen = {k: v for k, v in (listen.get("settings") or {}).items()
              if k in LISTEN_SETTINGS}
    for e in entries:
        if e.get("card") == "rig_control" and chosen:
            mine = dict(e.get("settings") or {})
            for k, v in chosen.items():
                mine.setdefault(k, v)
            e["settings"] = mine
    return tiles


def without_cards(tiles, card_ids) -> list[dict]:
    """The board with every entry of these cards taken off it.

    For a card that has been retired - `hidden` in its manifest - so a
    layout that still holds one does not keep a tile for something the
    board no longer offers. A stack keeps its other cards; one left empty
    goes, and `normalise_layout` re-points `active` if it was the one
    removed. Deliberately *not* part of normalising: that runs in the
    board as well as here (invariant 5a), and the board does not know
    which cards are retired.
    """
    drop = set(card_ids or ())
    kept = []
    for tile in normalise_layout(tiles):
        tile["cards"] = [e for e in tile["cards"] if e.get("card") not in drop]
        if tile["cards"]:
            kept.append(tile)
    return normalise_layout(kept)


def normalise_layout(tiles) -> list[dict]:
    """The whole board, in the stack shape, with every uid distinct.

    Two cards sharing a uid would share their settings and their identity
    through the proxy, which shows up as one tile changing when you edit a
    different one.
    """
    out, seen = [], set()
    for raw in tiles if isinstance(tiles, list) else []:
        tile = normalise_tile(raw)
        if not tile:
            continue
        for entry in tile["cards"]:
            if entry["uid"] in seen:
                entry["uid"] = new_uid()
            seen.add(entry["uid"])
        if tile["active"] not in [e["uid"] for e in tile["cards"]]:
            tile["active"] = tile["cards"][0]["uid"]
        out.append(tile)
    return out


class Config:
    def __init__(self, home: Path | str | None = None) -> None:
        # Absolute, always. A card process runs with its own working
        # directory, so a relative --home would put its files somewhere
        # else entirely - which is where the PSK cache first landed.
        self.home = (Path(home).expanduser().resolve() if home
                     else default_home())
        self.path = self.home / "config.json"
        self.layout_path = self.home / "layout.json"
        self.db_path = self.home / "logbook.sqlite3"
        self.user_plugins = self.home / "cards"
        self.user_themes = self.home / "themes"
        for d in (self.home, self.user_plugins, self.user_themes):
            d.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    # ------------------------------------------------------------ search paths
    @property
    def plugin_dirs(self) -> list[Path]:
        """Bundled cards first, user cards second: a user copy of a bundled
        card id wins, which is how you fork one and keep the same tile."""
        return [BUNDLED_PLUGINS, self.user_plugins]

    @property
    def theme_dirs(self) -> list[Path]:
        return [BUNDLED_THEMES, self.user_themes]

    # ------------------------------------------------------------------- state
    def _load(self) -> dict:
        if self.path.exists():
            try:
                return _merge(DEFAULTS, json.loads(self.path.read_text()))
            except json.JSONDecodeError as exc:
                print(f"{self.path}: bad JSON ({exc}); using defaults")
        return json.loads(json.dumps(DEFAULTS))

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2))

    def load_layout(self) -> list[dict]:
        if self.layout_path.exists():
            try:
                tiles = json.loads(self.layout_path.read_text())
            except json.JSONDecodeError:
                print(f"{self.layout_path}: bad JSON; starting with a blank board")
                return []
            # Valid JSON of the wrong shape used to get this far and then fail
            # somewhere unrelated - a hand-edited {"tiles": [...]} produced a
            # 500 on /api/tiles/<uid>/settings reading 'str' has no attribute
            # 'get'. Say what is wrong where it is wrong.
            if isinstance(tiles, dict) and isinstance(tiles.get("tiles"), list):
                tiles = tiles["tiles"]          # a forgivable hand-edit
            if not isinstance(tiles, list) or not all(
                    isinstance(t, dict) for t in tiles):
                print(f"{self.layout_path}: expected a list of tiles, got "
                      f"{type(tiles).__name__}; starting with a blank board")
                return []
            # Every read goes through this, so an old flat layout is a stack
            # of one from the first moment anything looks at it and nothing
            # downstream has to know there were ever two shapes.
            return normalise_layout(tiles)
        return normalise_layout([  # first run: the cards most people want
            {"uid": "t1", "card": "rig_control", "x": 0, "y": 0, "w": 4, "h": 3},
            {"uid": "t2", "card": "logbook", "x": 4, "y": 0, "w": 5, "h": 5},
            {"uid": "t3", "card": "beacon_map", "x": 9, "y": 0, "w": 3, "h": 4},
        ])

    def save_layout(self, tiles: list[dict]) -> None:
        # Normalised on the way in as well as the way out: a PUT is the
        # board talking, but it is also the one place a bad layout could be
        # written and then fail on the next load with the app already shut.
        self.layout_path.write_text(
            json.dumps(normalise_layout(tiles), indent=2))

    # ------------------------------------------------------------- lookups
    def entries(self) -> list[dict]:
        """Every card on the board, whatever tile it sits in."""
        return [e for t in self.load_layout() for e in t["cards"]]

    # ------------------------------------------------------- tile settings
    # Settings live on the tile, not in config.json: two web_embed tiles are
    # two different pages, and settings that travel with the tile survive
    # dragging, popping out and being removed.
    def tile(self, uid: str) -> dict | None:
        """The *card* with this uid, wherever it is sitting.

        Named `tile` because that is what a uid has always meant to a card:
        one card process serves every tile of its kind and this is the one
        asking. A card in a stack is still a tile to the card.
        """
        return next((e for e in self.entries() if e["uid"] == uid), None)

    def stack(self, uid: str) -> dict | None:
        """The box a card sits in - its geometry, its tabs, its siblings."""
        return next((t for t in self.load_layout()
                     if any(e["uid"] == uid for e in t["cards"])), None)

    def tile_settings(self, uid: str) -> dict:
        return dict((self.tile(uid) or {}).get("settings") or {})

    def set_tile_settings(self, uid: str, values: dict) -> dict:
        tiles = self.load_layout()
        for t in tiles:
            for entry in t["cards"]:
                if entry["uid"] == uid:
                    entry["settings"] = {**(entry.get("settings") or {}),
                                         **(values or {})}
                    self.save_layout(tiles)
                    return entry["settings"]
        raise KeyError(f"no tile {uid} on the board")

    def active_card_ids(self) -> set[str]:
        """Which cards need a process. A card parked on a background tab is
        still on the board, so it still counts - reaping it would stop the
        process behind a tab the operator is one click away from."""
        return {e["card"] for e in self.entries() if e.get("card")}
