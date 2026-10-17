/* The phone shell's ordering, under `node --test`.
 *
 *   node --test 'tests/js/*.test.js'
 *
 * `order` and `split` are the only judgement in `mobile.js` - everything
 * else is painting - so they are pure and tested here, the same way
 * `prop.js` and `layout.js` are. What goes in the bar is the whole of the
 * answer to "can this be used standing in a field": four slots, and the
 * operator asked for logging and spots to have two of them.
 *
 * Loaded the way a browser would, with the real `layout.js` behind it,
 * because `mobile.js` builds on the same model the board does.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const JS = path.join(__dirname, '..', '..', 'hamdash', 'static', 'js');
const L = require(path.join(JS, 'layout.js'));

const win = { HDLayout: L, document: { createElement: () => ({}) } };
// eslint-disable-next-line no-new-func
new Function('window', 'document', fs.readFileSync(path.join(JS, 'mobile.js'),
  'utf8'))(win, win.document);
const P = win.HDPhone;

/* A board as `layout.json` holds one: tiles, each a stack of cards. */
const tile = (y, ...ids) => ({
  uid: ids[0] + '-t', x: 0, y, w: 4, h: 3, active: ids[0],
  cards: ids.map((id) => ({ uid: id, card: id })),
});

const ids = (entries) => entries.map((c) => c.card);

test('logging and spots come first, whatever order the board is in', () => {
  const tiles = [tile(0, 'satellites'), tile(3, 'spots'), tile(6, 'logbook')];
  assert.deepStrictEqual(ids(P.order(tiles)).slice(0, 2), ['logbook', 'spots']);
});

test('the radio and the audio come after those two', () => {
  const tiles = [tile(0, 'rx_audio'), tile(3, 'rig_control'),
    tile(6, 'spots'), tile(9, 'logbook')];
  assert.deepStrictEqual(ids(P.order(tiles)),
    ['logbook', 'spots', 'rig_control', 'rx_audio']);
});

test('anything unranked keeps its board order', () => {
  // Not alphabetical, and not reversed: the order the operator arranged.
  const tiles = [tile(0, 'zulu'), tile(3, 'alpha'), tile(6, 'mike')];
  assert.deepStrictEqual(ids(P.order(tiles)), ['zulu', 'alpha', 'mike']);
});

test('a ranked card jumps ahead without shuffling the rest', () => {
  const tiles = [tile(0, 'zulu'), tile(3, 'alpha'), tile(6, 'logbook'),
    tile(9, 'mike')];
  assert.deepStrictEqual(ids(P.order(tiles)),
    ['logbook', 'zulu', 'alpha', 'mike']);
});

test('every card in a stack gets its own place in the bar', () => {
  // A tile is a box of cards (invariant 5a). On the board they share one
  // tile and a tab strip; on a phone there are no tabs, so each is its own
  // destination - a card docked into a stack must not become unreachable.
  const tiles = [tile(0, 'spots', 'decoder', 'path_map')];
  assert.deepStrictEqual(ids(P.order(tiles)),
    ['spots', 'decoder', 'path_map']);
});

test('five cards all fit, because the fifth slot is not needed for More', () => {
  const five = P.order([tile(0, 'a'), tile(3, 'b'), tile(6, 'c'),
    tile(9, 'd'), tile(12, 'e')]);
  const { bar, rest } = P.split(five);
  assert.strictEqual(bar.length, 5);
  assert.deepStrictEqual(rest, []);
});

test('six cards become four and a More', () => {
  const six = P.order([tile(0, 'a'), tile(3, 'b'), tile(6, 'c'),
    tile(9, 'd'), tile(12, 'e'), tile(15, 'f')]);
  const { bar, rest } = P.split(six);
  assert.strictEqual(bar.length, P.BAR);
  assert.strictEqual(rest.length, 2);
  // Nothing may be dropped on the floor: every card is reachable, in the
  // bar or behind More.
  assert.strictEqual(bar.length + rest.length, six.length);
});

test('what the operator asked for survives a crowded board', () => {
  // The real complaint this was built for: a board with everything on it,
  // opened on a phone. Logging and spots must still be one tap away.
  const tiles = [tile(0, 'satellites'), tile(3, 'psk_reporter'),
    tile(6, 'beacon_map'), tile(9, 'decoder'), tile(12, 'spots'),
    tile(15, 'web_embed'), tile(18, 'logbook'), tile(21, 'rig_control')];
  const { bar } = P.split(P.order(tiles));
  assert.deepStrictEqual(ids(bar).slice(0, 2), ['logbook', 'spots']);
  assert.ok(ids(bar).includes('rig_control'),
    'the radio should still reach the bar on a full board');
});

test('an empty board is not an error', () => {
  assert.deepStrictEqual(P.order([]), []);
  assert.deepStrictEqual(P.split([]), { bar: [], rest: [] });
});

/* ---- what counts as a screen the board does not fit on ---------------- */

/* A fresh copy of the module against a made-up window, because `wants()`
   reads the one it was loaded with. */
function wantsOn({ w, h, coarse }) {
  const sandbox = {
    HDLayout: L,
    document: win.document,
    innerWidth: w,
    innerHeight: h,
    addEventListener() {},
    matchMedia: (q) => ({
      matches: /max-width:\s*760px/.test(q) ? w <= 760
        : /pointer:\s*coarse/.test(q) ? !!coarse : false,
      addEventListener() {},
    }),
  };
  // eslint-disable-next-line no-new-func
  new Function('window', 'document', fs.readFileSync(path.join(JS, 'mobile.js'),
    'utf8'))(sandbox, win.document);
  return sandbox.HDPhone.wants();
}

test('a phone gets the phone shell', () => {
  assert.strictEqual(wantsOn({ w: 375, h: 812, coarse: true }), true);
});

test('a desk gets the board', () => {
  assert.strictEqual(wantsOn({ w: 1920, h: 1080, coarse: false }), false);
});

test('a phone turned sideways still gets the phone shell', () => {
  // 812px wide is not narrow, but 375px tall with a finger for a pointer
  // is no place to drag a tile by a 28px header.
  assert.strictEqual(wantsOn({ w: 812, h: 375, coarse: true }), true);
});

test('a short desktop window is still a desk', () => {
  // The same height, with a mouse. Catching this one as a phone would
  // take the board away from anybody who docks a window to half a screen.
  assert.strictEqual(wantsOn({ w: 1440, h: 420, coarse: false }), false);
});

test('a tablet in landscape gets the board', () => {
  assert.strictEqual(wantsOn({ w: 1024, h: 768, coarse: true }), false);
});

test('a width of zero is not a narrow screen', () => {
  // A window that has not been laid out yet - a hidden pane, a tab being
  // restored - matches every `max-width` query there is. Measured: the
  // board booted into the phone shell on a 1280px window because
  // `innerWidth` was 0 for the moment the question was asked, and
  // `wants()` answered correctly a second later with the wrong shell
  // already built. "Narrow" and "nothing measured yet" are not the same
  // answer and must not look like one.
  assert.strictEqual(wantsOn({ w: 0, h: 0, coarse: false }), false);
});
