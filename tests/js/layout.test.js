/* The board's layout model, under `node --test`.
 *
 *   node --test tests/js/
 *
 * No npm, no framework, no DOM: layout.js is deliberately free of all three
 * so the part that can be wrong invisibly is the part that gets tested. A
 * mis-drawn tab is obvious the moment you look at the board; a card that
 * loses its uid takes the operator's per-tile settings with it.
 */
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const L = require(path.join(__dirname, '..', '..', 'hamdash', 'static', 'js',
  'layout.js'));

const FLAT = { uid: 't1', card: 'logbook', x: 2, y: 1, w: 5, h: 4,
  settings: { page_size: 30 } };
const STACK = { uid: 's1', x: 0, y: 0, w: 6, h: 5, active: 'b',
  cards: [{ uid: 'a', card: 'spots', settings: { q: 1 } },
    { uid: 'b', card: 'decoder', settings: {} }] };

const board = (...tiles) => L.normalise(tiles.map((t) => JSON.parse(JSON.stringify(t))));
const uids = (tiles) => tiles.map((t) => t.cards.map((c) => c.uid));

test('an old flat tile becomes a stack of one', () => {
  const t = L.asStack(FLAT);
  assert.deepStrictEqual(t.cards.map((c) => c.card), ['logbook']);
  assert.strictEqual(t.active, 't1');
  assert.deepStrictEqual([t.x, t.y, t.w, t.h], [2, 1, 5, 4]);
});

test('a card keeps the uid it had, and its settings with it', () => {
  // Per-instance state hangs off the uid. A new one is those settings gone.
  const t = L.asStack(FLAT);
  assert.strictEqual(t.cards[0].uid, 't1');
  assert.deepStrictEqual(t.cards[0].settings, { page_size: 30 });
  assert.strictEqual(t.uid, 't1');
});

test('an active naming a card that is not here is repaired', () => {
  assert.strictEqual(L.asStack({ ...STACK, active: 'gone' }).active, 'a');
});

test('a tile with no cards is not a tile', () => {
  assert.strictEqual(L.asStack({ uid: 'x', cards: [] }), null);
  assert.strictEqual(L.asStack({ uid: 'x' }), null);
  assert.strictEqual(L.asStack('nope'), null);
  assert.strictEqual(L.asStack(null), null);
});

test('junk geometry falls back rather than reaching the grid as NaN', () => {
  const t = L.asStack({ ...FLAT, x: 'left', h: null, w: {} });
  assert.strictEqual(t.x, 0);
  assert.strictEqual(t.h, 3);
  assert.strictEqual(t.w, 4);
});

test('fields nobody declared are dropped', () => {
  const t = L.asStack({ ...FLAT, nonsense: 1 });
  assert.ok(!('nonsense' in t));
});

test('normalising twice changes nothing', () => {
  const once = board(FLAT, STACK);
  assert.deepStrictEqual(L.normalise(once), once);
});

test('two cards sharing a uid are made distinct, the first keeping it', () => {
  const got = board({ uid: 'p', cards: [{ uid: 'same', card: 'spots' }] },
    { uid: 'q', active: 'same', cards: [{ uid: 'same', card: 'decoder' }] });
  const all = got.flatMap((t) => t.cards.map((c) => c.uid));
  assert.strictEqual(new Set(all).size, 2);
  assert.strictEqual(got[0].cards[0].uid, 'same');
  assert.strictEqual(got[1].active, got[1].cards[0].uid);
});

/* ---- moving cards about ------------------------------------------------ */

test('docking moves the card and keeps its uid and settings', () => {
  const tiles = board(FLAT, STACK);
  const got = L.dock(tiles, 't1', 's1');
  assert.strictEqual(got.changed, true);
  assert.strictEqual(got.tiles.length, 1, 'the emptied tile goes');
  assert.strictEqual(got.dropped.uid, 't1');
  const moved = L.cardOf(got.tiles, 't1');
  assert.deepStrictEqual(moved.settings, { page_size: 30 });
  assert.strictEqual(got.tiles[0].active, 't1', 'what you dropped is shown');
  assert.deepStrictEqual(uids(got.tiles), [['a', 'b', 't1']]);
});

test('docking out of a stack leaves the rest of it alone', () => {
  const tiles = board(FLAT, STACK);
  const got = L.dock(tiles, 'a', 't1');
  assert.strictEqual(got.dropped, null);
  assert.deepStrictEqual(uids(got.tiles), [['t1', 'a'], ['b']]);
  assert.strictEqual(L.stackOf(got.tiles, 'b').active, 'b',
    'the card that left was the active one, so another takes over');
});

test('docking onto itself, onto nothing, or onto a popped tile is a no-op', () => {
  const tiles = board(FLAT, { ...STACK, popped: true });
  for (const [card, onto] of [['t1', 't1'], ['t1', 'nope'], ['nope', 's1'],
    ['t1', 's1']]) {
    const got = L.dock(board(FLAT, { ...STACK, popped: true }), card, onto);
    assert.strictEqual(got.changed, false, `${card} -> ${onto}`);
  }
  assert.strictEqual(tiles.length, 2);
});

test('undocking makes a tile of its own and the card keeps its uid', () => {
  const got = L.undock(board(STACK), 'a', null, 12);
  assert.strictEqual(got.changed, true);
  assert.deepStrictEqual(uids(got.tiles), [['b'], ['a']]);
  assert.deepStrictEqual(L.cardOf(got.tiles, 'a').settings, { q: 1 });
  assert.strictEqual(got.made.active, 'a');
});

test('the last card in a tile cannot be undocked', () => {
  const got = L.undock(board(FLAT), 't1', null, 12);
  assert.strictEqual(got.changed, false);
  assert.strictEqual(got.made, null);
});

test('an undocked tile lands on the board, not off the side of it', () => {
  const wide = board({ ...STACK, w: 6 });
  const got = L.undock(wide, 'a', { x: 99, y: -4 }, 12);
  assert.ok(got.made.x >= 0 && got.made.x + got.made.w <= 12,
    `x=${got.made.x} w=${got.made.w}`);
  assert.ok(got.made.y >= 0);
});

test('removing the last card takes the tile with it', () => {
  const got = L.removeCard(board(FLAT), 't1');
  assert.strictEqual(got.changed, true);
  assert.deepStrictEqual(got.tiles, []);
  assert.strictEqual(got.dropped.uid, 't1');
});

test('removing one card of a stack leaves the tile standing', () => {
  const got = L.removeCard(board(STACK), 'b');
  assert.strictEqual(got.dropped, null);
  assert.deepStrictEqual(uids(got.tiles), [['a']]);
  assert.strictEqual(got.tiles[0].active, 'a', 'the one that went was active');
});

test('removing a card that is not there changes nothing', () => {
  const got = L.removeCard(board(FLAT), 'nope');
  assert.strictEqual(got.changed, false);
});

test('a card can be docked and undocked without losing anything', () => {
  // The round trip an operator does while rearranging. Settings surviving it
  // is the whole reason the uid is carried rather than regenerated.
  let tiles = board(FLAT, STACK);
  tiles = L.dock(tiles, 't1', 's1').tiles;
  tiles = L.undock(tiles, 't1', null, 12).tiles;
  const back = L.cardOf(tiles, 't1');
  assert.deepStrictEqual(back.settings, { page_size: 30 });
  assert.strictEqual(back.card, 'logbook');
  assert.strictEqual(L.allCards(tiles).length, 3);
});

/* ---- geometry ---------------------------------------------------------- */

test('overlapping tiles are pushed down, never sideways', () => {
  const tiles = board({ uid: 'a', card: 'x', x: 0, y: 0, w: 6, h: 2 },
    { uid: 'b', card: 'y', x: 0, y: 0, w: 6, h: 2 });
  L.resolve(tiles, tiles[0]);
  assert.strictEqual(tiles[1].x, 0, 'horizontal position is deliberate');
  assert.strictEqual(tiles[1].y, 2);
});

test('a chain of overlaps all moves, and resolving terminates', () => {
  const tiles = board({ uid: 'a', card: 'x', x: 0, y: 0, w: 4, h: 2 },
    { uid: 'b', card: 'y', x: 0, y: 0, w: 4, h: 2 },
    { uid: 'c', card: 'z', x: 0, y: 1, w: 4, h: 2 });
  L.resolve(tiles, tiles[0]);
  assert.ok(tiles[1].y >= 2 && tiles[2].y >= tiles[1].y + 2,
    `b=${tiles[1].y} c=${tiles[2].y}`);
});

test('a tile never overlaps itself', () => {
  const t = L.asStack(FLAT);
  assert.strictEqual(L.overlap(t, t), false);
});

test('nextRow is the bottom of the lowest tile', () => {
  assert.strictEqual(L.nextRow(board(FLAT, STACK)), 5);
  assert.strictEqual(L.nextRow([]), 0);
});

/* ---- what reaches layout.json ------------------------------------------ */

test('forSave sends the stack shape and nothing the board kept for itself', () => {
  const tiles = board(STACK);
  tiles[0].min_w = 3;                       // the Board's own business
  tiles[0].cards[0]._configurable = true;
  const [sent] = L.forSave(tiles);
  assert.ok(!('min_w' in sent));
  assert.ok(!('_configurable' in sent.cards[0]));
  assert.deepStrictEqual(Object.keys(sent.cards[0]).sort(),
    ['card', 'settings', 'title', 'uid', 'url']);
  assert.strictEqual(sent.active, 'b');
});

test('what forSave produces normalises back to what it came from', () => {
  // The round trip that actually happens: board saves, host normalises,
  // board loads it again. A field lost here is a setting lost on restart.
  const before = board(FLAT, STACK);
  const after = L.normalise(L.forSave(before));
  assert.deepStrictEqual(uids(after), uids(before));
  assert.deepStrictEqual(after.map((t) => t.active),
    before.map((t) => t.active));
  assert.deepStrictEqual(L.cardOf(after, 'a').settings, { q: 1 });
});
