/* The map's wheel, under `node --test`.
 *
 *   node --test tests/js/
 *
 * A map inside a scrollable dashboard has to tell two gestures apart. The
 * browser already does the hard part: a trackpad **pinch** arrives as a
 * wheel event with `ctrlKey` set, and a two-finger **scroll** arrives
 * without it. So the decision is `e.ctrlKey`, and what is left to get wrong
 * is the arithmetic - which is what this covers, because it is the part
 * that cannot be seen by looking at the map.
 *
 * The rest of the behaviour was measured in a browser rather than here:
 * with the pointer over a map card, a real scroll moved the board 107 -> 507
 * px and the map did not zoom at all.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const JS = path.join(__dirname, '..', '..', 'hamdash', 'static', 'js');

/* hdmap.js is a browser file that hangs itself on `window`. Leaflet is only
   reached from inside its functions, but it reads the theme off the page at
   load, so it needs a body to ask about and a getComputedStyle to ask. The
   stub answers nothing, which is what the fallbacks in `readTheme` are for. */
const win = { addEventListener() {} };
const doc = { body: {}, documentElement: {} };
const noStyle = { getPropertyValue: () => '' };
// eslint-disable-next-line no-new-func
new Function('window', 'document', 'getComputedStyle', 'L', 'proj4',
  fs.readFileSync(path.join(JS, 'hdmap.js'), 'utf8'))(
  win, doc, () => noStyle, undefined, undefined);
const { zoomSteps } = win.HDMap;

const PER = 50;        // px of pinch per zoom level, as hdmap.js has it

test('a small pinch earns nothing yet, and is remembered', () => {
  const got = zoomSteps(0, -20);
  assert.strictEqual(got.steps, 0);
  assert.strictEqual(got.acc, -20, 'the movement was thrown away');
});

test('pinching far enough zooms in', () => {
  assert.strictEqual(zoomSteps(0, -PER).steps, 1);
  assert.strictEqual(zoomSteps(0, -PER * 2).steps, 2);
});

test('spreading the other way zooms out', () => {
  assert.strictEqual(zoomSteps(0, PER).steps, -1);
  assert.strictEqual(zoomSteps(0, PER * 3).steps, -3);
});

test('what is left over carries into the next event', () => {
  // A trackpad sends a stream of small deltas, so a gesture is only ever
  // the sum of them. Dropping the remainder each time loses most of it.
  let acc = 0;
  let total = 0;
  for (let i = 0; i < 10; i++) {
    const got = zoomSteps(acc, -15);
    acc = got.acc;
    total += got.steps;
  }
  assert.strictEqual(total, 3, '150px of pinch should be three levels');
});

test('the leftover never grows past one level', () => {
  let acc = 0;
  for (let i = 0; i < 50; i++) acc = zoomSteps(acc, -37).acc;
  assert.ok(Math.abs(acc) < PER, `leftover ran away to ${acc}`);
});

test('a gesture that reverses nets out', () => {
  const inAndOut = zoomSteps(zoomSteps(0, -PER).acc, PER);
  assert.strictEqual(inAndOut.steps, -1);
  assert.strictEqual(inAndOut.acc, 0);
});

test('an exact level boundary counts once, not twice', () => {
  const got = zoomSteps(0, -PER);
  assert.strictEqual(got.steps, 1);
  assert.strictEqual(got.acc, 0);
});
