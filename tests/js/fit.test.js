/* A tile big enough to show its card, under `node --test`.
 *
 *   node --test 'tests/js/*.test.js'
 *
 * A card says what it needs in pixels (`fit` in card.json), because a
 * column is a twelfth of the window and a need in columns that fits on one
 * screen scrolls on a smaller one. The board turns pixels into columns and
 * rows for the window it is in: a new tile starts at least that big, and
 * no tile can be resized smaller. The numbers in the manifests were
 * measured on a live board - the rig card asked for 535 px of height in a
 * 3x3 tile that gave it 232, which is why it had scroll bars.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const JS = path.join(ROOT, 'hamdash', 'static', 'js');
const L = require(path.join(JS, 'layout.js'));
const document = { createElement: () => ({}) };
const gwin = { HDLayout: L, document, addEventListener() {} };
// eslint-disable-next-line no-new-func
new Function('window', 'document', fs.readFileSync(path.join(JS, 'grid.js'), 'utf8'))(gwin, document);
const B = gwin.Board.prototype;

/* A board 1280 px wide, as measured: 12 columns of ~96 px, 80 px rows. */
function aBoard(fits = {}, cellW = 95.83) {
  const b = Object.create(B);
  Object.defineProperty(b, 'cellW', { value: cellW });
  Object.assign(b, { cols: 12, rowPx: 80, gap: 10, tiles: [],
    fitOf: (id) => fits[id] || null, onChange() {},
    _nextRow: () => 0, _mount() {}, _resolve() {}, reflow() {} });
  return b;
}

test('pixels become columns for the board as wide as it is', () => {
  const wide = aBoard();
  // A 3-column tile is 307.5 px, so its card frame is 305.5 - which the
  // browser reports as 306. 305 fits in three columns...
  assert.strictEqual(wide.colsFor(305), 3);
  // ...and 306 does not, by half a pixel.
  assert.strictEqual(wide.colsFor(306), 4);
  // The same need takes more columns on a narrower window.
  const narrow = aBoard({}, 60);
  assert.ok(narrow.colsFor(460) > wide.colsFor(460));
  // Never more than the board has.
  assert.strictEqual(wide.colsFor(5000), 12);
});

test('pixels become rows: a 3-row tile gives its card 232', () => {
  const b = aBoard();
  assert.strictEqual(b.rowsFor(232), 3);
  assert.strictEqual(b.rowsFor(233), 4);
});

test('a new tile starts big enough for its card', () => {
  const b = aBoard({ rig_control: { w: 460, h: 410 } });
  const t = B.add.call(b, { card: 'rig_control', w: 4, h: 3 });
  assert.strictEqual(t.w, b.colsFor(460));
  assert.strictEqual(t.h, b.rowsFor(410));
  assert.ok(t.w > 4 && t.h > 3, 'it started at the size that scrolls');
});

test('a list card gets its width and keeps the height it asked for', () => {
  const b = aBoard({ spots: { w: 590 } });
  const t = B.add.call(b, { card: 'spots', w: 4, h: 5 });
  assert.strictEqual(t.w, b.colsFor(590));
  assert.strictEqual(t.h, 5);
});

test('a card asking for less than its default keeps the default', () => {
  const b = aBoard({ beacon_map: { w: 100, h: 100 } });
  const t = B.add.call(b, { card: 'beacon_map', w: 6, h: 5 });
  assert.deepStrictEqual([t.w, t.h], [6, 5]);
});

test('a stack is as big as the most demanding card in it', () => {
  const b = aBoard({ rig_control: { w: 460, h: 410 }, spots: { w: 590 } });
  const m = b.minOf({ cards: [{ card: 'rig_control' }, { card: 'spots' }] });
  assert.strictEqual(m.w, b.colsFor(590));
  assert.strictEqual(m.h, b.rowsFor(410));
});

test('resizing goes no smaller than the card needs', () => {
  // The minimum is worked out each time, because layout.json does not keep
  // it: with min_w on the tile only, it held until the next reload.
  const src = fs.readFileSync(path.join(JS, 'grid.js'), 'utf8');
  assert.doesNotMatch(src, /t\.min_w \|\| 2, this\.cols/,
    'a resize still clamps to the floor stored on the tile');
  assert.strictEqual((src.match(/this\.minOf\(t\)/g) || []).length, 4,
    'add, drag-resize, keyboard resize and the automatic fit all ask minOf');
});

test('the cards measured to need it say so', () => {
  const fit = (id) => JSON.parse(fs.readFileSync(
    path.join(ROOT, 'plugins', id, 'card.json'), 'utf8')).fit || {};
  assert.ok(fit('rig_control').w && fit('rig_control').h);
  // The lists are meant to grow and scroll: width only.
  for (const id of ['logbook', 'spots']) {
    assert.ok(fit(id).w, `${id} has no width`);
    assert.strictEqual(fit(id).h, undefined, `${id} would be held tall`);
  }
});

/* ---- a card asking its tile to fit its content (HD.autoHeight) ---- */
function aTile(over = {}) {
  const b = aBoard();
  const t = { uid: 't1', x: 0, y: 0, w: 6, h: 5, active: 'c1',
    cards: [{ uid: 'c1', card: 'rig_control' }, { uid: 'c2', card: 'spots' }], ...over };
  const saved = [];
  const resolved = [];
  Object.assign(b, { tiles: [t], _nodes: new Map(), stackOf: () => t,
    onChange: () => saved.push(t.h), _resolve: (x) => resolved.push(x.uid) });
  return { b, t, saved, resolved };
}

test('a tile grows to fit its content, in whole rows', () => {
  const { b, t, saved, resolved } = aTile();
  B.fitHeight.call(b, 'c1', 500);          // 500 px of content: 6 rows
  assert.strictEqual(t.h, b.rowsFor(500));
  assert.strictEqual(t.h, 6);
  assert.deepStrictEqual(saved, [6], 'the new height was not saved');
  assert.deepStrictEqual(resolved, ['t1'], 'the tiles below were not pushed down');
});

test('and shrinks back as the content does - to where it started, no further', () => {
  const { b, t } = aTile();
  B.fitHeight.call(b, 'c1', 590);          // 7 rows: 7 x 90 - 10 - 28 = 592
  assert.strictEqual(t.h, 7);
  B.fitHeight.call(b, 'c1', 500);          // a row of memories deleted
  assert.strictEqual(t.h, 6);
  B.fitHeight.call(b, 'c1', 100);          // nearly empty
  assert.strictEqual(t.h, 5, 'it shrank below the size the operator chose');
  assert.strictEqual(t._autoBase, undefined, 'still thinks it is grown');
});

test('a tile the operator made tall is not shrunk to its content', () => {
  const { b, t, saved } = aTile({ h: 8 });
  B.fitHeight.call(b, 'c1', 300);
  assert.strictEqual(t.h, 8);
  assert.deepStrictEqual(saved, [], 'it saved a change that did not happen');
});

test('only the card on show, and never a popped-out tile', () => {
  const back = aTile();
  B.fitHeight.call(back.b, 'c2', 900);     // c2 is a background tab
  assert.strictEqual(back.t.h, 5);
  const popped = aTile({ popped: true });
  B.fitHeight.call(popped.b, 'c1', 900);
  assert.strictEqual(popped.t.h, 5);
});

test('a size set by hand is what an automatic fit grows from', () => {
  const src = fs.readFileSync(path.join(JS, 'grid.js'), 'utf8');
  // Both resize paths forget the automatic base, or the next fit would
  // shrink the tile back past where the operator just put it.
  assert.strictEqual((src.match(/delete t\._autoBase;/g) || []).length, 3,
    'drag-resize, keyboard resize and fitHeight itself');
  // Not saved: layout.json keeps the stack keys only.
  assert.ok(!L.STACK_KEYS.includes('_autoBase'));
});

test('a card can only resize its own tile', () => {
  const app = fs.readFileSync(path.join(JS, 'app.js'), 'utf8');
  const at = app.indexOf("d.__hamdash !== 'height'");
  assert.ok(at > 0, 'the board does not listen');
  const body = app.slice(at, at + 600);
  // The uid in the message is checked against the frame it came from.
  assert.match(body, /f\.contentWindow === e\.source/);
  assert.match(body, /frame\.dataset\.uid !== d\.uid/);
  const page = fs.readFileSync(path.join(ROOT, 'plugins', 'rig_control', 'index.html'), 'utf8');
  assert.match(page, /HD\.autoHeight\(\);/);
});
