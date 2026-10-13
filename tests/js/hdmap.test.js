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
const M = win.HDMap;

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

/* ---- the aurora ramp --------------------------------------------------
 *
 * The overlay is an image now, so intensity becomes a pixel: a colour and
 * an alpha together. Alpha is the half that was missing before - colour
 * alone has to stop somewhere, and wherever it stops is a visible edge.
 */

const STOPS = [[86, 182, 168], [233, 161, 59], [217, 96, 74]];  // the dark theme

test('aurora: nothing is fully transparent', () => {
  assert.strictEqual(M.auroraRamp(0, STOPS)[3], 0);
});

test('aurora: alpha rises with intensity', () => {
  const a = [0, 5, 10, 20, 35, 50].map((p) => M.auroraRamp(p, STOPS)[3]);
  for (let i = 1; i < a.length; i++) {
    assert.ok(a[i] > a[i - 1], `alpha fell from ${a[i - 1]} to ${a[i]}`);
  }
});

test('aurora: a quiet day is still visible', () => {
  // OVATION peaks near 28% on an ordinary day, and most of the field is
  // well under that. A straight alpha ramp makes all of it invisible,
  // which is what the gamma is for.
  // Measured, not guessed at: gamma 0.6 puts 5% at alpha 64 and 28% at
  // 177, which under the layer's 0.75 opacity is a fifth and two thirds.
  assert.ok(M.auroraRamp(5, STOPS)[3] >= 55, 'faint edge vanished');
  assert.ok(M.auroraRamp(28, STOPS)[3] >= 170, 'a quiet peak reads as faint');
});

test('aurora: the faint edge is fainter than the middle', () => {
  assert.ok(M.auroraRamp(3, STOPS)[3] < M.auroraRamp(25, STOPS)[3]);
});

test('aurora: it saturates rather than running off the end', () => {
  const full = M.auroraRamp(M.AURORA_FULL, STOPS);
  assert.deepStrictEqual(M.auroraRamp(100, STOPS), full);
  assert.deepStrictEqual(M.auroraRamp(999, STOPS), full);
  assert.strictEqual(full[3], 255);
});

test('aurora: the colour walks all three stops, continuously', () => {
  // The old version had three hard steps, so two contour lines showed
  // through the oval. Neighbouring intensities must differ by a little,
  // never by a jump.
  let biggest = 0;
  for (let p = 0; p < M.AURORA_FULL; p += 1) {
    const a = M.auroraRamp(p, STOPS), b = M.auroraRamp(p + 1, STOPS);
    for (let i = 0; i < 3; i++) biggest = Math.max(biggest, Math.abs(a[i] - b[i]));
  }
  assert.ok(biggest <= 12, `colour jumps by ${biggest} between neighbours`);
  // and it really does reach the far stop
  assert.deepStrictEqual(M.auroraRamp(M.AURORA_FULL, STOPS).slice(0, 3), STOPS[2]);
  assert.deepStrictEqual(M.auroraRamp(0, STOPS).slice(0, 3), STOPS[0]);
});

test('aurora: a negative or junk reading does not escape the scale', () => {
  assert.strictEqual(M.auroraRamp(-5, STOPS)[3], 0);
  assert.ok(M.auroraRamp(NaN, STOPS).every((v) => Number.isFinite(v)));
});

test('hex2rgb reads the shapes a stylesheet actually yields', () => {
  assert.deepStrictEqual(M.hex2rgb('#56b6a8'), [86, 182, 168]);
  assert.deepStrictEqual(M.hex2rgb(' #56b6a8 '), [86, 182, 168]);
  assert.deepStrictEqual(M.hex2rgb('#abc'), [170, 187, 204]);
  // Not a colour it can parse: white, which is visible, rather than NaN,
  // which paints nothing and reads as the overlay being broken.
  assert.deepStrictEqual(M.hex2rgb('rebeccapurple'), [255, 255, 255]);
});
