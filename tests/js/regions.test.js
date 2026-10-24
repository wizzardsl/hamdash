/* The ITU Regions overlay, under `node --test`.
 *
 *   node --test 'tests/js/*.test.js'
 *
 * The data is `hamdash/static/vendor/ituregions.js`, built by
 * tools/regions.py from the Radio Regulations' own lines A, B and C plus
 * the country exceptions. Lines only, no polygons, so what can be checked
 * here is that the lines run where the regulation puts them - well south of
 * every country, where nothing but the rule decides it.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8');
const w = {};
// eslint-disable-next-line no-new-func
new Function('window', read('hamdash', 'static', 'vendor', 'ituregions.js'))(w);
const R = w.ITU_REGIONS;

/* Longitudes where any line crosses a latitude, folded into -180..180. */
function crossings(lat) {
  const out = [];
  R.lines.forEach((ln) => {
    for (let i = 1; i < ln.length; i++) {
      const [x0, y0] = ln[i - 1];
      const [x1, y1] = ln[i];
      if ((y0 - lat) * (y1 - lat) > 0 || y0 === y1) continue;
      const x = x0 + ((lat - y0) * (x1 - x0)) / (y1 - y0);
      out.push(((x + 540) % 360) - 180);
    }
  });
  return out;
}
const near = (xs, want) => xs.some((x) => Math.abs(x - want) < 0.2);

test('small, and every point on the planet', () => {
  assert.ok(R.lines.length > 0 && R.lines.length < 40);
  const pts = R.lines.flat();
  assert.ok(pts.length < 2000, `${pts.length} points`);
  pts.forEach(([, lat]) => assert.ok(Math.abs(lat) <= 90));
  assert.ok(fs.statSync(path.join(ROOT, 'hamdash', 'static', 'vendor', 'ituregions.js')).size < 20000);
});

test('the three lines run where the Radio Regulations put them', () => {
  // South of every country only the lines decide: A on 60 E, B on 20 W,
  // C on 120 W - and nothing else crosses there.
  for (const lat of [-40, -60, -80]) {
    const xs = crossings(lat);
    assert.ok(near(xs, 60), `line A at ${lat}: ${xs}`);
    assert.ok(near(xs, -20), `line B at ${lat}: ${xs}`);
    assert.ok(near(xs, -120), `line C at ${lat}: ${xs}`);
    assert.strictEqual(xs.length, 3, `${lat}: ${xs}`);
  }
  // Line C runs along 10 N from 170 W to 120 W.
  const along = R.lines.some((ln) => ln.some((p, i) => i > 0 && p[1] === 10
    && ln[i - 1][1] === 10 && Math.min(p[0], ln[i - 1][0]) <= -169.9
    && Math.max(p[0], ln[i - 1][0]) >= -120.1));
  assert.ok(along, 'line C along 10 N');
});

test('one label per region and side, in the region it names', () => {
  const names = R.labels.map((l) => l.text).sort();
  assert.deepStrictEqual(names, ['Region 1', 'Region 1', 'Region 2', 'Region 2', 'Region 3', 'Region 3']);
});

test('both map cards offer it, and it never takes the mouse', () => {
  const src = read('hamdash', 'static', 'js', 'hdmap.js');
  assert.match(src, /'ITU regions': groups\.regions/);
  assert.match(src, /if \(map\.hasLayer\(groups\.regions\)\) drawRegions\(\); else groups\.regions\.clearLayers\(\);/);
  const draw = src.slice(src.indexOf('function drawRegions()'));
  const body = draw.slice(0, draw.indexOf('\n    }\n'));
  assert.match(body, /renderer: canvas/);
  assert.match(body, /densifyLine\(ln, 2\)/, 'curved on the azimuthal map');
  assert.strictEqual((body.match(/interactive: false/g) || []).length, 2);
  for (const card of ['path_map', 'psk_reporter']) {
    assert.match(read('plugins', card, 'index.html'),
      /<script src="\/static\/vendor\/ituregions\.js"><\/script>/, card);
  }
});
