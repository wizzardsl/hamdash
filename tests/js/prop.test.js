/* What a path will carry, from measured soundings.
 *
 *   node --test tests/js/
 *
 * `prop.js` has no DOM and no network for the same reason `cw.py` has
 * none: a propagation number that is 30% out still looks exactly like a
 * propagation number. The only way to know it is right is to drive it
 * with values whose answer is known from somewhere else.
 *
 * The anchor is the sounders themselves. kc2g publish `fof2`, `md`
 * (M(3000)F2) and `mufd` for each station, and `mufd = fof2 * md`. So
 * the geometry here can be checked against a number computed by somebody
 * else from the same ionogram - which is a far better test than any
 * figure invented in this file.
 */
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const P = require(path.join(__dirname, '..', '..', 'plugins', 'path_map',
  'prop.js'));

const NOW = Date.parse('2026-10-13T05:00:00Z');
const fresh = (over) => Object.assign({
  name: 'Austin, TX, USA', lat: 30.4, lon: -97.7,
  fof2: 8.6, md: 3.352, hmf2: 241.8,
  time: '2026-10-13T04:40:00',
}, over);

test('the secant law matches the sounders own M(3000)F2', () => {
  // Real M(3000)F2 sits between about 2.8 and 3.5. A flat-Earth secant
  // has no such ceiling and runs away at long distances, which is the
  // mistake this checks for.
  const at3000 = P.secant(3000, 300);
  assert.ok(at3000 > 2.8 && at3000 < 3.5,
    `3000 km gave ${at3000.toFixed(2)}, outside what sounders report`);
});

test('it reproduces a published MUF from a published foF2', () => {
  // Austin, measured: fof2 8.6, md 3.352, and their own mufd 28.827.
  const s = fresh();
  const mine = P.hopMuf(s, 3000);
  assert.ok(Math.abs(mine - 28.827) < 0.01,
    `got ${mine.toFixed(3)} for their 28.827`);
});

test('a shorter hop carries less', () => {
  // The whole point of the secant law: a steep hop reflects less.
  const s = fresh();
  assert.ok(P.hopMuf(s, 500) < P.hopMuf(s, 1500));
  assert.ok(P.hopMuf(s, 1500) < P.hopMuf(s, 3000));
});

test('the factor levels off rather than running away', () => {
  // A ray cannot leave below zero elevation, so there is a ceiling.
  const far = P.secant(4000, 300) / P.secant(3000, 300);
  assert.ok(far < 1.1, `4000 km is ${far.toFixed(2)}x of 3000 km`);
});

test('reflection height matters enough to take from the sounder', () => {
  // 250 vs 400 km moves the answer by about 30%: not a detail.
  const lo = P.secant(3000, 250), hi = P.secant(3000, 400);
  assert.ok(lo / hi > 1.25, `only ${(lo / hi).toFixed(2)}x apart`);
});

test('hops: one under 4000 km, more beyond', () => {
  assert.strictEqual(P.hopsFor(100), 1);
  assert.strictEqual(P.hopsFor(3999), 1);
  assert.strictEqual(P.hopsFor(4001), 2);
  assert.strictEqual(P.hopsFor(12000), 3);
});

test('distance and midpoint agree with known geography', () => {
  const london = { lat: 51.5, lon: -0.1 };
  const ny = { lat: 40.7, lon: -74.0 };
  const km = P.distance(london, ny);
  assert.ok(Math.abs(km - 5570) < 60, `London-New York came out ${km|0} km`);
  const mid = P.along(london, ny, 0.5);
  // The great-circle midpoint is well north of the latitude average,
  // which is the whole reason paths are drawn as great circles.
  assert.ok(mid.lat > 52, `midpoint at ${mid.lat.toFixed(1)} is not on the arc`);
  assert.ok(mid.lon < -35 && mid.lon > -45);
});

test('control points: the middle for one hop, 1/6 and 5/6 beyond', () => {
  const a = { lat: 0, lon: 0 }, b = { lat: 0, lon: 20 };
  assert.strictEqual(P.controlPoints(a, b, 1).length, 1);
  const two = P.controlPoints(a, b, 2);
  assert.strictEqual(two.length, 2);
  assert.ok(Math.abs(two[0].lon - 20 / 6) < 0.2);
  assert.ok(Math.abs(two[1].lon - 20 * 5 / 6) < 0.2);
});

test('a stale sounding is history, not weather', () => {
  const old = fresh({ time: '2026-03-19T22:10:05' });   // a real stale one
  assert.strictEqual(P.nearest({ lat: 30, lon: -97 }, [old], NOW), null);
});

test('a sounder with no foF2 cannot be used', () => {
  const blind = fresh({ fof2: null });
  assert.strictEqual(P.nearest({ lat: 30, lon: -97 }, [blind], NOW), null);
});

test('the nearest usable sounder wins, not the nearest station', () => {
  const near = fresh({ name: 'near but stale', lat: 30, lon: -97,
                       time: '2026-03-19T22:10:05' });
  const far = fresh({ name: 'far but live', lat: 45, lon: -75 });
  const got = P.nearest({ lat: 30, lon: -97 }, [near, far], NOW);
  assert.strictEqual(got.name, 'far but live');
});

test('a path with nothing near it says so instead of guessing', () => {
  const me = { lat: 48, lon: -123 };
  const them = { lat: -40, lon: 175 };          // the Pacific
  const got = P.pathMuf(me, them, [], { now: NOW });
  assert.strictEqual(got.mhz, null);
  assert.match(got.why, /no ionosonde/);
});

test('the worst control point governs the path', () => {
  // A path is only as good as the worst place it has to bounce.
  const me = { lat: 30, lon: 0 };
  const them = { lat: 30, lon: 90 };            // long: two control points
  const strong = fresh({ name: 'strong', lat: 30, lon: 15, fof2: 12 });
  const weak = fresh({ name: 'weak', lat: 30, lon: 75, fof2: 4 });
  const got = P.pathMuf(me, them, [strong, weak], { now: NOW });
  assert.strictEqual(got.from.name, 'weak');
  assert.ok(got.mhz < P.hopMuf(strong, got.hopKm));
});

test('it reports how far the evidence was, and doubts it when far', () => {
  const me = { lat: 48, lon: -123 };
  const them = { lat: 35, lon: 139 };
  const distant = fresh({ name: 'a long way off', lat: 30.4, lon: -97.7 });
  const got = P.pathMuf(me, them, [distant], { now: NOW });
  assert.ok(got.mhz > 0);
  assert.ok(got.far > P.FAR_KM, 'should know the sounding is distant');
  assert.strictEqual(got.uncertain, true);
});

test('a nearby sounding is not flagged as doubtful', () => {
  const me = { lat: 30, lon: -98 };
  const them = { lat: 31, lon: -97 };
  const got = P.pathMuf(me, them, [fresh()], { now: NOW });
  assert.strictEqual(got.uncertain, false);
});

test('bands come from the host table, in the shape it really returns', () => {
  // `HD.bands()` gives {bands: [[start, end, name], ...]} - triples,
  // wrapped. The first version of this test invented objects with
  // `start_mhz` fields, passed against code that invented the same
  // thing, and threw the moment it met the real reply.
  const real = { bands: [[1.8, 2, '160m'], [3.5, 4, '80m'], [7, 7.3, '40m'],
                         [10.1, 10.15, '30m'], [14, 14.35, '20m'],
                         [28, 29.7, '10m']], names: [] };
  assert.deepStrictEqual(P.bandsBetween(real, 5, 15),
                         ['40m', '30m', '20m']);
});

test('a bare list works too, so a caller need not wrap it', () => {
  assert.deepStrictEqual(P.bandsBetween([[7, 7.3, '40m']], 5, 15), ['40m']);
});

test('a band straddling the edge of the window is still usable', () => {
  const real = { bands: [[14, 14.35, '20m'], [21, 21.45, '15m']] };
  assert.deepStrictEqual(P.bandsBetween(real, 5, 14.1), ['20m']);
});

test('no window means no bands rather than every band', () => {
  const real = { bands: [[14, 14.35, '20m']] };
  assert.deepStrictEqual(P.bandsBetween(real, 20, 10), []);
});

test('nothing at all is not an error', () => {
  assert.deepStrictEqual(P.bandsBetween(null, 5, 15), []);
  assert.deepStrictEqual(P.bandsBetween({}, 5, 15), []);
});
