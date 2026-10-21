/* rigtiles.js - which rig tile belongs to which radio.
 *
 * A Rig Control tile follows the selected radio unless its `radio`
 * setting names one. A radio added in Settings -> Radio gets a tile of its
 * own by ticking it - in the Cards menu or beside the radio in Settings -
 * and both of those ask this file the same questions, so they cannot
 * disagree about which tile is whose.
 *
 * Pure: entries in, answers out. The board does the adding and removing.
 * `tests/js/rigtiles.test.js` drives it.
 */
(function (root) {
  const CARD = 'rig_control';

  /* The radio an entry is bound to, or '' for "the selected one" - which
     is also the answer for anything that is not a rig tile at all. */
  function boundTo(entry) {
    if (!entry || entry.card !== CARD) return '';
    const r = String((entry.settings || {}).radio || '');
    return r === 'selected' ? '' : r;
  }

  /* The rig tiles for one radio by id, or the unbound ones for ''. */
  function forRadio(cards, id) {
    return (cards || []).filter((c) => c.card === CARD
                                       && boundTo(c) === String(id || ''));
  }

  /* The tiles that are on a radio as the Cards menu means it: those bound
     to it, and - for the selected radio - a plain rig tile too, because
     that is the radio it is showing. Without the second half a board with
     a plain tile offered the FT-900 again unticked, and ticking it put a
     second tile on the same radio. */
  function ofRadio(cards, id, selected) {
    const mine = forRadio(cards, id);
    return id && String(id) === String(selected || '')
      ? [...forRadio(cards, ''), ...mine] : mine;
  }

  /* Every rig tile on the board, whichever radio it is on. */
  function all(cards) {
    return (cards || []).filter((c) => c.card === CARD);
  }

  /* What to add for a radio's own tile. Its title is the radio's name,
     because two rig tiles both called "Rig Control" are two tiles nobody
     can tell apart. */
  function def(rig, manifest) {
    const m = manifest || {};
    return { card: CARD, title: rig.name || 'Radio',
      settings: { radio: rig.id },
      w: m.w, h: m.h, min_w: m.min_w, min_h: m.min_h };
  }

  /* After the radio list changes: tiles bound to a radio that is gone,
     and tiles whose title is no longer their radio's name.

     Only when there is a list to go by. An empty or missing one is not
     "every radio was removed" - it is a page that has not read the list
     yet, and acting on it would take every radio's tile off the board. */
  function tidy(cards, rigs, plainName) {
    const out = { drop: [], retitle: [] };
    // A tile keeps the title it was given, so a card renamed since - Rig
    // control became Rig Control - would go on showing the old name on
    // every board that already had one.
    if (plainName) {
      forRadio(cards, '').forEach((c) => {
        if (c.title !== plainName) out.retitle.push([c.uid, plainName]);
      });
    }
    if (!Array.isArray(rigs) || !rigs.length) return out;
    const byId = new Map(rigs.map((r) => [String(r.id), r]));
    (cards || []).forEach((c) => {
      const id = boundTo(c);
      if (!id) return;
      const rig = byId.get(id);
      if (!rig) out.drop.push(c.uid);
      else if ((rig.name || 'Radio') !== c.title) {
        out.retitle.push([c.uid, rig.name || 'Radio']);
      }
    });
    return out;
  }

  root.HDRigTiles = { CARD, boundTo, forRadio, ofRadio, all, def, tidy };
}(typeof window !== 'undefined' ? window : globalThis));
