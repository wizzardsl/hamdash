/* memories.js - the rig card's memory buttons, without the page.
 *
 * A memory is a name, a frequency and a mode, kept in the tile's settings
 * so it travels with the tile and two rig tiles on two radios keep two
 * lists. Pure: lists in, lists out. `tests/js/rig-memories.test.js`.
 */
(function (root) {
  // What a tile starts with until somebody changes it - the quick buttons
  // the card always had, now editable. An empty list once saved stays
  // empty: deleting every memory must not bring these back.
  const DEFAULTS = [
    { label: 'FT8 20m', hz: 14074000, mode: 'USB' },
    { label: 'FT8 40m', hz: 7074000, mode: 'USB' },
    { label: 'SSB 20m DX', hz: 14195000, mode: 'USB' },
    { label: 'CW 40m', hz: 7030000, mode: 'CW' },
    { label: 'WSPR 20m', hz: 14095600, mode: 'USB' },
    { label: '2m calling', hz: 146520000, mode: 'FM' },
  ];
  const MAX_LABEL = 24;
  const MAX = 40;

  /* The stored list, made safe to draw: what a tile has never saved gets
     the defaults; anything that is not a memory is dropped rather than
     drawn as a button that tunes to NaN. */
  function clean(raw) {
    if (raw === undefined || raw === null) return DEFAULTS.map((m) => ({ ...m }));
    if (!Array.isArray(raw)) return [];
    return raw.map((m) => ({
      label: String((m && m.label) || '').trim().slice(0, MAX_LABEL),
      hz: Math.round(Number(m && m.hz)),
      mode: String((m && m.mode) || '').trim(),
    })).filter((m) => m.label && Number.isFinite(m.hz) && m.hz > 0).slice(0, MAX);
  }

  /* A name to start from: where it is and how, which is what most people
     would type anyway. 14074000 USB -> "14.074 USB"; 7030500 CW ->
     "7.0305 CW". */
  function suggest(hz, mode) {
    let mhz = (Number(hz) / 1e6).toFixed(6).replace(/0+$/, '');
    const dot = mhz.indexOf('.');
    if (mhz.length - dot - 1 < 3) mhz = Number(mhz).toFixed(3);
    return `${mhz}${mode ? ` ${mode}` : ''}`.slice(0, MAX_LABEL);
  }

  /* `list` with the item at `from` moved to `to`, both as indices into the
     list as it is now. */
  function move(list, from, to) {
    const out = list.slice();
    if (from < 0 || from >= out.length) return out;
    const [item] = out.splice(from, 1);
    out.splice(Math.max(0, Math.min(to, out.length)), 0, item);
    return out;
  }

  /* Where a dragged button lands, from the pointer and the buttons' boxes
     in order. The buttons wrap onto rows, so the first box on or below the
     pointer's row whose middle is right of it is the one it goes before.
     Returns the index in the list *without* the dragged one, which is
     what `move` wants once `from` is taken out. */
  function dropIndex(rects, x, y, from) {
    let at = rects.length;
    for (let i = 0; i < rects.length; i += 1) {
      const r = rects[i];
      if (y < r.top || (y < r.bottom && x < r.left + r.width / 2)) { at = i; break; }
    }
    return at > from ? at - 1 : at;
  }

  root.HDMem = { DEFAULTS, MAX_LABEL, MAX, clean, suggest, move, dropIndex };
}(typeof window !== 'undefined' ? window : globalThis));
