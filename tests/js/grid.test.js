/* What the board does with tiles, under `node --test`: what a tile's body
 * contains, and where a drop docks.
 *
 *   node --test 'tests/js/*.test.js'
 *
 * grid.js owns the pixels, so it needs a DOM. The shim below is deliberately
 * tiny: it implements only what `_paintBody` touches, and the test drives the
 * real function through `Board.prototype._paintBody.call(...)` rather than
 * building a Board, so none of the rest of the file has to be stood up.
 *
 * This exists because of a bug that read as "pop-in is broken". Popping a
 * card out replaces the body with a "Running in its own window" placeholder;
 * popping back in used to prune `.tile-card` nodes only, so that placeholder
 * survived. It is `height: 100%`, so the card came back mounted and pushed
 * out of sight beneath it, and its stale "Bring it back" button did nothing
 * because the tile was already back. Only restarting the app cleared it.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const JS = path.join(__dirname, '..', '..', 'hamdash', 'static', 'js');
const L = require(path.join(JS, 'layout.js'));

/* ---- just enough DOM ------------------------------------------------- */
class El {
  constructor(tag) {
    this.tagName = (tag || 'div').toUpperCase();
    this.children = [];
    this.parent = null;
    this.dataset = {};
    this.style = {};
    this._classes = new Set();
    this.textContent = '';
    this.classList = {
      add: (c) => this._classes.add(c),
      remove: (c) => this._classes.delete(c),
      contains: (c) => this._classes.has(c),
      toggle: (c, on) => (on ? this._classes.add(c) : this._classes.delete(c)),
    };
  }
  get className() { return [...this._classes].join(' '); }
  set className(v) {
    this._classes = new Set(String(v).split(/\s+/).filter(Boolean));
  }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  setAttribute(k, v) { this.attrs = Object.assign(this.attrs || {}, { [k]: v }); }
  getAttribute(k) { return (this.attrs || {})[k]; }
  addEventListener(type, fn) { (this.listeners ||= {})[type] = fn; }
  fire(type, ev) {
    const fn = (this.listeners || {})[type];
    if (fn) fn({ preventDefault() {}, target: this, ...ev });
  }
  scrollIntoView() {}
  /* Laid out by the test, since nothing here does layout. */
  setRect(r) {
    this.rect = { ...r, right: r.left + r.width, bottom: r.top + r.height };
    return this;
  }
  getBoundingClientRect() {
    return this.rect || { left: 0, top: 0, width: 0, height: 0,
      right: 0, bottom: 0 };
  }
  remove() {
    if (!this.parent) return;
    const i = this.parent.children.indexOf(this);
    if (i >= 0) this.parent.children.splice(i, 1);
    this.parent = null;
  }
  /* innerHTML is only ever used here to clear the body, or to stamp the
     placeholder - so parsing is limited to finding the buttons in it. */
  set innerHTML(html) {
    this.children = [];
    this._html = String(html);
    for (const m of this._html.matchAll(/data-act="([^"]+)"/g)) {
      const b = new El('button');
      b.dataset.act = m[1];
      this.appendChild(b);
    }
  }
  get innerHTML() { return this._html || ''; }
  _matches(sel) {
    const act = sel.match(/\[data-act="([^"]+)"\]/);
    if (act && this.dataset.act !== act[1]) return false;
    const uid = sel.match(/\[data-uid="([^"]+)"\]/);
    if (uid && this.dataset.uid !== uid[1]) return false;
    const cls = sel.match(/^\.([\w-]+)/);
    if (cls && !this._classes.has(cls[1])) return false;
    return !!(act || uid || cls);
  }
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => n.children.forEach((c) => {
      if (c._matches(sel)) out.push(c);
      walk(c);
    });
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

const document = { createElement: (tag) => new El(tag) };

/* ---- load grid.js the way a browser would ----------------------------- */
const win = { HDLayout: L, document };
// eslint-disable-next-line no-new-func
new Function('window', 'document', fs.readFileSync(path.join(JS, 'grid.js'),
  'utf8'))(win, document);
const Board = win.Board;

/* ---- a tile node, and a stand-in board -------------------------------- */
function aNode() {
  const node = new El('section');
  const body = new El('div');
  body.className = 'tile-body';
  node.appendChild(body);
  const btn = new El('button');
  btn.dataset.act = 'popout';
  node.appendChild(btn);
  return node;
}

function aBoard(tile) {
  return {
    get: (uid) => tile.cards.find((c) => c.uid === uid),
    isPoppedOpen: () => true,
    renderBody: (card, holder) => {
      const f = new El('iframe');
      f.dataset.uid = card.uid;
      holder.appendChild(f);
    },
    _showActive: () => {},
    popIn: async () => {},
    onReopen: () => {},
  };
}

const aTile = () => ({
  uid: 't1', x: 0, y: 0, w: 4, h: 3, active: 'c1', popped: false,
  cards: [{ uid: 'c1', card: 'path_map' }],
});

const paint = (self, t, node) => Board.prototype._paintBody.call(self, t, node);
const bodyOf = (node) => node.querySelector('.tile-body');

test('popping a card out replaces its body with the placeholder', () => {
  const t = aTile(); const node = aNode(); const self = aBoard(t);
  paint(self, t, node);
  assert.strictEqual(bodyOf(node).querySelectorAll('.tile-card').length, 1);

  t.popped = true;
  paint(self, t, node);
  assert.ok(bodyOf(node).querySelector('[data-act="popin"]'),
    'no "Bring it back" button while popped out');
  assert.strictEqual(bodyOf(node).querySelectorAll('.tile-card').length, 0);
});

test('popping it back in removes the placeholder', () => {
  const t = aTile(); const node = aNode(); const self = aBoard(t);
  paint(self, t, node);
  t.popped = true;
  paint(self, t, node);
  t.popped = false;
  paint(self, t, node);

  const body = bodyOf(node);
  assert.strictEqual(body.querySelector('[data-act="popin"]'), null,
    'the "Running in its own window" placeholder outlived the pop-in: it is '
    + 'height:100%, so the card is back but pushed out of sight under it');
  assert.strictEqual(body.querySelectorAll('.tile-card').length, 1);
  assert.strictEqual(body.children.length, 1, 'something else is in the body');
});

test('a card that stays put is not rebuilt', () => {
  // Rebuilding reloads the iframe, which loses a decoder's text or a
  // half-typed contact just because a neighbour moved.
  const t = aTile(); const node = aNode(); const self = aBoard(t);
  paint(self, t, node);
  const first = bodyOf(node).querySelector('.tile-card');
  paint(self, t, node);
  assert.strictEqual(bodyOf(node).querySelector('.tile-card'), first);
});

test('a card that has left the tile goes with it', () => {
  const t = aTile(); const node = aNode(); const self = aBoard(t);
  t.cards.push({ uid: 'c2', card: 'spots' });
  paint(self, t, node);
  assert.strictEqual(bodyOf(node).querySelectorAll('.tile-card').length, 2);

  t.cards = t.cards.filter((c) => c.uid !== 'c2');
  paint(self, t, node);
  const left = bodyOf(node).querySelectorAll('.tile-card');
  assert.strictEqual(left.length, 1);
  assert.strictEqual(left[0].dataset.uid, 'c1');
});


/* ---- where a drop docks ----------------------------------------------
 * The whole of a target tile used to dock. On a full board that is almost
 * everywhere, so moving a card across the board kept making it somebody
 * else's tab and plain rearranging became the hard case. Only the tab strip
 * docks now - the row where tabs already live.
 */
const HEAD_H = 28;

function aLaidOutTile(uid, { top, height = 200, popped = false }) {
  const node = new El('section');
  node.className = 'tile';
  node.dataset.uid = uid;
  node.setRect({ left: 100, top, width: 300, height });
  const head = new El('header');
  head.className = 'tile-head';
  head.setRect({ left: 100, top, width: 300, height: HEAD_H });
  node.appendChild(head);
  return { t: { uid, popped, cards: [{ uid, card: 'x' }] }, node };
}

function aLaidOutBoard() {
  const a = aLaidOutTile('target', { top: 0 });
  const b = aLaidOutTile('dragged', { top: 400 });
  return {
    tiles: [a.t, b.t],
    _nodes: new Map([['target', a.node], ['dragged', b.node]]),
    nodes: { target: a.node, dragged: b.node },
    tileOf: { target: a.t, dragged: b.t },
  };
}

const under = (self, x, y) =>
  Board.prototype._tileUnder.call(self, { clientX: x, clientY: y },
    self.nodes.dragged);

test('dropping on the tab strip of a tile docks into it', () => {
  const self = aLaidOutBoard();
  assert.strictEqual(under(self, 250, 10), self.tileOf.target);
});

test('dropping on the body of a tile does not dock', () => {
  // The one that matters: this is most of a full board, and it used to
  // dock, so moving a card anywhere kept turning it into a tab.
  const self = aLaidOutBoard();
  assert.strictEqual(under(self, 250, 120), null);
  assert.strictEqual(under(self, 250, 199), null);
});

test('the strip stays a usable target on a short tile', () => {
  // A header can be thinner than a finger. The band has a floor, so a tile
  // whose header is tiny is still droppable on.
  const self = aLaidOutBoard();
  self.nodes.target.querySelector('.tile-head')
    .setRect({ left: 100, top: 0, width: 300, height: 6 });
  assert.strictEqual(under(self, 250, 30), self.tileOf.target);
});

test('a tile never docks into itself', () => {
  const self = aLaidOutBoard();
  self.nodes.dragged.setRect({ left: 100, top: 0, width: 300, height: 200 });
  assert.strictEqual(under(self, 250, 10), self.tileOf.target,
    'the dragged tile is under the cursor by definition');
});

test('a popped-out tile is not a dock target', () => {
  const self = aLaidOutBoard();
  self.tileOf.target.popped = true;
  assert.strictEqual(under(self, 250, 10), null);
});

test('a drop beside the board docks into nothing', () => {
  const self = aLaidOutBoard();
  assert.strictEqual(under(self, 20, 10), null);
  assert.strictEqual(under(self, 900, 10), null);
});


/* ---- moving a tile without a pointer ---------------------------------
 * Dragging was the only way to arrange the board, so anybody not using a
 * pointer could add cards and never place them.
 */
function aKeyedTile(over = {}) {
  const node = new El('section');
  const head = new El('header');
  head.className = 'tile-head';
  node.appendChild(head);
  const t = Object.assign({ uid: 't1', x: 2, y: 3, w: 4, h: 3, active: 'c1',
    cards: [{ uid: 'c1', card: 'spots', title: 'Spots' }] }, over);
  const saved = [];
  const self = {
    cols: 12, rowPx: 80, gap: 10, tiles: [t],
    _nodes: new Map([['t1', node]]),
    _place() {}, _resolve() {}, reflow() {},
    onChange: () => saved.push(1),
    _sayPlace: Board.prototype._sayPlace,
    // How small it may go: the tile's own floor, raised by a card's `fit`.
    cellW: 90,
    fitOf: (id) => (over.fits || {})[id] || null,
    minOf: Board.prototype.minOf,
    colsFor: Board.prototype.colsFor,
    rowsFor: Board.prototype.rowsFor,
  };
  delete t.fits;
  Board.prototype._wireKeys.call(self, t, node);
  return { t, head, saved };
}

const press = (k, ev = {}) => ({ key: k, ...ev });

test('the arrow keys move a tile', () => {
  const k = aKeyedTile();
  k.head.fire('keydown', press('ArrowRight'));
  assert.strictEqual(k.t.x, 3);
  k.head.fire('keydown', press('ArrowDown'));
  assert.strictEqual(k.t.y, 4);
  k.head.fire('keydown', press('ArrowLeft'));
  k.head.fire('keydown', press('ArrowUp'));
  assert.deepStrictEqual([k.t.x, k.t.y], [2, 3], 'it did not come back');
});

test('shift and the arrows resize it', () => {
  const k = aKeyedTile();
  k.head.fire('keydown', press('ArrowRight', { shiftKey: true }));
  assert.strictEqual(k.t.w, 5);
  assert.strictEqual(k.t.x, 2, 'resizing moved it');
  k.head.fire('keydown', press('ArrowDown', { shiftKey: true }));
  assert.strictEqual(k.t.h, 4);
});

test('a tile cannot be walked off the board', () => {
  const k = aKeyedTile({ x: 0, y: 0 });
  k.head.fire('keydown', press('ArrowLeft'));
  k.head.fire('keydown', press('ArrowUp'));
  assert.deepStrictEqual([k.t.x, k.t.y], [0, 0]);
});

test('a tile cannot be made narrower than its card allows', () => {
  const k = aKeyedTile({ w: 3, min_w: 3 });
  k.head.fire('keydown', press('ArrowLeft', { shiftKey: true }));
  assert.strictEqual(k.t.w, 3);
});

test('nor smaller than its card needs to show without scrolling', () => {
  // The floor comes from the card's `fit`, in pixels, and is worked out
  // when the key is pressed - `min_w` on the tile does not survive a reload.
  // At 90 px columns 560 px is 6 of them, and 390 px is 5 rows.
  const k = aKeyedTile({ w: 6, h: 5, fits: { spots: { w: 560, h: 390 } } });
  k.head.fire('keydown', press('ArrowLeft', { shiftKey: true }));
  k.head.fire('keydown', press('ArrowUp', { shiftKey: true }));
  assert.deepStrictEqual([k.t.w, k.t.h], [6, 5]);
  k.head.fire('keydown', press('ArrowRight', { shiftKey: true }));
  assert.strictEqual(k.t.w, 7, 'growing it still works');
});

test('every move is saved', () => {
  // A board that forgets what the keyboard did is worse than one that
  // cannot be moved by keyboard at all.
  const k = aKeyedTile();
  k.head.fire('keydown', press('ArrowRight'));
  assert.strictEqual(k.saved.length, 1);
});

test('the arrows belong to the buttons inside the header', () => {
  const k = aKeyedTile();
  const btn = new El('button');
  k.head.fire('keydown', { key: 'ArrowRight', target: btn });
  assert.strictEqual(k.t.x, 2, 'it moved the tile from inside a button');
});

test('other keys are left alone', () => {
  const k = aKeyedTile();
  k.head.fire('keydown', press('Enter'));
  k.head.fire('keydown', press('a'));
  assert.deepStrictEqual([k.t.x, k.t.y], [2, 3]);
  assert.strictEqual(k.saved.length, 0);
});

test('the header says where the tile is', () => {
  const k = aKeyedTile();
  k.head.fire('keydown', press('ArrowRight'));
  const said = k.head.getAttribute('aria-label');
  assert.ok(/column 4/.test(said), `said: ${said}`);
  assert.ok(/row 4/.test(said), `said: ${said}`);
  assert.ok(/Arrow keys/.test(said), 'it does not say what the keys do');
});

/* ---- room to drag into ------------------------------------------------ */
test('the board grows under a tile dragged past the bottom', () => {
  const spacer = new El('div');
  spacer.style.height = '100px';
  const self = { gap: 10, rowPx: 80, _spacer: spacer };
  Board.prototype._growExtent.call(self, { y: 10, h: 3 });
  assert.strictEqual(spacer.style.height, `${10 * 2 + 13 * 90}px`);
});

test('it never shrinks mid-drag', () => {
  // Shrinking would move the ground under the tile being carried.
  const spacer = new El('div');
  spacer.style.height = '5000px';
  const self = { gap: 10, rowPx: 80, _spacer: spacer };
  Board.prototype._growExtent.call(self, { y: 0, h: 1 });
  assert.strictEqual(spacer.style.height, '5000px');
});
