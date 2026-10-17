/* The map's wheel, under `node --test`.
 *
 *   node --test 'tests/js/*.test.js'
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

/* ---- world copies ------------------------------------------------------
 *
 * The flat map used to draw exactly three worlds with `maxBounds` stopping
 * the pan at 1.5 either side. That is a wall: drag far enough west and the
 * map simply refuses, where a tile basemap repeats for ever. The span
 * follows the view now, so panning is unbounded and the cost is flat.
 */

test('a view of one world needs the copies it touches', () => {
  // zoom 1 showing -180..180
  assert.deepStrictEqual(M.worldSpan(-180, 180), { lo: -1, hi: 1 });
});

test('the span follows the view east, it does not stretch to reach it', () => {
  // Eight worlds east. Growing-only was the first version and it leaked:
  // four drags reached nine worlds and 630 land paths, never given back.
  const far = M.worldSpan(2000, 2400);
  assert.ok(far.lo >= 5, `lo ${far.lo} is still anchored near zero`);
  assert.ok(far.hi - far.lo <= 3, `span of ${far.hi - far.lo} worlds is leaking`);
});

test('the span follows the view west too', () => {
  const far = M.worldSpan(-2400, -2000);
  assert.ok(far.hi <= -5, `hi ${far.hi} is still anchored near zero`);
  assert.ok(far.hi - far.lo <= 3);
});

test('a wider view needs more copies', () => {
  const near = M.worldSpan(-180, 180);
  const wide = M.worldSpan(-900, 900);
  assert.ok(wide.hi - wide.lo > near.hi - near.lo);
});

test('the span is never empty', () => {
  const s = M.worldSpan(10, 20);        // a sliver inside one world
  assert.ok(s.hi >= s.lo);
});

test('a junk bound falls back to the three it always drew', () => {
  // Leaflet can hand back Infinity before the map has a size.
  assert.deepStrictEqual(M.worldSpan(NaN, NaN), { lo: -1, hi: 1 });
  assert.deepStrictEqual(M.worldSpan(-Infinity, Infinity), { lo: -1, hi: 1 });
});

/* ---- D-RAP shares the ramp, with its own full scale --------------------
 *
 * The aurora is a percentage and D-RAP is megahertz. That is the only
 * difference between them, so they share `fieldRamp` and differ in what
 * counts as full strength.
 */

test('D-RAP: an ordinary disturbed day is clearly visible', () => {
  // 5 to 15 MHz is a normal disturbed day; a big flare reaches the mid 30s.
  assert.ok(M.drapRamp(5, STOPS)[3] >= 80, 'mild absorption vanished');
  assert.ok(M.drapRamp(15, STOPS)[3] >= 160, 'a disturbed day looks mild');
});

test('D-RAP: quiet is transparent, which is the normal case', () => {
  // Zero over the whole planet is the good news, not a failure to load.
  assert.strictEqual(M.drapRamp(0, STOPS)[3], 0);
});

test('D-RAP: a severe event still reads as worse than a mild one', () => {
  assert.ok(M.drapRamp(30, STOPS)[3] > M.drapRamp(10, STOPS)[3]);
});

test('the two fields use the same ramp and differ only in full scale', () => {
  assert.deepStrictEqual(M.auroraRamp(25, STOPS), M.fieldRamp(25, M.AURORA_FULL, STOPS));
  assert.deepStrictEqual(M.drapRamp(15, STOPS), M.fieldRamp(15, M.DRAP_FULL, STOPS));
  // Same fraction of full scale, same pixel - whatever the units are.
  assert.deepStrictEqual(M.auroraRamp(M.AURORA_FULL / 2, STOPS),
                         M.drapRamp(M.DRAP_FULL / 2, STOPS));
});
