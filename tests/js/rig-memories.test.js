/* The rig card's memory buttons, under `node --test`.
 *
 *   node --test 'tests/js/*.test.js'
 *
 * A memory is a name, a frequency and a mode, kept in the tile's settings.
 * `memories.js` is the part that can be wrong quietly - a drop that lands
 * one place off, a stored list that draws a button tuning to NaN - so it
 * is pure and driven here. The page and the board are checked for the
 * wiring that made the old settings quietly revert.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8');
const win = {};
// eslint-disable-next-line no-new-func
new Function('window', read('plugins', 'rig_control', 'memories.js'))(win);
const M = win.HDMem;

test('a tile that never saved any gets the quick buttons it always had', () => {
  const got = M.clean(undefined);
  assert.deepStrictEqual(got.map((m) => m.label).slice(0, 2), ['FT8 20m', 'FT8 40m']);
  got[0].label = 'changed';
  assert.strictEqual(M.DEFAULTS[0].label, 'FT8 20m', 'the defaults were shared');
});

test('deleting every memory does not bring the defaults back', () => {
  assert.deepStrictEqual(M.clean([]), []);
});

test('a stored list that is not memories draws nothing rather than NaN', () => {
  const got = M.clean([
    { label: 'Good', hz: 7074000, mode: 'USB' },
    { label: '', hz: 7074000, mode: 'USB' },
    { label: 'No hz', mode: 'USB' },
    { label: 'Junk hz', hz: 'abc', mode: 'USB' },
    null,
    { label: '   padded   ', hz: '14074000.4', mode: ' USB ' },
  ]);
  assert.deepStrictEqual(got, [
    { label: 'Good', hz: 7074000, mode: 'USB' },
    { label: 'padded', hz: 14074000, mode: 'USB' },
  ]);
  assert.deepStrictEqual(M.clean('nonsense'), []);
});

test('a name is offered: where it is and how', () => {
  assert.strictEqual(M.suggest(14074000, 'USB'), '14.074 USB');
  assert.strictEqual(M.suggest(7030500, 'CW'), '7.0305 CW');
  assert.strictEqual(M.suggest(146520000, 'FM'), '146.520 FM');
  assert.strictEqual(M.suggest(3573000, ''), '3.573');
});

test('moving one puts it where it was dropped', () => {
  const l = ['a', 'b', 'c', 'd'];
  assert.deepStrictEqual(M.move(l, 0, 2), ['b', 'c', 'a', 'd']);
  assert.deepStrictEqual(M.move(l, 3, 0), ['d', 'a', 'b', 'c']);
  assert.deepStrictEqual(M.move(l, 1, 99), ['a', 'c', 'd', 'b']);
  assert.deepStrictEqual(l, ['a', 'b', 'c', 'd'], 'the list was changed in place');
});

/* Two rows of buttons, 60 wide with 5 between, 24 high:
 *   [0][1][2]
 *   [3][4]                                                            */
const R = (left, top) => ({ left, top, width: 60, height: 24, right: left + 60, bottom: top + 24 });
const ROWS = [R(0, 0), R(65, 0), R(130, 0), R(0, 30), R(65, 30)];

test('a drop lands before the button whose middle it is left of', () => {
  // Dragging 0 to just left of the middle of 2: it goes between 1 and 2.
  const to = M.dropIndex(ROWS, 155, 10, 0);
  assert.deepStrictEqual(M.move([0, 1, 2, 3, 4], 0, to), [1, 0, 2, 3, 4]);
  // Past the middle of 2: after it.
  const after = M.dropIndex(ROWS, 170, 10, 0);
  assert.deepStrictEqual(M.move([0, 1, 2, 3, 4], 0, after), [1, 2, 0, 3, 4]);
});

test('a drop on the second row of a wrapped list lands on that row', () => {
  // Dragging 1 to the start of the second row: before 3.
  const to = M.dropIndex(ROWS, 5, 40, 1);
  assert.deepStrictEqual(M.move([0, 1, 2, 3, 4], 1, to), [0, 2, 1, 3, 4]);
  // Right of everything on the last row: the end.
  const end = M.dropIndex(ROWS, 300, 40, 1);
  assert.deepStrictEqual(M.move([0, 1, 2, 3, 4], 1, end), [0, 2, 3, 4, 1]);
});

test('dropping a button where it started leaves the order alone', () => {
  for (let i = 0; i < ROWS.length; i += 1) {
    const r = ROWS[i];
    const to = M.dropIndex(ROWS, r.left + 10, r.top + 10, i);
    assert.strictEqual(to, i, `button ${i} moved without going anywhere`);
  }
});

test('the page saves memories alone, and never with a browser dialog', () => {
  const page = read('plugins', 'rig_control', 'index.html');
  assert.match(page, /HD\.saveSettings\(\{ memories: list \}\)/);
  // Saving the whole settings object could put back a memory list another
  // browser had just edited.
  assert.doesNotMatch(page, /HD\.saveSettings\(conf\)/);
  assert.doesNotMatch(page, /\b(prompt|confirm|alert)\(/, 'browser chrome in a webview');
  // A python card serves its folder under assets/ - a bare "memories.js"
  // is a 404 and the page fails on its first line.
  assert.match(page, /<script src="assets\/memories\.js"><\/script>/);
  // Deleting offers Undo, and right-click and the keyboard both reach it.
  assert.match(page, /id="mem-undo"/);
  assert.match(page, /addEventListener\('contextmenu'/);
  assert.match(page, /e\.key === 'Delete'/);
  // The pointer is captured only once a drag has started, never on
  // pointerdown - that sends the click to the wrong element.
  const down = page.slice(page.indexOf("box.addEventListener('pointerdown'"));
  const beforeMove = down.slice(0, down.indexOf('const move ='));
  assert.doesNotMatch(beforeMove, /setPointerCapture/);
});

test('a drag that ends without a click does not swallow the next one', () => {
  // A real drag often fires no click (the button moved from under the
  // pointer), so the flag that swallows the drag's own click stayed set and
  // ate the operator's next press - measured, with real mouse input.
  const page = read('plugins', 'rig_control', 'index.html');
  const end = page.slice(page.indexOf('    const end = (ev) => {'));
  const body = end.slice(0, end.indexOf('if (!memDrag) return;'));
  assert.match(body, /setTimeout\(\(\) => \{ memSuppress = false; \}, 0\)/);
});

test('the board keeps up with a card saving its own settings', () => {
  // It sent its own older copy back with every layout save, so a card's
  // setting reverted the moment anything on the board was moved.
  const app = read('hamdash', 'static', 'js', 'app.js');
  assert.match(app, /HD\.on\('card\.settings_changed', \(p\) => \{[\s\S]{0,120}entry\.settings = \{ \.\.\.p\.values \}/);
  // And the settings sheet keeps what the host kept, not just its fields.
  assert.match(app, /settingTile\.settings = \(kept && kept\.values\)/);
});
