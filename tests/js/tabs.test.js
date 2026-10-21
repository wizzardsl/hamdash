/* Dragging a tab in a stack, under `node --test`.
 *
 *   node --test 'tests/js/*.test.js'
 *
 * Along the strip it changes the tab's place in the stack; off the strip -
 * down, or past either end of the tile - it tears off into a tile of its
 * own. Tearing off used to be the only thing a tab drag did, so putting
 * tabs in an order meant taking each one out and docking it back.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const JS = path.join(__dirname, '..', '..', 'hamdash', 'static', 'js');
const L = require(path.join(JS, 'layout.js'));

/* ---- the model ------------------------------------------------------- */
const stack = () => [{ uid: 's', x: 0, y: 0, w: 4, h: 3, active: 'b',
  cards: [{ uid: 'a', card: 'logbook' }, { uid: 'b', card: 'spots' },
          { uid: 'c', card: 'rig_control' }] }];
const order = (tiles) => tiles[0].cards.map((c) => c.uid).join('');

test('a tab moves to its new place, and nothing else changes', () => {
  const tiles = stack();
  const got = L.reorder(tiles, 'a', 2);
  assert.strictEqual(got.changed, true);
  assert.strictEqual(order(tiles), 'bca');
  assert.strictEqual(tiles[0].active, 'b', 'the card on show changed');
  assert.strictEqual(tiles.length, 1, 'it left the stack');
});

test('a drag that ends where it began is not a change', () => {
  const tiles = stack();
  assert.strictEqual(L.reorder(tiles, 'b', 1).changed, false);
  assert.strictEqual(order(tiles), 'abc');
});

test('a place past the end is the end', () => {
  const tiles = stack();
  L.reorder(tiles, 'a', 99);
  assert.strictEqual(order(tiles), 'bca');
});

/* Three tabs 60 wide, 2 apart: [a 0-60][b 62-122][c 124-184]. */
const BOXES = [{ left: 0, width: 60 }, { left: 62, width: 60 }, { left: 124, width: 60 }];

test('a tab lands before the first tab whose middle it is left of', () => {
  assert.strictEqual(L.tabSlot(BOXES, 80, 0), 0, 'a, left of b\'s middle (92): stays');
  assert.strictEqual(L.tabSlot(BOXES, 130, 0), 1, 'a, past b\'s middle: after b');
  assert.strictEqual(L.tabSlot(BOXES, 200, 0), 2, 'a, past everything: last');
  assert.strictEqual(L.tabSlot(BOXES, 10, 2), 0, 'c, over a: first');
});

/* ---- the drag, through the real grid.js ------------------------------- */
const listeners = {};
const win = {
  HDLayout: L,
  addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
  removeEventListener(type, fn) {
    listeners[type] = (listeners[type] || []).filter((f) => f !== fn);
  },
};
const fire = (type, ev) => (listeners[type] || []).slice().forEach((f) => f(ev));
// eslint-disable-next-line no-new-func
new Function('window', 'document', fs.readFileSync(path.join(JS, 'grid.js'), 'utf8'))(
  win, { createElement: () => ({}) });
const Board = win.Board;

function aStrip() {
  const tiles = stack();
  const t = tiles[0];
  const tabs = BOXES.map((b, i) => ({
    uid: t.cards[i].uid, classList: { add() {}, remove() {} },
    getBoundingClientRect: () => b,
  }));
  const strip = {
    querySelectorAll: () => tabs,
    insertBefore() {},
  };
  const node = {
    querySelector: () => strip,
    getBoundingClientRect: () => ({ left: 0, right: 300, top: 0, bottom: 200 }),
  };
  const did = { saved: 0, undocked: [], painted: 0 };
  const self = {
    tiles, cols: 12, rowPx: 80, gap: 10, cellW: 90,
    el: { getBoundingClientRect: () => ({ left: 0, top: 0 }) },
    _nodes: new Map(),
    _paintTabs: () => { did.painted += 1; },
    onChange: () => { did.saved += 1; },
    undock: (uid) => { did.undocked.push(uid); return null; },
    _drag() {},
  };
  const grab = (i) => Board.prototype._dragTab.call(self, {
    button: 0, clientX: BOXES[i].left + 30, clientY: 10,
    target: { closest: () => tabs[i] },
  }, t, node, t.cards[i].uid);
  return { tiles, did, grab };
}

test('along the strip, a tab changes places and stays in the stack', () => {
  const s = aStrip();
  s.grab(0);                                     // a, from the middle of a
  fire('pointermove', { clientX: 60, clientY: 12 });
  fire('pointermove', { clientX: 140, clientY: 12 });
  fire('pointerup', {});
  assert.strictEqual(order(s.tiles), 'bac');
  assert.deepStrictEqual(s.did.undocked, [], 'it tore off instead');
  assert.strictEqual(s.did.saved, 1);
});

test('pulled down off the strip, it still tears off', () => {
  const s = aStrip();
  s.grab(1);
  fire('pointermove', { clientX: 95, clientY: 40 });
  assert.deepStrictEqual(s.did.undocked, ['b']);
  assert.strictEqual(order(s.tiles), 'abc', 'it was reordered as well');
});

test('changing your mind mid-reorder and pulling it down tears it off', () => {
  const s = aStrip();
  s.grab(0);
  fire('pointermove', { clientX: 140, clientY: 12 });
  fire('pointermove', { clientX: 140, clientY: 60 });
  assert.deepStrictEqual(s.did.undocked, ['a']);
  fire('pointerup', {});
  assert.strictEqual(s.did.saved, 0, 'a reorder was saved as well');
});

test('past the end of the tile, it tears off sideways', () => {
  const s = aStrip();
  s.grab(2);
  fire('pointermove', { clientX: 340, clientY: 12 });
  assert.deepStrictEqual(s.did.undocked, ['c']);
});

test('Escape puts a reordered tab back', () => {
  const s = aStrip();
  s.grab(0);
  fire('pointermove', { clientX: 160, clientY: 12 });
  fire('keydown', { key: 'Escape' });
  fire('pointerup', {});
  assert.strictEqual(order(s.tiles), 'abc');
  assert.strictEqual(s.did.saved, 0);
});

test('a click that hardly moves is a click, not a drag', () => {
  const s = aStrip();
  s.grab(1);
  fire('pointermove', { clientX: 95, clientY: 11 });
  fire('pointerup', {});
  assert.strictEqual(order(s.tiles), 'abc');
  assert.deepStrictEqual([s.did.saved, s.did.undocked.length], [0, 0]);
});
