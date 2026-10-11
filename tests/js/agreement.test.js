/* The board's normaliser must agree with the host's.
 *
 * Both rewrite layout.json - the board saves what it holds, the host
 * normalises what it is sent - so a disagreement means a tile saved in a
 * shape the other side repairs differently, and a card that comes back in
 * the wrong box or without its settings.
 *
 * The contract is a file. `layouts.expected.json` is generated from the host
 * (`python tools/layout_golden.py`), which makes the host the reference and
 * leaves this side to agree or fail.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const L = require(path.join(ROOT, 'hamdash', 'static', 'js', 'layout.js'));
const read = (p) => JSON.parse(fs.readFileSync(path.join(ROOT, p), 'utf8'));

const CASES = read('tests/fixtures/layouts.json').cases;
const GOLDEN = read('tests/fixtures/layouts.expected.json');

/* A uid the normaliser invented - a duplicate being made distinct - is one
   neither side can agree on and neither should. What matters is that it was
   replaced, so it is masked, and `active` becomes a position. */
function canonical(tiles, before) {
  const known = new Set();
  (function walk(v) {
    if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === 'object') {
      if (v.uid) known.add(String(v.uid));
      Object.values(v).forEach(walk);
    }
  }(before));
  const mask = (u) => (known.has(u) ? u : '<new>');
  return tiles.map((t) => ({
    ...t,
    uid: mask(t.uid),
    cards: t.cards.map((c) => ({ ...c, uid: mask(c.uid) })),
    active: `#${t.cards.findIndex((c) => c.uid === t.active)}`,
  }));
}

/* Key order differs between JSON.stringify and json.dumps, and nothing
   downstream cares, so compare as sorted-key structures. */
const sortKeys = (v) => {
  if (Array.isArray(v)) return v.map(sortKeys);
  if (v && typeof v === 'object') {
    return Object.keys(v).sort().reduce((o, k) => {
      o[k] = sortKeys(v[k]);
      return o;
    }, {});
  }
  return v;
};

test('the fixtures and the golden file are in step', () => {
  assert.strictEqual(CASES.length, GOLDEN.length,
    'run: python tools/layout_golden.py');
});

CASES.forEach((c, i) => {
  test(`agrees with the host: ${c.why}`, () => {
    const mine = canonical(L.normalise(c.tiles), c.tiles);
    assert.deepStrictEqual(sortKeys(mine), sortKeys(GOLDEN[i].tiles),
      `layout.js disagrees with config.normalise_layout for: ${c.why}`);
  });
});

test('normalising is idempotent, right down to the text', () => {
  // Compared as text, not as values: they matched as values all along while
  // the keys came out in a different order each pass, so every save rewrote
  // layout.json with the lines shuffled.
  CASES.forEach((c) => {
    const once = L.normalise(c.tiles);
    assert.strictEqual(JSON.stringify(L.normalise(once)),
      JSON.stringify(once), c.why);
  });
});

test('a tile comes out with the same keys in the same order as the host', () => {
  const want = ['uid', 'x', 'y', 'w', 'h', 'active', 'popped', 'cards'];
  CASES.forEach((c) => {
    L.normalise(c.tiles).forEach((t) => {
      assert.deepStrictEqual(Object.keys(t).filter((k) => want.includes(k)),
        want, c.why);
      t.cards.forEach((card) => {
        const keys = Object.keys(card);
        assert.deepStrictEqual(keys.slice(0, 2), ['uid', 'card'], c.why);
        assert.strictEqual(keys[keys.length - 1], 'settings', c.why);
      });
    });
  });
});
