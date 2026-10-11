/* What a tile's body contains, under `node --test`.
 *
 *   node --test tests/js/
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
