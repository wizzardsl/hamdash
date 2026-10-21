/* Which rig tile is whose radio, under `node --test`.
 *
 *   node --test 'tests/js/*.test.js'
 *
 * Each radio in Settings -> Radio can have a Rig control tile of its own,
 * switched on from the Cards menu or beside the radio in Settings. Both
 * ask `rigtiles.js` the same questions; these pin the answers, and that
 * the two switches really are the same one.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8');
const win = {};
// eslint-disable-next-line no-new-func
new Function('window', read('hamdash', 'static', 'js', 'rigtiles.js'))(win);
const RT = win.HDRigTiles;

const rig = (uid, radio, title) => ({ uid, card: 'rig_control', title,
  settings: radio === undefined ? {} : { radio } });
const BOARD = [
  rig('plain', undefined, 'Rig control'),
  rig('sel', 'selected', 'Rig control'),
  rig('ft', 'r900', 'FT-900'),
  { uid: 'log', card: 'logbook', settings: { radio: 'r900' } },
];

test('no radio and "selected" both mean the selected radio', () => {
  assert.deepStrictEqual(RT.forRadio(BOARD, '').map((c) => c.uid),
    ['plain', 'sel']);
});

test("a radio's tiles are the rig tiles bound to it, and nothing else", () => {
  // The logbook carries a `radio` key here only to prove it is ignored:
  // only a rig tile belongs to a radio.
  assert.deepStrictEqual(RT.forRadio(BOARD, 'r900').map((c) => c.uid), ['ft']);
  assert.deepStrictEqual(RT.forRadio(BOARD, 'other'), []);
});

test("the selected radio's row counts a plain rig tile as its own", () => {
  // A plain tile shows the selected radio. Counting it only as "the
  // selected radio" left the FT-900 row unticked on a board already
  // showing the FT-900, and ticking it put a second tile on the same radio.
  assert.deepStrictEqual(RT.ofRadio(BOARD, 'r900', 'r900').map((c) => c.uid),
    ['plain', 'sel', 'ft']);
  assert.deepStrictEqual(RT.ofRadio(BOARD, 'r900', 'other').map((c) => c.uid),
    ['ft']);
  assert.deepStrictEqual(RT.ofRadio(BOARD, 'other', 'other').map((c) => c.uid),
    ['plain', 'sel']);
});

test('every rig tile, whichever radio it is on', () => {
  assert.deepStrictEqual(RT.all(BOARD).map((c) => c.uid), ['plain', 'sel', 'ft']);
});

test("a radio's own tile is a rig tile, named after it, bound by id", () => {
  const d = RT.def({ id: 'r900', name: 'FT-900' }, { w: 4, h: 3 });
  assert.strictEqual(d.card, 'rig_control');
  assert.strictEqual(d.title, 'FT-900');
  assert.deepStrictEqual(d.settings, { radio: 'r900' });
  assert.strictEqual(d.w, 4);
});

test('a removed radio takes its tile; a renamed one renames it', () => {
  const got = RT.tidy([...BOARD, rig('gone', 'r_old', 'Old')],
    [{ id: 'r900', name: 'FT-900 MP' }]);
  assert.deepStrictEqual(got.drop, ['gone']);
  assert.deepStrictEqual(got.retitle, [['ft', 'FT-900 MP']]);
});

test('the tiles that follow the selected radio are never tidied away', () => {
  const got = RT.tidy(BOARD, [{ id: 'r900', name: 'FT-900' }]);
  assert.deepStrictEqual(got, { drop: [], retitle: [] });
});

test('no radio list yet means nothing is removed', () => {
  // A page that has not read the list must not read it as "every radio
  // was removed" and clear the board of rig tiles.
  for (const none of [undefined, null, []]) {
    assert.deepStrictEqual(RT.tidy(BOARD, none), { drop: [], retitle: [] });
  }
});

test('the Cards menu and Settings flip the same switch', () => {
  const app = read('hamdash', 'static', 'js', 'app.js');
  // The menu lists each radio under Rig control...
  assert.match(app, /radioRows\(ul, c\)/);
  assert.match(app, /onclick: \(\) => toggleRadio\(rig\)/);
  // ...and the radio's row in Settings calls the same function.
  assert.match(app, /class="rig-tile"/);
  assert.match(app, /own\.onchange = \(\) => toggleRadio\(/);
  // Rig control is a heading over the radios, not a switch: it shows
  // whether any radio has a tile, and pressing it does nothing.
  const head = app.slice(app.indexOf('menuButton(heading ? {'));
  const headDef = head.slice(0, head.indexOf('} : {'));
  assert.match(headDef, /head: true/);
  assert.match(headDef, /RT\.all\(shell\.cards\)/);
  assert.doesNotMatch(headDef, /onclick/, 'the heading is still a switch');
  // Both switches count what the menu counts.
  assert.match(app, /mine: RT\.ofRadio\(shell\.cards, rig\.id, selectedRadio\(\)\)/);
  assert.match(app, /own\.checked = !!rig\.id\s*&& RT\.ofRadio\(/);
  // The board loads the module before the shell that uses it.
  const page = read('hamdash', 'static', 'index.html');
  assert.ok(page.indexOf('rigtiles.js') > 0
    && page.indexOf('rigtiles.js') < page.indexOf('/static/js/app.js'));
});

test("a radio's tile is saved before its card loads", () => {
  // The card reads its settings from the host. Mounted first, it read the
  // defaults - the selected radio - under a title naming another one.
  const app = read('hamdash', 'static', 'js', 'app.js');
  const fn = app.slice(app.indexOf('  async function addBorn(def) {'));
  assert.ok(fn.length < app.length, 'addBorn is gone');
  const body = fn.slice(0, fn.indexOf('\n  }\n'));
  assert.ok(body.indexOf("await HD.put('/api/layout'") > 0);
  assert.ok(body.indexOf("await HD.put('/api/layout'") < body.indexOf('shell.add(def)'),
    'the tile is mounted before its settings are saved');
  assert.match(app, /await addBorn\(def\)/);
});

test('a ticked box shows a tick', () => {
  // It was the two characters "¹3" from the day the menu was written.
  const css = read('hamdash', 'static', 'css', 'app.css');
  const rules = css.match(/menu-tick::after[^}]*}/g) || [];
  assert.ok(rules.length > 0);
  rules.forEach((r) => assert.match(r, /content: "\\2713"/, r));
});

test('renaming a tile repaints it without reloading the card', () => {
  for (const f of ['grid.js', 'mobile.js']) {
    const src = read('hamdash', 'static', 'js', f);
    const at = src.indexOf('    retitle(cardUid, title) {');
    assert.ok(at > 0, `${f} has no retitle`);
    const body = src.slice(at);
    const fn = body.slice(0, body.indexOf('\n    }\n'));
    assert.doesNotMatch(fn, /setTiles|_mount|_remount/, `${f} reloads the card`);
  }
});
