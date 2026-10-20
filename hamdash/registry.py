"""Card discovery. A card is a directory with a card.json manifest.

Three kinds, so the same board holds a one-line URL tile and a full program:
  url     nothing but an address. No code, no process.
  html    static files the host serves straight from the card directory.
  python  a real program: own process, own port, own HTTP API.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from hamdash import bus

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
    # Set to MADE_HERE on a manifest hamdash itself wrote from the "New
    # from URL" form. That is what makes it safe to delete from the UI:
    # there is nothing in it but a name and an address, so nothing of the
    # operator's can be lost. A card they wrote has their code in it.
    created_by: str = ""
    view: str = "index.html"
    url: str = ""
    w: int = 4
    h: int = 3
    min_w: int = 2
    min_h: int = 2
    permissions: list[str] = field(default_factory=list)
    # Bus namespace, declared. Both default to empty, which means "not
    # declared" rather than "none": publishes falls back to the whole of
    # <card_id>.*, subscribes to everything. Declaring narrows.
    publishes: list[str] = field(default_factory=list)
    subscribes: list[str] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    # Other cards this one needs running while its tiles are on the board.
    # The rig card plays receive audio through `rx_audio`, which has no
    # tiles of its own any more - without this, the next layout save would
    # reap the audio engine out from under a rig tile that is listening.
    uses: list[str] = field(default_factory=list)
    # Retired: kept installed because another card uses it, but not
    # offered in the Cards menu, and its tiles are taken off the board.
    hidden: bool = False
    error: str | None = None
    # Things wrong with the manifest that don't stop the card running.
    warnings: list[str] = field(default_factory=list)

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
            # `audio_in` is a select the *host* fills: the card cannot
            # know what sound cards this machine has, and three cards
            # were each asking the operator to type part of a device
            # name from memory. Anything the manifest puts in `options`
            # is offered first (`rig`, `mock`), then the real inputs.
            # `radio` is the same idea for the configured radios:
            # "selected" first, then each radio by name, stored by id.
            # One line on purpose: tests/test_card_docs.py reads this
            # tuple to check every type is documented, and its regex
            # cannot span lines.
            if kind not in ("string", "number", "bool", "select", "secret", "audio_in", "radio"):
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

    def may_publish(self, topic: str) -> str | None:
        """None if this card may publish the topic, else why not."""
        return bus.refusal(self.id, topic, self.publishes)

    def subscribe_filter(self) -> list[str] | None:
        """Topics this card may receive, or None for unrestricted.

        Its own namespace is implicit. Two tiles of one card are separate
        pages served by one process, so a tile has to be able to hear what
        another tile published without declaring its own id - and it cannot
        learn anything it did not produce that way.
        """
        if not self.subscribes:
            return None
        return [*self.subscribes, self.id]

    def check_namespace(self) -> None:
        """Validate the declared namespace against the policy.

        A publishes entry outside the card's own namespace can never fire, so
        it is a manifest mistake worth naming at load rather than leaving the
        author to wonder why a subscriber is silent. It is a warning and not
        an error: the card still runs, it just can't publish that topic.
        """
        self.subscribes = _strings(self.subscribes)
        kept = []
        for topic in _strings(self.publishes):
            why = bus.refusal(self.id, topic)
            if why:
                self.warnings.append(f"publishes: {why}")
            else:
                kept.append(topic)
        self.publishes = kept

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

    card.uses = _strings(card.uses)
    card.hidden = card.hidden is True
    card.check_namespace()

    if card.kind not in KINDS:
        card.error = f"unknown kind {card.kind!r} (expected one of {', '.join(KINDS)})"
    elif card.kind == "python" and not (d / card.entry).exists():
        card.error = f"entry {card.entry!r} is missing from {d}"
    elif card.kind == "html" and not (d / card.view).exists():
        card.error = f"view {card.view!r} is missing from {d}"
    elif card.kind == "url" and not card.url:
        card.error = "kind is 'url' but no url is set in card.json"
    return card


def _strings(value) -> list[str]:
    """A manifest list that survives being written as a bare string."""
    if isinstance(value, str):
        value = [value]
    return [v.strip() for v in (value or []) if isinstance(v, str) and v.strip()]


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


# ------------------------------------------------------- cards made here
#
# A `url` card is a name and an address, so there is no reason to make
# somebody open an editor to write one. The UI writes the manifest.
#
# Which also means a manifest is now written from a *form*, and that is a
# different thing from one a person typed: the address ends up as an
# iframe `src` on the board's own origin, where `javascript:` would run
# with the board's privileges and `file:` would read the disk. So the
# scheme is checked here rather than left to the browser.

MADE_HERE = "hamdash"

ALLOWED_SCHEMES = ("http", "https")


class CardError(ValueError):
    """Something the operator can fix. The message says what to do."""


def tidy_url(raw: str) -> str:
    """The address as it will be stored, or raise saying what is wrong.

    A bare `qrz.com` gets `https://`, the way a browser's address bar
    does - the alternative is refusing the most natural thing to type.
    Anything that names a scheme keeps it and has to be one we will put
    in a frame.
    """
    url = (raw or "").strip()
    if not url:
        raise CardError("give the page address, e.g. https://www.qrz.com")
    if any(ch.isspace() or ord(ch) < 0x20 for ch in url):
        raise CardError("that address has a space or a line break in it")
    # A scheme, not a port: `localhost:8080` is a host somebody typed and
    # `javascript:alert(1)` is the thing being kept out, and the only
    # thing that tells them apart before the colon is what follows it.
    m = re.match(r"([a-zA-Z][a-zA-Z0-9+.\-]*):(?!\d)", url)
    scheme = m.group(1).lower() if m else ""
    if not scheme:
        return "https://" + url
    if scheme not in ALLOWED_SCHEMES:
        raise CardError(f"{scheme}: addresses cannot go in a tile — "
                        "use http:// or https://")
    return url


def slug(name: str) -> str:
    """A card id from a card name: one safe path segment."""
    out = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return out[:40]


def safe_id(cid: str) -> str:
    """A card id that is a plain directory name and nothing else.

    `..` and separators are the whole point: this id is joined onto the
    cards directory and then created or deleted.
    """
    cid = (cid or "").strip()
    if not cid or cid != slug(cid):
        raise CardError(f"{cid!r} is not a usable card id")
    return cid


def free_id(base: str, taken) -> str:
    """`qrz`, or `qrz_2` if that is spoken for.

    Collisions matter more than they look: a user card beats a bundled
    one of the same id (that is how you fork a card), so a page called
    "Logbook" would quietly replace the logbook.
    """
    taken = set(taken or ())
    if base not in taken:
        return base
    for n in range(2, 1000):
        if f"{base}_{n}" not in taken:
            return f"{base}_{n}"
    raise CardError("too many cards with that name")


def make_url_card(root: Path, name: str, url: str, w: int = 4, h: int = 3,
                  card_id: str = "", taken=()) -> Card:
    """Write a `kind: url` manifest into the user's cards directory.

    `card_id` is for putting one back after a delete: Undo has to restore
    the same id or the tiles that referred to it come back pointing at
    nothing.
    """
    name = (name or "").strip()
    if not name:
        raise CardError("give the card a name — it is what the menu shows")
    name = name[:60]
    url = tidy_url(url)

    base = slug(name) or "page"
    cid = safe_id(card_id) if card_id else free_id(base, taken)
    if card_id and cid in set(taken or ()):
        raise CardError(f"a card called {cid!r} is already here")

    root = Path(root)
    d = root / cid
    # Belt and braces over safe_id: whatever the id turned out to be, the
    # directory it names has to be directly inside the cards folder.
    if d.resolve().parent != root.resolve():
        raise CardError(f"{cid!r} is not a usable card id")
    if d.exists():
        raise CardError(f"a card folder called {cid!r} is already here")

    d.mkdir(parents=True)
    (d / "card.json").write_text(json.dumps({
        "id": cid,
        "name": name,
        "kind": "url",
        "version": "1.0.0",
        "created_by": MADE_HERE,
        "description": url,
        "url": url,
        "w": max(1, int(w or 4)),
        "h": max(1, int(h or 3)),
    }, indent=2) + "\n")
    card = _read(d)
    if card is None:                      # cannot happen; say so if it does
        raise CardError(f"wrote {d} but it does not read back as a card")
    return card


def may_remove(card: Card, root: Path) -> str | None:
    """None if the UI may delete this card, else why not.

    Two conditions, and both are needed. The marker says hamdash wrote
    it, so nothing but a name and an address is lost. The directory check
    says where it lives, so a bundled card can never be deleted however
    its manifest reads.
    """
    if card.created_by != MADE_HERE:
        return (f"{card.name} was not made from a URL here — "
                "delete its folder yourself if you mean to remove it")
    try:
        inside = Path(card.path).resolve().parent == Path(root).resolve()
    except OSError:
        inside = False
    if not inside:
        return f"{card.name} does not live in your cards folder"
    return None


def remove(card: Card, root: Path) -> None:
    why = may_remove(card, root)
    if why:
        raise CardError(why)
    shutil.rmtree(Path(card.path))
