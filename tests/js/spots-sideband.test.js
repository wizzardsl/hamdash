/* The spots card tunes SSB as LSB or USB, under `node --test`.
 *
 *   node --test 'tests/js/*.test.js'
 *
 * A spot says "SSB"; a radio has LSB and USB and nothing called SSB, so
 * clicking such a spot sent a mode the radio refused, the refusal was
 * swallowed, and the radio tuned the frequency in whatever mode it was
 * already in - an LSB station heard on USB is unreadable. The operator's
 * rule of thumb picks the sideband: LSB below 10 MHz, USB above. 60 m is
 * the exception, USB by regulation though it sits at 5.3 MHz.
 *
 * `radioMode` lives inline in the card's page, so it is lifted out of the
 * real file and run - not a copy of it kept here.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PAGE = fs.readFileSync(path.join(__dirname, '..', '..', 'plugins',
  'spots', 'index.html'), 'utf8');

function lift(name) {
  const start = PAGE.indexOf(`function ${name}(`);
  assert.notStrictEqual(start, -1, `${name} is not in the spots page`);
  let i = PAGE.indexOf('{', start) + 1;
  for (let depth = 1; depth; i += 1) {
    if (PAGE[i] === '{') depth += 1;
    else if (PAGE[i] === '}') depth -= 1;
  }
  // eslint-disable-next-line no-new-func
  return new Function(`${PAGE.slice(start, i)}; return ${name};`)();
}

const radioMode = lift('radioMode');
const tuneMode = lift('tuneMode');

test('SSB below 10 MHz is LSB', () => {
  for (const hz of [1_843_000, 3_790_000, 7_185_000, 7_250_000, 9_999_000]) {
    assert.strictEqual(radioMode('SSB', hz), 'LSB', String(hz));
  }
});

test('SSB from 10 MHz up is USB', () => {
  for (const hz of [10_000_000, 14_250_000, 18_130_000, 21_300_000,
    24_950_000, 28_400_000, 50_125_000, 144_200_000]) {
    assert.strictEqual(radioMode('SSB', hz), 'USB', String(hz));
  }
});

test('60 m is USB though it is below 10 MHz', () => {
  for (const hz of [5_330_500, 5_346_500, 5_357_000, 5_403_500]) {
    assert.strictEqual(radioMode('SSB', hz), 'USB', String(hz));
  }
});

test('a spot that names its sideband keeps it', () => {
  assert.strictEqual(radioMode('USB', 7_185_000), 'USB');
  assert.strictEqual(radioMode('LSB', 14_250_000), 'LSB');
});

test('every other mode passes through untouched', () => {
  assert.strictEqual(radioMode('CW', 7_030_000), 'CW');
  assert.strictEqual(radioMode('FT8', 14_074_000), 'FT8');
  assert.strictEqual(radioMode('am', 3_885_000), 'AM');
  assert.strictEqual(radioMode('', 7_185_000), '');
  assert.strictEqual(radioMode(undefined, 7_185_000), '');
});

test('the click handler gives the radio radioMode, not the spot\'s mode', () => {
  const at = PAGE.indexOf("$('#pane').addEventListener('click'");
  assert.notStrictEqual(at, -1);
  let i = PAGE.indexOf('{', at) + 1;
  for (let depth = 1; depth; i += 1) {
    if (PAGE[i] === '{') depth += 1;
    else if (PAGE[i] === '}') depth -= 1;
  }
  const handler = PAGE.slice(at, i);
  assert.match(handler, /radioMode\(b\.dataset\.mode,\s*hz,\s*b\.dataset\.sideband\)/);
  assert.doesNotMatch(handler, /HD\.radio\.mode\(b\.dataset\.mode\)/);
  // Mode still before frequency: the CW offset rule.
  assert.ok(handler.indexOf('HD.radio.mode(') < handler.indexOf('HD.radio.freq('));
});

test('a sideband the comment named beats the rule of thumb', () => {
  assert.strictEqual(radioMode('SSB', 14_250_000, 'LSB'), 'LSB');
  assert.strictEqual(radioMode('SSB', 7_185_000, 'usb'), 'USB');
  assert.strictEqual(radioMode('SSB', 7_185_000, ''), 'LSB');
  assert.strictEqual(radioMode('SSB', 7_185_000, 'nonsense'), 'LSB');
  assert.strictEqual(radioMode('CW', 7_030_000, 'LSB'), 'CW');
});

test('which spots may change the radio\'s mode', () => {
  // The source's own field: always.
  assert.strictEqual(tuneMode({ mode: 'CW', mode_from: '' }), 'CW');
  assert.strictEqual(tuneMode({ mode: 'SSB' }), 'SSB');
  // Worked out from a comment: only SSB, where the frequency settles the
  // sideband and nothing else is being decided.
  assert.strictEqual(tuneMode({ mode: 'SSB', mode_from: 'comment' }), 'SSB');
  assert.strictEqual(tuneMode({ mode: 'CW', mode_from: 'comment' }), '');
  assert.strictEqual(tuneMode({ mode: 'FT8', mode_from: 'comment' }), '');
  // From the frequency: never.
  assert.strictEqual(tuneMode({ mode: 'FT8', mode_from: 'frequency' }), '');
  assert.strictEqual(tuneMode({ mode: '', mode_from: '' }), '');
});
