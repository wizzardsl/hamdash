/* HD.radio.for(id): the radio calls, bound to one radio.
 *
 *   node --test 'tests/js/*.test.js'
 *
 * `HD.radio` is the selected radio and must go on sending exactly what it
 * always did - no `rig` anywhere - so every card that never names a radio
 * is untouched. `.for(id)` adds the id to every call: `?rig=` on a GET,
 * `rig` in a POST body.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const SRC = fs.readFileSync(path.join(__dirname, '..', '..', 'hamdash',
  'static', 'js', 'hamdash.js'), 'utf8');

// The function is pulled out of the file rather than the whole SDK run,
// which wants a DOM, a parent frame and an event stream.
const start = SRC.indexOf('  function radioFor(rig) {');
const end = SRC.indexOf('\n  }\n', start) + 4;
assert.ok(start > 0, 'radioFor is gone from hamdash.js');

function load() {
  const sent = [];
  const HD = {
    get: (p) => { sent.push(['GET', p]); return Promise.resolve({}); },
    post: (p, b) => { sent.push(['POST', p, b]); return Promise.resolve({}); },
  };
  // eslint-disable-next-line no-new-func
  const radioFor = new Function('HD', `${SRC.slice(start, end)}; return radioFor;`)(HD);
  return { radio: radioFor(''), sent };
}

test('the selected radio sends no radio id at all', async () => {
  const { radio, sent } = load();
  await radio.state();
  await radio.freq(7074000);
  await radio.meters();
  await radio.ptt(true);
  assert.deepStrictEqual(sent, [
    ['GET', '/api/radio/state'],
    ['POST', '/api/radio/freq', { hz: 7074000 }],
    ['GET', '/api/radio/meters'],
    ['POST', '/api/radio/ptt', { on: true }],
  ]);
});

test('for(id) names the radio on every call', async () => {
  const { radio, sent } = load();
  const b = radio.for('rig b');
  await b.state();
  await b.meters();
  await b.freq(14074000);
  await b.mode('USB');
  await b.ptt(false);
  await b.vfo('B');
  await b.swapVfo();
  await b.split(true);
  assert.deepStrictEqual(sent, [
    ['GET', '/api/radio/state?rig=rig%20b'],
    ['GET', '/api/radio/meters?rig=rig%20b'],
    ['POST', '/api/radio/freq', { hz: 14074000, rig: 'rig b' }],
    ['POST', '/api/radio/mode', { mode: 'USB', width: 0, rig: 'rig b' }],
    ['POST', '/api/radio/ptt', { on: false, rig: 'rig b' }],
    ['POST', '/api/radio/vfo', { which: 'B', rig: 'rig b' }],
    ['POST', '/api/radio/vfo', { swap: true, rig: 'rig b' }],
    ['POST', '/api/radio/split', { on: true, rig: 'rig b' }],
  ]);
  assert.strictEqual(b.id, 'rig b');
});

test("'selected' and nothing are the selected radio", async () => {
  for (const id of ['selected', '', undefined, null]) {
    const { radio, sent } = load();
    const r = radio.for(id);
    assert.strictEqual(r.id, '');
    await r.state();
    assert.deepStrictEqual(sent, [['GET', '/api/radio/state']], String(id));
  }
});

test('the rig card goes through its own radio, never HD.radio directly', () => {
  const page = fs.readFileSync(path.join(__dirname, '..', '..', 'plugins',
    'rig_control', 'index.html'), 'utf8');
  const direct = page.match(/HD\.radio\.(?!for\(|modes\()\w+/g) || [];
  assert.deepStrictEqual(direct, [], 'a call that ignores the tile\'s radio');
  // A keep-alive sent to a radio that is not transmitting keys it, so it
  // must be bound to the radio that is on the air.
  assert.match(page, /const held = radio;[\s\S]*held\.ptt\(true\)/);
});
