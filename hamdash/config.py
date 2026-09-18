"""Config, layout and the search paths for cards and themes.

Everything the user can change lives in one directory (HAMDASH_HOME, or the
platform config dir), so a shack PC can be backed up by copying one folder.
"""
from __future__ import annotations

import json
import os
import sys
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
    "radio": {"rigctld_host": "127.0.0.1", "rigctld_port": 4532, "mock": True},
    "callsign_lookup": {"provider": "auto", "user": "", "password": "",
                        "cache_days": 30},
    "units": "km",
}


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
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


class Config:
    def __init__(self, home: Path | str | None = None) -> None:
        self.home = Path(home).expanduser() if home else default_home()
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
            return tiles
        return [  # first run: the three cards most people want
            {"uid": "t1", "card": "rig_control", "x": 0, "y": 0, "w": 4, "h": 3},
            {"uid": "t2", "card": "logbook", "x": 4, "y": 0, "w": 5, "h": 5},
            {"uid": "t3", "card": "beacon_map", "x": 9, "y": 0, "w": 3, "h": 4},
        ]

    def save_layout(self, tiles: list[dict]) -> None:
        self.layout_path.write_text(json.dumps(tiles, indent=2))

    # ------------------------------------------------------- tile settings
    # Settings live on the tile, not in config.json: two web_embed tiles are
    # two different pages, and settings that travel with the tile survive
    # dragging, popping out and being removed.
    def tile(self, uid: str) -> dict | None:
        return next((t for t in self.load_layout() if t.get("uid") == uid), None)

    def tile_settings(self, uid: str) -> dict:
        return dict((self.tile(uid) or {}).get("settings") or {})

    def set_tile_settings(self, uid: str, values: dict) -> dict:
        tiles = self.load_layout()
        for t in tiles:
            if t.get("uid") == uid:
                t["settings"] = {**(t.get("settings") or {}), **(values or {})}
                self.save_layout(tiles)
                return t["settings"]
        raise KeyError(f"no tile {uid} on the board")

    def active_card_ids(self) -> set[str]:
        return {t.get("card") for t in self.load_layout() if t.get("card")}
