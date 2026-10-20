/* Listen's output: both ears, or one, under `node --test`.
 *
 *   node --test 'tests/js/*.test.js'
 *
 * Two rig tiles can play two radios at once - the browser mixes every
 * tile's player - but in both ears they are hard to tell apart, so each
 * tile can be put in one ear. Loaded from the real file, the way the
 * phone shell's test loads mobile.js; the audio graph is a stand-in that
 * records what is wired to what. That the routing really is silent in
 * the other ear was checked separately in a browser's own audio engine,
 * rendering a tone offline and measuring each channel.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const SRC = fs.readFileSync(path.join(__dirname, '..', '..', 'hamdash',
  'static', 'js', 'listen.js'), 'utf8');
const win = { HD: { tileUid: 't1' } };
// eslint-disable-next-line no-new-func
new Function('window', 'document', SRC)(win, {});
const L = win.HDListen;

/* A node that remembers its connections. */
function node(name, edges) {
  return {
    name,
    connect(to) { edges.push([name, to.name]); return to; },
    disconnect() {
      for (let i = edges.length - 1; i >= 0; i -= 1) {
        if (edges[i][0] === name) edges.splice(i, 1);
      }
    },
  };
}

function ctx(withPanner = true) {
  const edges = [];
  const c = { edges, destination: node('out', edges), made: [] };
  if (withPanner) {
    c.createStereoPanner = () => {
      const p = node(`pan${c.made.length}`, edges);
      p.pan = { value: 0 };
      c.made.push(p);
      return p;
    };
  }
  return c;
}

test('stereo goes straight out, through no panner', () => {
  // A panner at its centre is 3 dB down on a mono source; stereo must
  // sound exactly as it did before there was a choice.
  const c = ctx();
  const gain = node('gain', c.edges);
  const p = L.route(c, gain, 'stereo', null);
  assert.strictEqual(p, null);
  assert.deepStrictEqual(c.edges, [['gain', 'out']]);
  assert.strictEqual(c.made.length, 0);
});

test('left and right are a panner hard over', () => {
  for (const [choice, want] of [['left', -1], ['right', 1]]) {
    const c = ctx();
    const gain = node('gain', c.edges);
    const p = L.route(c, gain, choice, null);
    assert.strictEqual(p.pan.value, want, choice);
    assert.deepStrictEqual(c.edges, [['gain', 'pan0'], ['pan0', 'out']], choice);
  }
});

test('changing ears mid-listen leaves exactly one path out', () => {
  const c = ctx();
  const gain = node('gain', c.edges);
  let p = L.route(c, gain, 'left', null);
  p = L.route(c, gain, 'right', p);
  assert.deepStrictEqual(c.edges, [['gain', 'pan1'], ['pan1', 'out']]);
  p = L.route(c, gain, 'stereo', p);
  assert.strictEqual(p, null);
  assert.deepStrictEqual(c.edges, [['gain', 'out']]);
});

test('a browser with no stereo panner still plays, in both ears', () => {
  const c = ctx(false);
  const gain = node('gain', c.edges);
  assert.strictEqual(L.route(c, gain, 'left', null), null);
  assert.deepStrictEqual(c.edges, [['gain', 'out']]);
});

test('an unknown choice is stereo', () => {
  const c = ctx();
  const gain = node('gain', c.edges);
  assert.strictEqual(L.route(c, gain, 'both', null), null);
  assert.deepStrictEqual(c.edges, [['gain', 'out']]);
});

test('volume and ears are kept per tile', () => {
  assert.strictEqual(L.key('vol', 't1'), 'rx_audio.vol.t1');
  assert.strictEqual(L.key('ears', 't1'), 'rx_audio.ears.t1');
  assert.notStrictEqual(L.key('vol', 't1'), L.key('vol', 't2'));
});

test('the page offers the three choices and wires them', () => {
  for (const v of ['stereo', 'left', 'right']) {
    assert.match(SRC, new RegExp(`<option value="${v}">`));
  }
  assert.match(SRC, /\$\('ears'\)\.onchange/);
  assert.match(SRC, /panner = route\(ctx, gain, choice, panner\)/);
  // A level set before volume was per tile still comes across.
  assert.match(SRC, /localStorage\.getItem\(key\('vol', uid\)\)\s*\?\?\s*localStorage\.getItem\(OLD_VOL_KEY\)/);
});
