/* The ITU zones overlay, under `node --test`.
 *
 *   node --test 'tests/js/*.test.js'
 *
 * The data is `hamdash/static/vendor/ituzones.js`, built by
 * tools/itu_zones.py from HB9HIL's GeoJSON (MIT). A boundary that is a
 * degree out still draws a perfectly plausible map, so the test that
 * matters is the one that puts real places in their real zones - checked
 * against the ITU's own assignments, not against numbers read off this
 * data, which would only test the data against itself.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8');
const w = {};
// eslint-disable-next-line no-new-func
new Function('window', read('hamdash', 'static', 'vendor', 'ituzones.js'))(w);
const ZONES = w.ITU_ZONES;

const closed = (r) => r.length > 2 && r[0][0] === r[r.length - 1][0]
  && r[0][1] === r[r.length - 1][1];

function inside([x, y], ring) {
  let c = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c;
  }
  return c;
}

/* Which zones a place falls in: the data is unwrapped across the date line,
   so a place is also tried one world either side. */
const zonesAt = (lon, lat) => ZONES.filter((z) => z.lines.some((r) => closed(r)
  && [0, -360, 360].some((d) => inside([lon + d, lat], r)))).map((z) => z.n);

test('all ninety zones, once each', () => {
  assert.strictEqual(ZONES.length, 90);
  assert.deepStrictEqual(ZONES.map((z) => z.n), Array.from({ length: 90 }, (_, i) => i + 1));
  ZONES.forEach((z) => {
    assert.ok(z.lines.length > 0, `zone ${z.n} has nothing to draw`);
    assert.ok(Math.abs(z.at[0]) <= 90, `zone ${z.n}'s label is off the planet`);
  });
});

test('places are in their ITU zones', () => {
  // Each exactly one zone: two would be a boundary drawn through a city.
  const want = {
    'Vancouver, VE7': [[-123.1, 49.25], 2],
    'New York, W2': [[-74.0, 40.7], 8],
    'London, G': [[-0.1, 51.5], 27],
    'Moscow, UA3': [[37.6, 55.75], 29],
    'Tokyo, JA1': [[139.7, 35.7], 45],
    'Johannesburg, ZS6': [[28.0, -26.2], 57],
    'Sydney, VK2': [[151.2, -33.9], 59],
    'Buenos Aires, LU': [[-58.4, -34.6], 14],
  };
  for (const [place, [[lon, lat], zone]] of Object.entries(want)) {
    assert.deepStrictEqual(zonesAt(lon, lat), [zone], place);
  }
});

test('nothing is drawn along the date line or across the world', () => {
  // A segment that jumps more than half the world, or runs along +-180, is a
  // seam: drawn, it is a line straight across the map, or a stripe at the
  // date line near the poles.
  ZONES.forEach((z) => z.lines.forEach((ln) => {
    for (let i = 1; i < ln.length; i++) {
      const [a, b] = [ln[i - 1], ln[i]];
      assert.ok(Math.abs(a[0] - b[0]) <= 180, `zone ${z.n} jumps the world`);
      assert.ok(!(Math.abs(a[0]) === 180 && a[0] === b[0]), `zone ${z.n} draws on the date line`);
    }
  }));
});

test('the polar bands keep their parallels, with points all the way round', () => {
  // The source writes a whole parallel as a jump from +180 to -180; treated
  // as a seam the polar zones drew nothing at all, which is how this was
  // first built. Two points apart it would be a chord on the azimuthal map.
  for (const [n, lats] of [[74, [-80, -85]], [75, [80, 85]]]) {
    const z = ZONES.find((x) => x.n === n);
    const got = z.lines.map((ln) => ln[0][1]).sort((a, b) => a - b);
    assert.deepStrictEqual(got, [...lats].sort((a, b) => a - b), `zone ${n}`);
    z.lines.forEach((ln) => {
      assert.deepStrictEqual([ln[0][0], ln[ln.length - 1][0]], [-180, 180]);
      assert.ok(ln.length >= 70, `zone ${n}'s parallel is ${ln.length} points`);
    });
  }
});

test('the bundle is small, and says whose it is', () => {
  const src = read('hamdash', 'static', 'vendor', 'ituzones.js');
  assert.ok(src.length < 60_000, `${src.length} bytes`);
  assert.match(src, /HB9HIL/);
  assert.match(src, /MIT/);
  assert.match(read('hamdash', 'static', 'vendor', 'ITU-ZONES-LICENSE.txt'), /HB9HIL/);
});

/* ---- the drawing ------------------------------------------------------ */
const win = { addEventListener() {} };
const doc = { body: {}, documentElement: {} };
// eslint-disable-next-line no-new-func
new Function('window', 'document', 'getComputedStyle', 'L', 'proj4',
  read('hamdash', 'static', 'js', 'hdmap.js'))(
  win, doc, () => ({ getPropertyValue: () => '' }), undefined, undefined);
const M = win.HDMap;

test('a long edge gets points along it, and keeps its ends', () => {
  const got = M.densifyLine([[-60, 30], [-20, 30], [-20, 40]], 2);
  assert.deepStrictEqual(got[0], [-60, 30]);
  assert.deepStrictEqual(got[got.length - 1], [-20, 40]);
  for (let i = 1; i < got.length; i++) {
    const step = Math.max(Math.abs(got[i][0] - got[i - 1][0]),
                          Math.abs(got[i][1] - got[i - 1][1]));
    assert.ok(step <= 2 + 1e-9, `a ${step} degree gap`);
  }
  // Every corner survives: [-20, 30] is where the line turns.
  assert.ok(got.some(([x, y]) => x === -20 && y === 30));
});

test('both map cards offer it, and it never takes the mouse', () => {
  const src = read('hamdash', 'static', 'js', 'hdmap.js');
  assert.match(src, /'ITU zones': groups\.itu/);
  // Drawn only while it is switched on.
  assert.match(src, /if \(map\.hasLayer\(groups\.itu\)\) drawItu\(\); else groups\.itu\.clearLayers\(\);/);
  // On the inert canvas, and the labels too: a zone line must not stop a
  // click reaching a spot on the PSK map.
  const draw = src.slice(src.indexOf('function drawItu()'));
  const body = draw.slice(0, draw.indexOf('\n    }\n'));
  assert.match(body, /renderer: canvas/);
  assert.strictEqual((body.match(/interactive: false/g) || []).length, 2);
  for (const card of ['path_map', 'psk_reporter']) {
    assert.match(read('plugins', card, 'index.html'),
      /<script src="\/static\/vendor\/ituzones\.js"><\/script>/, card);
  }
});
