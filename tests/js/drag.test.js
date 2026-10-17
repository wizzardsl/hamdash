/* Cancelling a drag, under `node --test`.
 *
 *   node --test 'tests/js/*.test.js'
 *
 * Escape has to put a tile back exactly, and - the part worth guarding -
 * must leave nothing behind it: no dock, no pop-out, no save. Dropping is
 * the step that is hard to undo, so the escape hatch is the one that has
 * to work.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const JS = path.join(__dirname, '..', '..', 'hamdash', 'static', 'js');
const L = require(path.join(JS, 'layout.js'));

/* A DOM small enough to read, covering only what _drag touches. */
class El {
  constructor(tag) {
    this.tagName = (tag || 'div').toUpperCase();
    this.children = [];
    this.parent = null;
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this._classes = new Set();
    this.classList = {
      add: (...c) => c.forEach((x) => this._classes.add(x)),
      remove: (...c) => c.forEach((x) => this._classes.delete(x)),
      contains: (c) => this._classes.has(c),
      toggle: (c, on) => (on ? this._classes.add(c) : this._classes.delete(c)),
    };
  }
  get className() { return [...this._classes].join(' '); }
  set className(v) { this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  setPointerCapture() { throw new Error('no active pointer'); }  // as in a test
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) {
    this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
  }
  fire(type, ev) { (this.listeners[type] || []).forEach((f) => f({ ...ev })); }
  getBoundingClientRect() {
    return this.rect || { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 };
  }
  querySelector() { return null; }
  querySelectorAll() { return []; }
}

const win = {
  HDLayout: L,
  listeners: {},
  addEventListener(type, fn) { (win.listeners[type] ||= []).push(fn); },
  removeEventListener(type, fn) {
    win.listeners[type] = (win.listeners[type] || []).filter((f) => f !== fn);
  },
  press(key) { (win.listeners.keydown || []).forEach((f) => f({ key, preventDefault() {} })); },
  requestAnimationFrame() { return 0; },
  cancelAnimationFrame() {},
};
const document = { createElement: (t) => new El(t) };
// eslint-disable-next-line no-new-func
new Function('window', 'document', 'requestAnimationFrame', 'cancelAnimationFrame',
  fs.readFileSync(path.join(JS, 'grid.js'), 'utf8'))(
  win, document, win.requestAnimationFrame, win.cancelAnimationFrame);
const Board = win.Board;

function aDrag() {
  const node = new El('section');
  node.className = 'tile';
  const t = { uid: 't1', x: 2, y: 3, w: 4, h: 3, active: 'c1',
    cards: [{ uid: 'c1', card: 'path_map' }] };
  const saved = [];
  const docked = [];
  const popped = [];
  const hints = [];
  const self = {
    el: Object.assign(new El('main'), {
      rect: { left: 0, top: 0, width: 1200, height: 800, right: 1200, bottom: 800 },
      scrollTop: 0,
    }),
    cols: 12, rowPx: 80, gap: 10, cellW: 90,
    tiles: [t],
    _nodes: new Map([['t1', node]]),
    _place() {},
    _resolve() {},
    _growExtent() {},
    _tileUnder() { return null; },
    reflow() {},
    onChange: (tiles) => saved.push(JSON.parse(JSON.stringify(tiles))),
    dock: (...a) => docked.push(a),
    popOut: (...a) => { popped.push(a); return Promise.resolve(); },
    onError() {},
    onHint: (what) => hints.push(what),
    onDocked: () => hints.push('docked'),
  };
  Board.prototype._drag.call(self, {
    button: 0, clientX: 300, clientY: 300, pointerId: 1, preventDefault() {},
  }, t, node, 'move');
  return { self, t, node, saved, docked, popped, hints };
}

test('a drag that is escaped puts the tile back', () => {
  const d = aDrag();
  node_move(d, 700, 120);
  assert.notDeepStrictEqual([d.t.x, d.t.y], [2, 3], 'the drag did nothing');
  win.press('Escape');
  assert.deepStrictEqual([d.t.x, d.t.y, d.t.w, d.t.h], [2, 3, 4, 3]);
});

test('an escaped drag saves nothing', () => {
  // The layout is written on every drop. A cancelled drag is not a drop.
  const d = aDrag();
  node_move(d, 700, 120);
  win.press('Escape');
  d.node.fire('pointerup', {});     // the button comes up after Escape
  assert.deepStrictEqual(d.saved, [], 'it wrote the layout anyway');
});

test('an escaped drag does not dock', () => {
  const d = aDrag();
  d.self._tileUnder = () => ({ uid: 'other' });
  node_move(d, 700, 120);
  win.press('Escape');
  d.node.fire('pointerup', {});
  assert.deepStrictEqual(d.docked, [], 'it docked into the tile underneath');
});

test('an escaped drag does not pop the card out', () => {
  const d = aDrag();
  node_move(d, 300, -200);          // well above the board: a pop-out drag
  win.press('Escape');
  d.node.fire('pointerup', {});
  assert.deepStrictEqual(d.popped, [], 'it popped out anyway');
});

test('escape after the drag has ended does nothing', () => {
  const d = aDrag();
  node_move(d, 700, 120);
  d.node.fire('pointerup', {});
  const after = [d.t.x, d.t.y];
  win.press('Escape');
  assert.deepStrictEqual([d.t.x, d.t.y], after, 'it moved a tile it had let go of');
});

function node_move(d, x, y) {
  d.node.fire('pointermove', { clientX: x, clientY: y });
}


test('the first real move offers the hint, once', () => {
  // It used to read `t.x` before the line that assigns it, so the tile was
  // compared with itself and the hint waited for a second pointermove.
  const d = aDrag();
  node_move(d, 700, 300);
  assert.deepStrictEqual(d.hints, ['drag'], 'not said on the first move');
  node_move(d, 760, 320);
  assert.deepStrictEqual(d.hints, ['drag'], 'it said it twice');
});

test('a drag that never leaves the spot says nothing', () => {
  const d = aDrag();
  node_move(d, 302, 301);           // a wobble, not a drag
  assert.deepStrictEqual(d.hints, []);
});

test('docking says so, so the hint can stop', () => {
  const d = aDrag();
  d.self._tileUnder = () => ({ uid: 'other' });
  node_move(d, 700, 300);
  d.node.fire('pointerup', {});
  assert.ok(d.hints.includes('docked'), 'nothing marked it as learned');
});
