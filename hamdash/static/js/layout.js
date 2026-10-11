/* layout.js — what a board is, with no DOM in sight.
 *
 * A tile is a box holding one or more cards, shown one at a time:
 *
 *   {uid, x, y, w, h, active, popped, cards: [{uid, card, title, url}, ...]}
 *
 * Everything here is arithmetic and object shuffling over plain tiles. It is
 * split out of `grid.js` for the same reason `cw.py` is split out of the
 * decoder card: this is the part that can be wrong in a way nobody sees. A
 * mis-drawn tab is obvious the moment you look at the board; a card that
 * quietly loses its uid takes the operator's per-tile settings with it, and a
 * normaliser that disagrees with the host's rewrites `layout.json` into a
 * shape the other side then repairs differently.
 *
 * `tests/js/layout.test.js` drives it under `node --test`, and
 * `tests/test_layout_agreement.py` runs the same fixtures through this and
 * through `config.normalise_layout` and insists they agree.
 *
 * **Every function here either returns a new value or says what it changed.**
 * The Board owns the DOM and has to know which tiles went away so it can drop
 * their nodes; it cannot be handed a quietly mutated array.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HDLayout = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
  const uid = () => Math.random().toString(36).slice(2, 9);

  /* What a card entry and a tile may carry. Anything else is dropped, so a
     hand-edited file cannot smuggle through a field something later reads as
     geometry. Kept in step with config.ENTRY_KEYS / STACK_KEYS. */
  const ENTRY_KEYS = ['uid', 'card', 'settings', 'title', 'url'];
  const STACK_KEYS = ['uid', 'x', 'y', 'w', 'h', 'active', 'cards', 'popped',
    'pop_w', 'pop_h', 'on_top', 'from_stack'];

  const pick = (obj, keys) => {
    const out = {};
    keys.forEach((k) => { if (obj[k] !== undefined) out[k] = obj[k]; });
    return out;
  };

  function entry(raw) {
    if (!raw || !raw.card) return null;
    const kept = pick(raw, ENTRY_KEYS);
    const out = { uid: String(kept.uid || '') || uid(),
      card: String(kept.card) };
    ['title', 'url'].forEach((k) => {
      if (kept[k] !== undefined) out[k] = kept[k];
    });
    out.settings = { ...(kept.settings || {}) };
    return out;
  }

  /* One tile in the stack shape, from either shape, or null if it is not a
     tile at all. The old flat `{uid, card, ...}` is what every upgrade
     starts from, and `add()` is still called with one. */
  function asStack(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const cards = Array.isArray(raw.cards)
      ? raw.cards.map(entry).filter(Boolean)
      : [entry(raw)].filter(Boolean);
    if (!cards.length) return null;

    const kept = pick(raw, STACK_KEYS);
    // Matches what `int()` does on the host, which is not what `Number()`
    // does here: Number(null) is 0 and Number('') is 0, while int(None) and
    // int('') both raise and fall back. A `w` of null became a tile zero
    // columns wide on the board and four columns wide on the host - the two
    // disagreeing about the same file, which is the whole thing the
    // agreement fixtures exist to stop.
    const whole = (k, fallback) => {
      const v = kept[k];
      if (v === null || v === undefined) return fallback;
      if (typeof v === 'object') return fallback;
      if (typeof v === 'string' && v.trim() === '') return fallback;
      const n = Number(v);
      return Number.isFinite(n) ? Math.trunc(n) : fallback;
    };
    // An `active` naming a card that is not here is worse than none: the
    // board draws an empty box under a working tab strip.
    const active = cards.some((c) => c.uid === kept.active)
      ? kept.active : cards[0].uid;

    // Built in a fixed order, not by copying whatever order the file had.
    // Normalising is not idempotent otherwise - the values match but the
    // keys come out shuffled - so every save rewrites layout.json with the
    // lines moved around, for no reason anybody can see.
    const t = { uid: String(kept.uid || '') || cards[0].uid,
      x: whole('x', 0), y: whole('y', 0), w: whole('w', 4), h: whole('h', 3),
      active, popped: !!kept.popped };
    ['pop_w', 'pop_h', 'on_top', 'from_stack'].forEach((k) => {
      if (kept[k] !== undefined) t[k] = kept[k];
    });
    t.cards = cards;                 // last: it is the long one to read
    return t;
  }

  /* The whole board, with every uid distinct. Two cards sharing a uid share
     their settings and their identity through the proxy, which shows up as
     one tile changing when you edit a different one. */
  function normalise(tiles) {
    const out = [];
    const seen = new Set();
    (Array.isArray(tiles) ? tiles : []).forEach((raw) => {
      const t = asStack(raw);
      if (!t) return;
      t.cards.forEach((c) => {
        if (seen.has(c.uid)) c.uid = uid();
        seen.add(c.uid);
      });
      if (!t.cards.some((c) => c.uid === t.active)) t.active = t.cards[0].uid;
      out.push(t);
    });
    return out;
  }

  /* ---- lookups -------------------------------------------------------- */

  const cardOf = (tiles, cardUid) => {
    for (const t of tiles) {
      const c = t.cards.find((x) => x.uid === cardUid);
      if (c) return c;
    }
    return undefined;
  };
  const stackOf = (tiles, cardUid) =>
    tiles.find((t) => t.cards.some((c) => c.uid === cardUid));
  const allCards = (tiles) => tiles.flatMap((t) => t.cards);

  /* ---- moving cards about --------------------------------------------- */

  /* Take a card off the board. `dropped` is the tile that went with it, if
     it was the last one in it - the Board needs that to drop its node. */
  function removeCard(tiles, cardUid) {
    const t = stackOf(tiles, cardUid);
    if (!t) return { tiles, changed: false, dropped: null, emptied: null };
    t.cards = t.cards.filter((c) => c.uid !== cardUid);
    if (t.cards.length) {
      if (t.active === cardUid) t.active = t.cards[0].uid;
      return { tiles, changed: true, dropped: null, emptied: t };
    }
    return { tiles: tiles.filter((x) => x !== t), changed: true,
      dropped: t, emptied: null };
  }

  /* Move a card into another tile, as a tab. `changed: false` for a no-op,
     so a drag that ends where it started is not a layout change. */
  function dock(tiles, cardUid, ontoTileUid) {
    const from = stackOf(tiles, cardUid);
    const onto = tiles.find((t) => t.uid === ontoTileUid);
    if (!from || !onto || from === onto || onto.popped) {
      return { tiles, changed: false, dropped: null, from: null, onto: null };
    }
    const card = from.cards.find((c) => c.uid === cardUid);
    from.cards = from.cards.filter((c) => c.uid !== cardUid);
    onto.cards.push(card);
    onto.active = cardUid;
    if (from.cards.length) {
      if (from.active === cardUid) from.active = from.cards[0].uid;
      return { tiles, changed: true, dropped: null, from, onto };
    }
    return { tiles: tiles.filter((t) => t !== from), changed: true,
      dropped: from, from: null, onto };
  }

  /* Pull a tab out into a tile of its own. The card keeps its uid, so its
     settings come with it. */
  function undock(tiles, cardUid, where, cols) {
    const from = stackOf(tiles, cardUid);
    if (!from || from.cards.length < 2) {
      return { tiles, changed: false, made: null, from: null };
    }
    const card = from.cards.find((c) => c.uid === cardUid);
    from.cards = from.cards.filter((c) => c.uid !== cardUid);
    if (from.active === cardUid) from.active = from.cards[0].uid;
    const span = Math.max(1, Math.min(from.w, cols));
    const made = asStack({
      cards: [card], active: card.uid, w: span, h: from.h,
      x: clamp(where && where.x !== undefined ? where.x : from.x,
        0, Math.max(0, cols - span)),
      y: Math.max(0, where && where.y !== undefined ? where.y : from.y + from.h),
    });
    return { tiles: tiles.concat([made]), changed: true, made, from };
  }

  /* ---- geometry -------------------------------------------------------- */

  const overlap = (a, b) => a.uid !== b.uid
    && a.x < b.x + b.w && b.x < a.x + a.w
    && a.y < b.y + b.h && b.y < a.y + a.h;

  /* Overlap is resolved by pushing tiles down, never sideways: horizontal
     position is the thing people arrange deliberately, vertical order isn't. */
  function resolve(tiles, moved, seen) {
    seen = seen || new Set();
    if (seen.has(moved.uid)) return tiles;
    seen.add(moved.uid);
    tiles.filter((o) => overlap(moved, o))
      .sort((p, q) => p.y - q.y)
      .forEach((o) => {
        // Never upwards. Straight assignment put a tile back where it had
        // just been pushed from: with three tiles overlapping, the recursion
        // moved the third down and then the outer loop set it back to the
        // first tile's bottom edge, leaving it sitting on top of the second.
        o.y = Math.max(o.y, moved.y + moved.h);
        resolve(tiles, o, seen);
      });
    return tiles;
  }

  const nextRow = (tiles) => tiles.reduce((m, t) => Math.max(m, t.y + t.h), 0);

  /* ---- what goes to the host ------------------------------------------ */

  /* The board sends exactly this; anything the Board keeps for its own use
     (min_w, a card's _configurable flag) must not reach layout.json, where
     the next load would read it back as part of the tile. */
  const forSave = (tiles) => tiles.map((t) => ({
    uid: t.uid, x: t.x, y: t.y, w: t.w, h: t.h, active: t.active,
    popped: !!t.popped, pop_w: t.pop_w, pop_h: t.pop_h, on_top: t.on_top,
    from_stack: t.from_stack,
    cards: (t.cards || []).map((c) => ({
      uid: c.uid, card: c.card, title: c.title, url: c.url,
      settings: c.settings || {},
    })),
  }));

  return { asStack, normalise, cardOf, stackOf, allCards, removeCard, dock,
    undock, overlap, resolve, nextRow, forSave, clamp, uid,
    ENTRY_KEYS, STACK_KEYS };
}));
