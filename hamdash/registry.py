"""Card discovery. A card is a directory with a card.json manifest.

Three kinds, so the same board holds a one-line URL tile and a full program:
  url     nothing but an address. No code, no process.
  html    static files the host serves straight from the card directory.
  python  a real program: own process, own port, own HTTP API.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("url", "html", "python")


@dataclass
class Card:
    id: str
    path: Path
    name: str = ""
    kind: str = "python"
    version: str = "0.0.0"
    author: str = ""
    description: str = ""
    entry: str = "card.py"
    view: str = "index.html"
    url: str = ""
    w: int = 4
    h: int = 3
    min_w: int = 2
    min_h: int = 2
    permissions: list[str] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    error: str | None = None

    def schema(self) -> list[dict]:
        """Manifest settings, normalised into fields the UI can render.

        {"url": {"type": "string", "label": "Page address", "default": ""}}
        becomes a list, because order matters in a form and dict order in
        hand-written JSON is not something to rely on.
        """
        out = []
        for name, spec in (self.settings or {}).items():
            spec = spec if isinstance(spec, dict) else {"type": "string",
                                                        "default": spec}
            kind = spec.get("type", "string")
            if kind not in ("string", "number", "bool", "select", "secret"):
                kind = "string"
            out.append({
                "name": name,
                "type": kind,
                "label": spec.get("label", name.replace("_", " ")),
                "help": spec.get("help", ""),
                "default": spec.get("default", False if kind == "bool" else ""),
                "options": spec.get("options", []),
                "placeholder": spec.get("placeholder", ""),
            })
        return out

    def defaults(self) -> dict:
        return {f["name"]: f["default"] for f in self.schema()}

    def may(self, permission: str) -> bool:
        return permission in (self.permissions or [])

    def public(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "path"}
        d["path"] = str(self.path)
        d["schema"] = self.schema()
        return d


def _read(d: Path) -> Card | None:
    mf = d / "card.json"
    if not mf.exists():
        return None
    try:
        raw = json.loads(mf.read_text())
    except json.JSONDecodeError as exc:
        return Card(id=d.name, path=d, name=d.name, kind="html",
                    error=f"card.json is not valid JSON: {exc}")

    cid = raw.get("id") or d.name
    card = Card(id=cid, path=d, name=raw.get("name", cid))
    for key, val in raw.items():
        if key in ("id", "path"):
            continue
        if hasattr(card, key):
            setattr(card, key, val)

    if card.kind not in KINDS:
        card.error = f"unknown kind {card.kind!r} (expected one of {', '.join(KINDS)})"
    elif card.kind == "python" and not (d / card.entry).exists():
        card.error = f"entry {card.entry!r} is missing from {d}"
    elif card.kind == "html" and not (d / card.view).exists():
        card.error = f"view {card.view!r} is missing from {d}"
    elif card.kind == "url" and not card.url:
        card.error = "kind is 'url' but no url is set in card.json"
    return card


def discover(dirs) -> dict[str, Card]:
    """Later directories win on id collision, so a user copy overrides a
    bundled card of the same id."""
    found: dict[str, Card] = {}
    for root in [Path(p) for p in dirs]:
        if not root.exists():
            continue
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            card = _read(d)
            if card:
                found[card.id] = card
    return found
