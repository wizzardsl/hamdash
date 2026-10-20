/* app.js — the shell: themes, card palette, settings, layout persistence. */
(async function () {
  const $ = (s) => document.querySelector(s);
  const gridEl = $('#grid');
  const phoneEl = $('#phone');
  let cards = [];
  let themes = [];
  let config = {};

  // ---- toast ----------------------------------------------------------
  let toastTimer;
  function toast(msg, action) {
    const t = $('#toast');
    t.textContent = '';
    t.classList.toggle('has-action', !!action);
    t.appendChild(document.createTextNode(msg));
    if (action) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'toast-action';
      b.textContent = action.label;
      b.onclick = () => {
        t.classList.remove('show', 'has-action');
        action.fn();
      };
      t.appendChild(b);
    }
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show', 'has-action'),
                            action ? 7000 : 3200);
  }

  // ---- clock ----------------------------------------------------------
  // Both clocks. A log is kept in UTC and a life is lived in local time, so
  // showing one and making the operator do the arithmetic is a way to mislog
  // a QSO by hours.
  const zone = (() => {
    try {
      const n = new Intl.DateTimeFormat(undefined, { timeZoneName: 'short' })
        .formatToParts(new Date()).find((x) => x.type === 'timeZoneName');
      return (n && n.value) || 'local';
    } catch { return 'local'; }
  })();
  $('#clock-zone').textContent = zone.toLowerCase();
  setInterval(() => {
    const now = new Date();
    $('#clock').textContent = now.toISOString().slice(11, 19);
    $('#clock-local').textContent = now.toTimeString().slice(0, 8);
  }, 1000);

  // ---- space weather on the rail --------------------------------------
  //
  // The numbers an operator reads before deciding what band to try. They
  // move slowly - the flux and sunspot number are daily, Kp three-hourly -
  // so this asks every quarter hour and the host caches for the same,
  // which between them means one request to NOAA however many boards are
  // open.
  const SOLAR_EVERY = 15 * 60 * 1000;

  /* K is the one that changes what you should do. Below 4 is ordinary and
     gets no colour at all: a rail of traffic lights that are green every
     day teaches you to stop reading it. */
  function kLevel(k) {
    if (!Number.isFinite(k)) return '';
    if (k >= 5) return 'bad';            // storm
    if (k >= 4) return 'warn';           // unsettled
    return '';
  }

  /* A, B and C flares are the background the bands live with. M and X are
     the ones that take the daylit HF bands away, which is the only reason
     this is on the rail at all. */
  function xrayLevel(cls) {
    const c = String(cls || '').trim().toUpperCase()[0];
    if (c === 'X') return 'bad';
    if (c === 'M') return 'warn';
    return '';
  }

  function cell(id, value, level, title) {
    const el = $(id);
    if (!el) return;
    el.querySelector('i').textContent = value;
    el.className = 'sw' + (level ? ' ' + level : '')
      + (id === '#sw-ssn' || id === '#sw-a' ? ' opt' : '');
    if (title) el.title = title;
  }

  async function paintSolar() {
    let d;
    try { d = await HD.get('/api/space/solar'); } catch (e) { return; }
    // Nothing rather than zeros: a dashboard that says SFI 0 is lying,
    // and 0 is a real reading.
    if (!d || !d.ok) { $('#solar').hidden = true; return; }
    const num = (v) => (Number.isFinite(Number(v)) ? String(v) : '--');
    cell('#sw-sfi', num(d.sfi));
    cell('#sw-ssn', num(d.ssn));
    cell('#sw-a', num(d.a));
    cell('#sw-k', Number.isFinite(Number(d.k)) ? Number(d.k).toFixed(1) : '--',
         kLevel(Number(d.k)));
    cell('#sw-xray', d.xray || '--', xrayLevel(d.xray),
         d.xray_max ? `Now ${d.xray} · largest today ${d.xray_max}`
                    : undefined);
    $('#solar').hidden = false;
  }

  paintSolar();
  setInterval(paintSolar, SOLAR_EVERY);

  // ---- themes ---------------------------------------------------------
  function applyTheme(id) {
    const t = themes.find((x) => x.id === id) || themes[0];
    if (!t) return;
    document.documentElement.dataset.theme = t.id;
    Object.entries(t.vars || {}).forEach(([k, v]) => {
      document.documentElement.style.setProperty(k, v);
    });
    $('#theme-pick').value = t.id;
    $('#s-theme').value = t.id;
  }

  // ---- the shell: a board, or a phone ---------------------------------
  //
  // One card, in its holder. A tile may hold several and shows one at a
  // time; every one of them is mounted, so this runs once per card rather
  // than once per tile. The phone shell mounts lazily and calls the same
  // function, so there is one answer to "what is a card on this board".
  function renderBody(entry, host) {
    const card = cards.find((c) => c.id === entry.card);
    const frame = document.createElement('iframe');
    const st = entry.settings || {};
    if (card && card.kind === 'url') {
      frame.src = st.url || entry.url || card.url;
    } else {
      // the tile uid goes with it: one process, many tiles
      frame.src = `/cards/${entry.card}/?uid=${encodeURIComponent(entry.uid)}`;
    }
    frame.dataset.uid = entry.uid;
    frame.setAttribute('title', entry.title || entry.card);
    frame.setAttribute('referrerpolicy', 'no-referrer');
    host.appendChild(frame);
    shell.setConfigurable(entry.uid, !!(card && card.schema && card.schema.length));
    if (card && card.kind === 'python') {
      HD.post(`/api/cards/${card.id}/start`).then((i) => {
        shell.status(card.id, i.status);
      }).catch(() => shell.status(card.id, 'failed'));
    } else {
      shell.status(entry.card, 'n/a');
    }
  }

  const titleOf = (entry) => entry.title
    || (cards.find((c) => c.id === entry.card) || {}).name
    || entry.card;

  function makeBoard() {
    return new Board(gridEl, {
    cols: 12,
    rowPx: 80,
    onChange: saveLayout,
    onPopOut: (entry) => HDPopout.popOut(entry),
    onPopIn: (entry) => HDPopout.popIn(entry.uid),
    onReopen: (entry) => HDPopout.popOut(entry),
    isPoppedOpen: (uid) => HDPopout.isOpen(uid),
    onSettings: (entry) => openTileSettings(entry),
    onClose: (cardUid) => closeCard(cardUid),
    // Docking moved to the tab strip, so anybody who used to drop a card
    // anywhere will think it broke. Said once, to somebody who has never
    // docked anything - and never again once they have, because by then
    // they know and a hint that keeps coming back is just noise.
    onHint: (what) => { if (what === 'drag') hintDock(); },
    onDocked: () => { learned('dock'); },
    renderBody,
    });
  }

  function makePhone() {
    return new HDPhone.Phone(phoneEl, {
      onChange: saveLayout,
      onSettings: (entry) => openTileSettings(entry),
      titleOf,
      renderBody,
    });
  }

  /* Which shell, and the operator's say in it.
   *
   * `HDPhone.wants()` decides by viewport, not by user agent - see there.
   * A choice is remembered in `localStorage` and nowhere else: it is a
   * fact about this person at this screen, like `hd.learned.*`, and the
   * same board is opened from a desk and from a pocket. Putting it in
   * `layout.json` or `config.json` would make one of those two wrong.
   */
  const VIEW_KEY = 'hd.view';
  const savedView = () => {
    try { return localStorage.getItem(VIEW_KEY) || ''; } catch { return ''; }
  };
  const rememberView = (v) => {
    try { localStorage.setItem(VIEW_KEY, v); } catch { /* private window */ }
  };
  const wantPhone = () =>
    (savedView() || (HDPhone.wants() ? 'phone' : 'board')) === 'phone';

  let shell = null;
  let shellKind = '';

  /* Build one shell and only one.
   *
   * Both at once is every card mounted twice - two iframes, two listeners,
   * two openers of one sound card - which is the bug
   * `tests/test_rx_listeners.py` exists for, reached from the other end.
   * So the old one is destroyed first, and destroying tells its frames
   * they are hidden on the way out. */
  function buildShell(kind) {
    if (shellKind === kind) return;
    const keep = shell ? shell.tiles : null;
    if (shell) shell.destroy();
    shellKind = kind;
    gridEl.hidden = kind !== 'board';
    phoneEl.hidden = kind !== 'phone';
    shell = kind === 'phone' ? makePhone() : makeBoard();
    shell.onError = (msg) => toast(msg);
    // The way back, and only where it is needed. On a desk the board is
    // right and the button is noise; on a phone showing the full board it
    // is the only way out, so it goes in the rail. In the phone shell the
    // rail has no room for it and it lives in More instead - one route,
    // whichever shell you are in.
    const t = $('#view-toggle');
    t.hidden = kind === 'phone' || !HDPhone.wants();
    t.textContent = 'Phone view';
    if (keep) shell.setTiles(keep);
  }

  /* Follow the screen until the operator overrules it.
   *
   * A phone turned sideways, or a desktop window dragged narrow, should
   * land in the shell that fits - but only while nobody has chosen. Once
   * they have, rebuilding under them would throw away the choice and the
   * card they were looking at. */
  HDPhone.onChange(() => {
    if (savedView()) return;
    buildShell(HDPhone.wants() ? 'phone' : 'board');
  });

  $('#view-toggle').onclick = () => {
    const to = shellKind === 'phone' ? 'board' : 'phone';
    rememberView(to);
    buildShell(to);
  };
  $('#phone-full').onclick = () => {
    $('#phone-more').close();
    rememberView('board');
    buildShell('board');
  };
  $('#phone-more-close').onclick = () => $('#phone-more').close();

  let saveTimer;
  function saveLayout(tiles) {
    $('#empty').style.display = tiles.length ? 'none' : '';
    renderCardsMenu();      // a tile closed with its own x unticks it here
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      // HDLayout.forSave decides the shape, so the board and its tests
      // agree about what reaches layout.json rather than each having a list
      // of field names to drift apart.
      HD.put('/api/layout', { tiles: HDLayout.forSave(tiles) })
        .catch((e) => toast(e.message));
    }, 300);
  }

  // ---- per-tile settings ----------------------------------------------
  let settingTile = null;
  let tileSecrets = {};

  function fieldRow(f, value) {
    const row = document.createElement('label');
    row.className = f.type === 'bool' ? 'row' : '';
    const id = `set-${f.name}`;
    let input;
    if (f.type === 'audio_in') {
      /* A sound input, chosen rather than typed.
       *
       * The card cannot know what this machine has, so the host fills
       * the list: the manifest's own entries first (`rig`, `mock`) and
       * then the real inputs. It was a free text box where the operator
       * typed part of a device name from memory, which is how you get a
       * card that is silently deaf because of a typo.
       *
       * The list is fetched after the row is built, because enumerating
       * sound devices can take seconds on Windows and a settings sheet
       * that will not open is worse than one that fills in.
       */
      input = document.createElement('select');
      const add = (val, label) => {
        const o = document.createElement('option');
        o.value = String(val);
        o.textContent = label === undefined ? String(val) : label;
        input.appendChild(o);
        return o;
      };
      (f.options || []).forEach((o) => add(o));
      const want = value ?? f.default ?? '';
      if (want !== '' && !(f.options || []).map(String).includes(String(want))) {
        // Whatever is stored stays selectable while the real list is on
        // its way - and if the device has since gone, it stays in the
        // list rather than the sheet silently choosing something else.
        add(want);
      }
      input.value = want;
      HD.get('/api/audio/devices').then((d) => {
        const seen = new Set([...input.options].map((o) => o.value));
        (d.inputs || []).forEach((dev) => {
          if (!seen.has(dev.id)) add(dev.id, dev.name || dev.id);
        });
        if (want !== '' && !(d.inputs || []).some((dev) => dev.id === want)
            && !(f.options || []).map(String).includes(String(want))) {
          const stale = [...input.options].find((o) => o.value === want);
          if (stale) stale.textContent = `${want} — not found now`;
        }
        input.value = want;             // the fetch must not move it
      }).catch(() => { /* the typed-in value still stands */ });
    } else if (f.type === 'select') {
      input = document.createElement('select');
      (f.options || []).forEach((o) => {
        const opt = document.createElement('option');
        opt.value = String(o);
        opt.textContent = String(o);
        input.appendChild(opt);
      });
      input.value = value ?? f.default;
    } else {
      input = document.createElement('input');
      input.type = { number: 'number', bool: 'checkbox', secret: 'password' }[f.type] || 'text';
      if (f.type === 'bool') input.checked = value ?? f.default;
      else if (f.type === 'secret') input.value = '';   // never shown one
      else input.value = value ?? f.default ?? '';
      if (f.placeholder) input.placeholder = f.placeholder;
      if (f.type === 'secret') {
        // The host does not send it, and an empty box means "leave it".
        input.placeholder = tileSecrets[f.name] ? 'saved — type to replace'
                                                : (f.placeholder || 'not set');
      }
      if (f.type === 'secret' || f.type === 'number') input.classList.add('mono');
    }
    input.id = id;
    input.dataset.field = f.name;
    input.dataset.kind = f.type;
    if (f.type === 'bool') {
      row.append(input, document.createTextNode(` ${f.label}`));
    } else {
      row.append(document.createTextNode(f.label), input);
    }
    if (f.help) {
      const help = document.createElement('small');
      help.className = 'sheet-note';
      help.textContent = f.help;
      row.appendChild(help);
    }
    return row;
  }

  async function openTileSettings(tile) {
    const card = cards.find((c) => c.id === tile.card);
    const schema = (card && card.schema) || [];
    if (!schema.length) { toast(`${tile.title || tile.card} has no settings`); return; }
    let values = tile.settings || {};
    try {
      const got = await HD.get(`/api/tiles/${tile.uid}/settings`);
      values = got.values || values;
      tileSecrets = got.secrets_set || {};
    } catch { /* unsaved tile: fall back to what's in memory */ }
    settingTile = tile;
    $('#tile-sheet-title').textContent = `${tile.title || tile.card} settings`;
    $('#tile-sheet-note').textContent =
      `Applies to this tile only. Other ${tile.card} tiles keep their own.`;
    const form = $('#tile-fields');
    form.innerHTML = '';
    schema.forEach((f) => form.appendChild(fieldRow(f, values[f.name])));
    $('#tile-sheet').showModal();
  }

  $('#tile-cancel').onclick = () => $('#tile-sheet').close();
  $('#tile-save').onclick = async () => {
    if (!settingTile) return;
    const values = {};
    $('#tile-fields').querySelectorAll('[data-field]').forEach((el) => {
      values[el.dataset.field] = el.dataset.kind === 'bool' ? el.checked
        : el.dataset.kind === 'number' ? Number(el.value)
        : el.value;
    });
    try {
      await HD.put(`/api/tiles/${settingTile.uid}/settings`, { values });
      settingTile.settings = values;
      shell.markPopped(settingTile.uid, !!settingTile.popped);   // repaint
      const node = document.querySelector(`.tile[data-uid="${settingTile.uid}"] iframe`);
      if (node) node.src = node.src;
      $('#tile-sheet').close();
      toast('Settings saved');
    } catch (e) {
      toast(e.message);
    }
  };

  // ---- palette --------------------------------------------------------
  // The cards a person can add. A `hidden` one is retired - still
  // installed because another card uses it (receive audio, which plays
  // from the rig card now) - and its tiles have been taken off the board.
  // Everything else still looks cards up in the full list, so a tile is
  // never left without its card's name or status.
  const offered = () => cards.filter((c) => !c.hidden);

  function renderPalette() {
    const ul = $('#palette-list');
    ul.innerHTML = '';
    offered().forEach((c) => {
      const li = document.createElement('li');
      li.innerHTML = `
        <span class="cl-kind"></span>
        <span class="cl-name"></span>
        <button class="btn" ${c.error ? 'disabled' : ''}>Add</button>`;
      li.querySelector('.cl-kind').textContent = c.kind;
      const name = li.querySelector('.cl-name');
      name.textContent = c.name;
      const meta = document.createElement('div');
      meta.className = c.error ? 'cl-err' : 'cl-meta';
      meta.textContent = c.error ? c.error : (c.description || `v${c.version}`);
      name.appendChild(meta);
      const def = { card: c.id, title: c.name, w: c.w, h: c.h,
        min_w: c.min_w, min_h: c.min_h, url: c.url };
      li.querySelector('button').onclick = () => {
        shell.add(def);
        toast(`${c.name} added`);
      };
      if (!c.error) {
        li.draggable = true;
        li.classList.add('cl-drag');
        li.addEventListener('dragstart', (e) => {
          e.dataTransfer.setData('text/hamdash-card', JSON.stringify(def));
          e.dataTransfer.effectAllowed = 'copy';
          $('#palette').close();          // get the sheet out of the way
        });
      }
      ul.appendChild(li);
    });
  }

  $('#palette-close').onclick = () => $('#palette').close();
  $('#rescan').onclick = rescan;

  async function rescan() {
    cards = await HD.get('/api/cards', { rescan: 1 });
    renderPalette();
    renderCardsMenu();
    toast(`${cards.length} cards found`);
  }

  // ---- cards menu -----------------------------------------------------
  // Ticking a card puts one tile on the board; unticking takes every copy
  // off, including the ones parked as tabs in a stack. A card can
  // legitimately have several (two web_embed tiles are two pages), so the
  // row shows the count - each carries its own settings and they go with it.
  const menuEl = () => $('#cards-menu');

  function tilesOf(cardId) {
    return shell.cards.filter((c) => c.card === cardId);
  }

  function renderCardsMenu() {
    const ul = $('#cards-menu-list');
    if (!ul) return;
    ul.innerHTML = '';
    offered().forEach((c) => {
      const mine = tilesOf(c.id);
      const li = document.createElement('li');
      const b = document.createElement('button');
      b.className = 'menu-item';
      b.type = 'button';
      b.setAttribute('role', 'menuitemcheckbox');
      b.setAttribute('aria-checked', String(mine.length > 0));
      // The name has to be explicit: the visible label is wrapped in spans
      // alongside a decorative tick, and the computed name came out empty -
      // a screen reader announced six unnamed checkboxes.
      b.setAttribute('aria-label', mine.length > 1
        ? `${c.name} (${mine.length} tiles)` : c.name);
      if (c.error) b.disabled = true;

      const tick = document.createElement('span');
      tick.className = 'menu-tick';
      tick.setAttribute('aria-hidden', 'true');

      const text = document.createElement('span');
      text.className = 'menu-text';
      text.textContent = c.name;
      const sub = document.createElement('small');
      sub.textContent = c.error ? c.error : (c.description || c.kind);
      text.appendChild(sub);

      b.append(tick, text);
      if (mine.length > 1) {
        const n = document.createElement('span');
        n.className = 'menu-count';
        n.textContent = `×${mine.length}`;
        b.appendChild(n);
      }
      b.onclick = () => toggleCard(c);
      li.appendChild(b);
      // Only cards the board itself wrote from a URL. A card somebody
      // wrote is their code, and a menu is not where that gets deleted -
      // the host refuses it too, so this is the signpost rather than the
      // rule (`registry.may_remove`).
      if (c.created_by === 'hamdash') {
        li.classList.add('has-del');
        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'menu-del';
        del.textContent = '\u00d7';
        // Nested spans and a bare glyph compute to no accessible name at
        // all - the same thing that left six unnamed checkboxes here.
        del.setAttribute('aria-label', `Delete the ${c.name} card`);
        del.title = `Delete the ${c.name} card`;
        del.onclick = (e) => { e.stopPropagation(); deleteCard(c); };
        li.appendChild(del);
      }
      ul.appendChild(li);
    });
  }

  function toggleCard(c) {
    const mine = tilesOf(c.id);
    if (!mine.length) {
      shell.add({ card: c.id, title: c.name, w: c.w, h: c.h,
                  min_w: c.min_w, min_h: c.min_h, url: c.url });
      toast(`${c.name} added`);
    } else {
      // Undo rather than a confirm dialog. A card can have several copies,
      // each carries its own settings, and one of them may be the third tab
      // of a stack - so the whole board is kept and put back, which is the
      // only way "where it was" survives. A browser confirm() is the one bit
      // of chrome this app has never used, for the same reason it never uses
      // window.open.
      const before = JSON.parse(JSON.stringify(shell.tiles));
      mine.forEach((entry) => shell.remove(entry.uid));
      toast(`${c.name} removed${mine.length > 1 ? ` (${mine.length} tiles)` : ''}`,
            { label: 'Undo',
              fn: () => {
                // Every card is remounted, so the board reloads - the price
                // of putting a tab back exactly where it was.
                shell.setTiles(before);
                saveLayout(shell.tiles);
                renderCardsMenu();
                toast(`${c.name} restored`);
              } });
    }
    renderCardsMenu();
  }

  /* Deleting a card the board made from a URL.
   *
   * Undo, like every other destructive thing here - and it can be offered
   * honestly because there is nothing in one of these but a name and an
   * address, so putting it back is writing the same two fields again
   * under the same id. The id matters: tiles refer to a card by it, so a
   * restored card with a new id would come back with the board pointing
   * at nothing.
   *
   * Its tiles go with it, the way unticking takes every copy off. Leaving
   * them would leave tiles of a card that no longer exists.
   */
  async function deleteCard(c) {
    const was = { id: c.id, name: c.name, url: c.url, w: c.w, h: c.h };
    const before = JSON.parse(JSON.stringify(shell.tiles));
    try {
      await HD.del(`/api/cards/${encodeURIComponent(c.id)}`);
    } catch (e) { toast(e.message); return; }
    tilesOf(c.id).forEach((entry) => shell.remove(entry.uid));
    cards = await HD.get('/api/cards', { rescan: 1 });
    renderCardsMenu();
    renderPalette();
    toast(`${was.name} deleted`, {
      label: 'Undo',
      fn: async () => {
        try {
          await HD.post('/api/cards', was);
        } catch (e) { toast(e.message); return; }
        cards = await HD.get('/api/cards', { rescan: 1 });
        shell.setTiles(before);
        saveLayout(shell.tiles);
        renderCardsMenu();
        renderPalette();
        toast(`${was.name} back`);
      },
    });
  }

  /* Closing a card from its own tile, or its tab.
   *
   * The same Undo the Cards menu offers, for the same reason and then one
   * more: ✕ is the *more* destructive of the two routes, because it takes
   * one tile's settings with it rather than a whole card's worth, and those
   * settings live on the tile (invariant 5). Having Undo on the menu and
   * not here left the easier button as the unrecoverable one.
   */
  function closeCard(cardUid) {
    const entry = shell.get(cardUid);
    if (!entry) return;
    const card = cards.find((c) => c.id === entry.card);
    const name = (card && card.name) || entry.card;
    const before = JSON.parse(JSON.stringify(shell.tiles));
    shell.remove(cardUid);
    renderCardsMenu();
    toast(`${name} closed`, {
      label: 'Undo',
      fn: () => {
        shell.setTiles(before);
        saveLayout(shell.tiles);
        renderCardsMenu();
        toast(`${name} back`);
      },
    });
  }

  /* Hints the operator only needs until they have done the thing once.
     localStorage because it is a fact about this person at this screen,
     not about the board - it must not travel in layout.json, and a shack
     computer that loses it has lost nothing that matters. */
  function hasLearned(what) {
    try { return localStorage.getItem(`hd.learned.${what}`) === '1'; }
    catch { return true; }        // no storage: say nothing rather than nag
  }

  function learned(what) {
    try { localStorage.setItem(`hd.learned.${what}`, '1'); } catch { /* fine */ }
  }

  let hintedThisLoad = false;
  function hintDock() {
    if (hintedThisLoad || hasLearned('dock')) return;
    hintedThisLoad = true;
    toast('Drop a card on another card’s tab strip to make it a tab',
          { label: 'Got it', fn: () => learned('dock') });
  }

  function openMenu(open) {
    const m = menuEl();
    if (!m) return;
    if (open) renderCardsMenu();
    m.hidden = !open;
    $('#cards-menu-btn').setAttribute('aria-expanded', String(open));
  }

  $('#cards-menu-btn').onclick = (e) => {
    e.stopPropagation();
    openMenu(menuEl().hidden);
  };
  menuEl().addEventListener('click', (e) => e.stopPropagation());
  document.addEventListener('click', () => openMenu(false));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !menuEl().hidden) {
      openMenu(false);
      $('#cards-menu-btn').focus();
    }
  });
  /* Pull the tiles up into the gaps.
   *
   * Unticking a few cards leaves holes, and the board deliberately does not
   * close them by itself - a layout that rearranged whenever something was
   * removed would move cards somebody had placed on purpose. The cost is
   * rows of nothing to scroll past, and closing them by hand is a drag per
   * tile. This is the same thing in one press, and it lives in the Cards
   * menu because unticking is what makes the gaps.
   *
   * It moves every tile, so it offers Undo like the other two things here
   * that rearrange the board.
   */
  $('#menu-tidy').onclick = () => {
    openMenu(false);
    const before = JSON.parse(JSON.stringify(shell.tiles));
    const moved = HDLayout.compact(shell.tiles);
    if (!moved) { toast('Already tidy'); return; }
    shell.setTiles(shell.tiles);
    saveLayout(shell.tiles);
    toast(`Tidied up — ${moved} tile${moved === 1 ? '' : 's'} moved`, {
      label: 'Undo',
      fn: () => {
        shell.setTiles(before);
        saveLayout(shell.tiles);
        toast('Put back');
      },
    });
  };

  /* A card from a web page, without opening an editor.
   *
   * `kind: url` has been in the manifest from the start and the only way
   * to get one was to write the JSON by hand, which is a strange thing to
   * ask of somebody who wants their cluster page in a tile.
   */
  const urlSheet = () => $('#url-sheet');

  function urlWhy(msg) {
    const el = $('#url-why');
    el.textContent = msg || '';
    el.hidden = !msg;
  }

  $('#menu-new-url').onclick = () => {
    openMenu(false);
    $('#url-name').value = '';
    $('#url-addr').value = '';
    urlWhy('');
    urlSheet().showModal();
    $('#url-name').focus();
  };

  $('#url-cancel').onclick = () => urlSheet().close();

  $('#url-form').addEventListener('submit', async (e) => {
    // The form is `method="dialog"`, which closes the sheet on submit -
    // too early to show the host's complaint in it.
    e.preventDefault();
    const name = $('#url-name').value.trim();
    const url = $('#url-addr').value.trim();
    urlWhy('');
    $('#url-make').disabled = true;
    let made;
    try {
      made = await HD.post('/api/cards', { name, url });
    } catch (err) {
      // The host names what to fix - a scheme that cannot go in a frame,
      // an empty address - so it is shown where the typing is rather
      // than in a toast that has gone by the time they look.
      urlWhy(err.message);
      return;
    } finally {
      $('#url-make').disabled = false;
    }
    urlSheet().close();
    cards = await HD.get('/api/cards', { rescan: 1 });
    renderCardsMenu();
    renderPalette();
    // Put it on the board: they asked for a page in a tile, not for an
    // entry in a menu.
    shell.add({ card: made.id, title: made.name, w: made.w, h: made.h,
                min_w: made.min_w, min_h: made.min_h, url: made.url });
    toast(`${made.name} added`);
  });

  $('#menu-rescan').onclick = rescan;
  $('#menu-browse').onclick = () => {
    openMenu(false);
    renderPalette();
    $('#palette').showModal();
  };

  // Arrange mode is gone: tiles drag by their header and resize from the
  // corner at any time, and the grid guides already appear while dragging.
  // A mode you had to enter first to do what already worked was just a step
  // in the way.

  // ---- logbook sync ---------------------------------------------------
  // The endpoint starts a job and returns; the result arrives on the bus,
  // because a LoTW pull is a minute of waiting on someone else's server and
  // a request handler holding a thread for that is what invariant 4 is about.
  function syncNote(which, msg, kind) {
    const n = $(`#s-${which}-note`);
    if (!n) return;
    n.textContent = msg;
    n.className = 'sheet-note' + (kind ? ` ${kind}` : '');
  }

  async function refreshSync() {
    try {
      const s = await HD.get('/api/log/sync');
      if (!s.lotw.configured) {
        syncNote('lotw', 'Enter your LoTW username and password, then Test connection.');
      } else if (!s.lotw.can_push) {
        syncNote('lotw', 'Ready to pull. Push needs TQSL, which is not '
                 + 'installed here — pulling works either way.');
      } else {
        syncNote('lotw', `Ready. TQSL found at ${s.lotw.tqsl}`, 'good');
      }
      syncNote('qrz', s.qrz.configured
        ? 'Ready.' : 'Paste the logbook API key, then Test connection.',
        s.qrz.configured ? 'good' : '');
      syncNote('wrl', s.wrl && s.wrl.configured
        ? 'Ready.' : 'Paste the developer API key, then Test connection.',
        s.wrl && s.wrl.configured ? 'good' : '');
    } catch (e) { /* settings can open before the host is warm */ }
  }

  const VERBS = { pull: 'Pulling', push: 'Pushing', test: 'Testing' };

  function wireSync(service, direction) {
    const el = $(`#s-${service}-${direction}`);
    if (!el) return;
    el.onclick = async () => {
      syncNote(service, `${VERBS[direction]}…`);
      try {
        // Saved first, as the lookup's Test does. The job runs on the host
        // from stored config, so a key pasted and tested straight away was
        // tested as "no key" - and the only way to make Test work was
        // Save, reopen Settings, then Test.
        await saveSettings({ quiet: true });
        await HD.post(`/api/log/sync/${service}/${direction}`, {
          since: $('#s-sync-since').value.trim(),
          // Defaults to true server side too, so a caller that forgets this
          // counts rather than uploads.
          dry_run: direction === 'push' ? $('#s-sync-dry').checked : false,
        });
      } catch (e) { syncNote(service, e.message, 'warn'); }
    };
  }
  ['lotw', 'qrz', 'wrl'].forEach((s) =>
    ['test', 'pull', 'push'].forEach((d) => wireSync(s, d)));

  HD.on('log.sync', (p) => {
    // The job is "<service> <direction>", so its first word names the panel.
    const which = (p.job || '').split(' ')[0] || 'lotw';
    if (p.running) { syncNote(which, p.message); return; }
    // Deliberately not calling refreshSync() here: it would immediately
    // overwrite "1,234 QSOs on QRZ" with "Ready.", which is how the most
    // useful message in the whole panel disappeared half a second after
    // arriving.
    syncNote(which, p.message, p.ok ? 'good' : 'warn');
  });

  // ---- radios ---------------------------------------------------------
  // The Radio tab is a list of rigs rather than one set of fields: a shack
  // usually has more than one radio and more than one way into it. What the
  // form shows depends on the backend, because the fields genuinely differ -
  // OmniRig owns the port and the rig type itself, so offering them here
  // would be a second place to set them and a second thing to disagree with.
  let rigEnv = { rigs: [], active: '', max: 10, bauds: [], ports: [],
                 backends: [], rigctld: '', rigctld_path: '' };
  let rigModels = null;              // fetched once, on demand
  let audioEnv = { ok: false, inputs: [], outputs: [], error: '' };
  // Until the list has been fetched, rigEnv.rigs is [] - which is a perfectly
  // good instruction to delete every radio. Saving must know the difference
  // between "none configured" and "not read yet".
  let rigsLoaded = false;

  // A device that is saved but no longer present - the rig interface is
  // unplugged, or this config came from another machine - still has to be
  // the selected option, or opening the sheet would silently change the
  // setting to whatever happened to be first.
  function audioOptions(side, chosen) {
    const rows = audioEnv[side] || [];
    const known = rows.some((d) => d.id === chosen);
    return opt('', 'none', chosen || '')
      + (chosen && !known
        ? opt(chosen, `${chosen} (not connected)`, chosen) : '')
      + rows.map((d) => opt(d.id, d.default ? `${d.name} (default)` : d.name,
                            chosen || '')).join('');
  }

  function audioNote() {
    const n = audioEnv.inputs.length + audioEnv.outputs.length;
    $('#audio-note').textContent = audioEnv.ok
      ? `${audioEnv.inputs.length} in, ${audioEnv.outputs.length} out`
        + (audioEnv.stale ? ' (last known)' : '')
      : (audioEnv.error || 'no sound devices found');
    $('#audio-note').className = 'sheet-note'
      + (audioEnv.ok && n ? '' : ' warn');
  }

  const BACKEND_LABEL = {
    mock: 'Simulated (no hardware)',
    omnirig: 'OmniRig',
    flrig: 'flrig already running',
    hamlib: 'Hamlib (hamdash opens the port)',
    rigctld: 'rigctld already running',
  };

  async function loadRigs() {
    try {
      rigEnv = await HD.get('/api/rigs');
      rigsLoaded = true;
    } catch (e) {
      $('#rig-note').textContent = e.message;
      return;
    }
    $('#s-rigctld-path').value = rigEnv.rigctld_path || '';
    // Served with the rigs, from the host's cache, so opening the sheet
    // never waits on PortAudio.
    if (rigEnv.audio) audioEnv = rigEnv.audio;
    renderRigs();
    rigNote();
    audioNote();
  }

  function rigNote() {
    const n = rigEnv.rigs.length;
    $('#rig-count').textContent = `${n} of ${rigEnv.max}`;
    $('#rig-add').disabled = n >= rigEnv.max;
    const st = rigEnv.state || {};
    $('#rig-note').textContent = st.ok
      ? `Live: ${st.rig || 'radio'} \u2014 ${fmtRigState(st)}`
      : (st.error || '');
    $('#rig-note').className = 'sheet-note' + (st.ok ? '' : ' warn');
    $('#rig-models-note').textContent = rigEnv.rigctld
      ? `rigctld: ${rigEnv.rigctld}`
      : 'Hamlib not found. OmniRig and simulated radios do not need it.';
  }

  const fmtRigState = (st) => [
    st.rig_type, st.freq ? `${(st.freq / 1e6).toFixed(6)} MHz` : '',
    st.mode && st.mode !== '?' ? st.mode : '', st.band,
  ].filter(Boolean).join(' \u00b7 ') || 'answered';

  const opt = (v, label, sel) =>
    `<option value="${esc(v)}"${String(sel) === String(v) ? ' selected' : ''}>`
    + `${esc(label)}</option>`;
  const esc = (v) => String(v ?? '').replace(/[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function renderRigs() {
    const box = $('#rig-list');
    box.innerHTML = '';
    rigEnv.rigs.forEach((rig, i) => box.appendChild(rigRow(rig, i)));
  }

  function rigRow(rig, i) {
    const el = document.createElement('div');
    el.className = 'rig' + (rig.id === rigEnv.active ? ' on' : '');
    const live = rig.id === rigEnv.active;
    el.innerHTML = `
      <div class="rig-top">
        <input class="rig-name" value="${esc(rig.name || '')}" aria-label="Name">
        <select class="rig-backend" aria-label="Connection">
          ${rigEnv.backends.map((b) => opt(b, BACKEND_LABEL[b] || b, rig.backend)).join('')}
        </select>
        ${live ? '<span class="live">in use</span>' : ''}
      </div>
      <div class="grid" data-for="omnirig"${rig.backend === 'omnirig' ? '' : ' hidden'}>
        <label>OmniRig slot
          <select class="rig-slot">${opt(1, 'Rig 1', rig.slot || 1)}${opt(2, 'Rig 2', rig.slot || 1)}</select>
        </label>
      </div>
      <div class="grid" data-for="hamlib"${rig.backend === 'hamlib' ? '' : ' hidden'}>
        <label>Radio <select class="rig-model"></select></label>
        <label>Port
          <select class="rig-port">${[''].concat(rigEnv.ports)
            .map((p) => opt(p, p || 'choose\u2026', rig.port || '')).join('')}</select>
        </label>
        <label>Baud
          <select class="rig-baud">${rigEnv.bauds
            .map((b) => opt(b, b, rig.baud || 9600)).join('')}</select>
        </label>
        <label>Data bits
          <select class="rig-data">${[7, 8].map((b) => opt(b, b, rig.data_bits || 8)).join('')}</select>
        </label>
        <label>Stop bits
          <select class="rig-stop">${[1, 2].map((b) => opt(b, b, rig.stop_bits || 1)).join('')}</select>
        </label>
        <label>Parity
          <select class="rig-parity">${['None', 'Even', 'Odd']
            .map((b) => opt(b, b, rig.parity || 'None')).join('')}</select>
        </label>
        <label>Handshake
          <select class="rig-hand">${['None', 'Hardware', 'XONXOFF']
            .map((b) => opt(b, b, rig.handshake || 'None')).join('')}</select>
        </label>
      </div>
      <div class="grid" data-for="rigctld"${rig.backend === 'rigctld' ? '' : ' hidden'}>
        <label>Host <input class="rig-host mono" value="${esc(rig.host || '127.0.0.1')}"></label>
        <label>Port <input class="rig-tcp mono" inputmode="numeric" value="${esc(rig.port || 4532)}"></label>
      </div>
      <div class="grid" data-for="flrig"${rig.backend === 'flrig' ? '' : ' hidden'}>
        <label>Host <input class="rig-fl-host mono" value="${esc(rig.host || '127.0.0.1')}"></label>
        <label title="flrig's XML port, from its Config → Xcvr tab">Port
          <input class="rig-fl-port mono" inputmode="numeric" value="${esc(rig.port || 12345)}"></label>
      </div>
      <div class="grid">
        <label>Audio in
          <select class="rig-audio-in" title="${esc(rig.audio_in || 'none')}">${audioOptions('inputs', rig.audio_in)}</select>
        </label>
        <label>Audio out
          <select class="rig-audio-out" title="${esc(rig.audio_out || 'none')}">${audioOptions('outputs', rig.audio_out)}</select>
        </label>
      </div>
      <div class="row-btns">
        <button class="btn btn-quiet rig-test" type="button">Test</button>
        <button class="btn btn-quiet rig-use" type="button"${live ? ' disabled' : ''}>Use this one</button>
        <button class="btn btn-quiet rig-omni" type="button"${rig.backend === 'omnirig' ? '' : ' hidden'}>OmniRig settings\u2026</button>
        <button class="btn btn-quiet rig-del" type="button">Remove</button>
      </div>
      <div class="said"></div>`;

    const sel = (c) => el.querySelector(`.${c}`);
    fillModels(sel('rig-model'), rig.model);

    const read = () => {
      const kind = sel('rig-backend').value;
      const out = { id: rig.id, name: sel('rig-name').value, backend: kind };
      if (kind === 'omnirig') out.slot = Number(sel('rig-slot').value);
      if (kind === 'hamlib') Object.assign(out, {
        model: Number(sel('rig-model').value) || 0,
        port: sel('rig-port').value,
        baud: Number(sel('rig-baud').value) || 0,
        data_bits: Number(sel('rig-data').value),
        stop_bits: Number(sel('rig-stop').value),
        parity: sel('rig-parity').value,
        handshake: sel('rig-hand').value,
      });
      if (kind === 'rigctld') Object.assign(out, {
        host: sel('rig-host').value.trim(),
        port: Number(sel('rig-tcp').value) || 4532,
      });
      if (kind === 'flrig') Object.assign(out, {
        host: sel('rig-fl-host').value.trim(),
        port: Number(sel('rig-fl-port').value) || 12345,
      });
      // Not inside the backend branches: a radio's sound card has nothing to
      // do with how its CAT is reached, and changing backend must not throw
      // the audio away.
      out.audio_in = sel('rig-audio-in').value;
      out.audio_out = sel('rig-audio-out').value;
      return out;
    };

    const say = (msg, kind) => {
      const n = el.querySelector('.said');
      n.textContent = msg;
      n.className = 'said' + (kind ? ` ${kind}` : '');
    };

    // Changing the backend changes which questions make sense, so the form
    // follows at once rather than after a save.
    sel('rig-backend').onchange = () => {
      const kind = sel('rig-backend').value;
      el.querySelectorAll('.grid[data-for]').forEach((g) => {
        g.hidden = g.dataset.for !== kind;
      });
      el.querySelector('.rig-omni').hidden = kind !== 'omnirig';
      rigEnv.rigs[i] = read();
      say('');
    };
    el.querySelectorAll('input, select').forEach((f) => {
      f.addEventListener('change', () => { rigEnv.rigs[i] = read(); });
    });

    sel('rig-test').onclick = async (e) => {
      e.target.disabled = true;
      say('asking\u2026');
      try {
        const r = await HD.post('/api/rigs/test', read());
        say(r.ok ? fmtRigState(r) : (r.error || 'no answer'),
            r.ok ? 'good' : 'warn');
      } catch (err) { say(err.message, 'warn'); }
      e.target.disabled = false;
    };

    sel('rig-use').onclick = async () => {
      try {
        await saveRigs(rig.id);
        say('');
      } catch (err) { say(err.message, 'warn'); }
    };

    sel('rig-omni').onclick = async () => {
      say('opening OmniRig\u2026');
      try {
        const r = await HD.post('/api/rigs/omnirig/dialog',
                                { slot: Number(sel('rig-slot').value) });
        say(r.ok ? `OmniRig: ${r.rig_type || 'no rig'} \u2014 ${r.status || ''}`
                 : (r.error || ''), r.ok ? 'good' : 'warn');
      } catch (err) { say(err.message, 'warn'); }
    };

    sel('rig-del').onclick = async () => {
      rigEnv.rigs.splice(i, 1);
      try { await saveRigs(); } catch (err) { toast(err.message); }
    };
    return el;
  }

  function fillModels(el, chosen) {
    if (!el) return;
    if (!rigModels || !rigModels.ok) {
      // No Hamlib, so no list to offer. A number still works, and saying
      // where it comes from beats an empty dropdown that looks broken.
      el.outerHTML = `<input class="rig-model mono" inputmode="numeric"
        value="${esc(chosen || '')}" placeholder="Hamlib model number"
        title="Install Hamlib and press Rescan to choose by name instead.">`;
      return;
    }
    el.innerHTML = opt('', 'choose\u2026', chosen || '')
      + rigModels.models.map((m) => opt(m.model, `${m.label} (${m.model})`,
                                        chosen || '')).join('');
  }

  async function saveRigs(active) {
    if (!rigsLoaded) return null;             // nothing was read, so nothing
    const body = { rigs: rigEnv.rigs, rigctld_path: $('#s-rigctld-path').value.trim() };
    if (active !== undefined) body.active = active;
    const r = await HD.put('/api/rigs', body);
    rigEnv.rigs = r.rigs;
    rigEnv.active = r.active;
    rigEnv.state = r.state;
    renderRigs();
    rigNote();
    return r;
  }

  $('#rig-add').onclick = async () => {
    if (rigEnv.rigs.length >= rigEnv.max) return;
    rigEnv.rigs.push({ name: 'New radio', backend: 'omnirig', slot: 1 });
    try { await saveRigs(); } catch (e) { toast(e.message); }
  };

  $('#audio-rescan').onclick = async (e) => {
    e.target.disabled = true;
    $('#audio-note').textContent = 'asking the sound system\u2026';
    try {
      // Save first: re-rendering the rows from a fresh device list would
      // otherwise discard anything edited and not yet saved.
      await saveRigs();
      audioEnv = await HD.get('/api/audio/devices', { refresh: 1 });
      renderRigs();
    } catch (err) { audioEnv = { ok: false, inputs: [], outputs: [],
                                 error: err.message }; }
    audioNote();
    e.target.disabled = false;
  };

  $('#rig-rescan').onclick = async (e) => {
    e.target.disabled = true;
    $('#rig-models-note').textContent = 'asking Hamlib\u2026';
    try {
      await HD.put('/api/rigs', { rigs: rigEnv.rigs,
                                  rigctld_path: $('#s-rigctld-path').value.trim() });
      rigModels = await HD.get('/api/rigs/models');
      await loadRigs();
      $('#rig-models-note').textContent = rigModels.ok
        ? `${rigModels.count} radios from Hamlib`
        : rigModels.error;
    } catch (err) { $('#rig-models-note').textContent = err.message; }
    e.target.disabled = false;
  };

  // The live rig can change without the sheet being open - another card, or
  // the radio being switched off - so keep the note honest.
  HD.on('radio.state', (st) => {
    if (!$('#settings-sheet').open) return;
    rigEnv.state = st;
    rigNote();
  });

  // ---- secrets --------------------------------------------------------
  // A password field that shows the stored value - or a stand-in for it -
  // sends that value back on the next save. That is how the callsign lookup
  // password used to be replaced by the literal "***" whenever anyone opened
  // settings and pressed Save. These boxes start empty, say whether something
  // is stored, and are only sent when actually typed in.
  function secretBox(sel, path) {
    const el = $(sel);
    el.value = '';
    const set = (config.secrets_set || {})[path];
    el.placeholder = set ? 'saved — type to replace' : 'not set';
    el.dataset.cleared = '';
    const clear = el.parentElement.querySelector('.clear-secret');
    if (clear) clear.hidden = !set;
  }

  // What a clipboard adds to a credential and nobody ever means: ordinary
  // whitespace, the non-breaking space a web page's markup leaves behind, the
  // zero-width joiners a rich-text copy carries, and a BOM. Only the ends —
  // the middle of a password is the operator's business.
  const CRUFT = /^[\s\u00a0\u200b-\u200d\ufeff]+|[\s\u00a0\u200b-\u200d\ufeff]+$/g;
  const cleanCred = (v) => (v || '').replace(CRUFT, '');

  // '' means "leave it", null means "clear it", anything else is a new value.
  function secretValue(sel) {
    const el = $(sel);
    const v = cleanCred(el.value);
    // Typing beats a pending Clear: the button is how you remove a secret,
    // not a mode you have to leave before you can set a new one.
    if (!v) return el.dataset.cleared === 'yes' ? null : undefined;
    return v;
  }

  function withSecret(obj, key, sel) {
    const v = secretValue(sel);
    if (v !== undefined) obj[key] = v;
    return obj;
  }

  document.querySelectorAll('.clear-secret').forEach((btn) => {
    btn.onclick = () => {
      const el = btn.parentElement.querySelector('input');
      el.value = '';
      el.dataset.cleared = 'yes';
      el.placeholder = 'will be cleared on save';
    };
  });

  // Credentials lose their surrounding whitespace where it can be seen, not
  // silently on save: pasting a key with a trailing newline used to store the
  // newline, and the service then said the password was wrong.
  document.querySelectorAll('#s-lk-user, #s-lk-pass, #s-lotw-user, '
    + '#s-lotw-pass, #s-qrz-key, #s-wrl-key, #s-call').forEach((el) => {
    const tidy = () => {
      const v = cleanCred(el.value);
      if (v !== el.value) el.value = v;
    };
    // After the paste has landed, and again when the field is left.
    el.addEventListener('paste', () => setTimeout(tidy, 0));
    el.addEventListener('blur', tidy);
    el.addEventListener('input', () => {
      if (el.dataset.cleared === 'yes' && el.value) {
        el.dataset.cleared = '';
        el.placeholder = 'saved \u2014 type to replace';
      }
    });
  });

  // ---- settings -------------------------------------------------------
  // Tabs. The panel is picked by name rather than by index so reordering the
  // strip in the markup cannot silently point a tab at the wrong section.
  function showTab(name) {
    document.querySelectorAll('#settings-sheet .tab').forEach((b) => {
      const on = b.dataset.tab === name;
      b.classList.toggle('on', on);
      b.setAttribute('aria-selected', String(on));
    });
    document.querySelectorAll('#settings-sheet .tab-panel').forEach((p) => {
      p.hidden = p.dataset.panel !== name;
    });
    $('.tab-body').scrollTop = 0;
  }
  document.querySelectorAll('#settings-sheet .tab').forEach((b) => {
    b.onclick = () => showTab(b.dataset.tab);
  });

  function fillSettings() {
    $('#s-call').value = config.station.callsign || '';
    $('#s-grid').value = config.station.grid || '';
    $('#s-op').value = config.station.operator || '';
    $('#s-units').value = config.units || 'km';
    $('#s-theme').value = config.theme || (themes[0] || {}).id || '';
    loadRigs();
    const sy = config.logbook_sync || {};
    $('#s-lotw-user').value = sy.lotw_user || '';
    secretBox('#s-lotw-pass', 'logbook_sync.lotw_password');
    secretBox('#s-qrz-key', 'logbook_sync.qrz_api_key');
    secretBox('#s-wrl-key', 'logbook_sync.wrl_api_key');
    refreshSync();
    const lk = config.callsign_lookup || {};
    $('#s-lk-provider').value = lk.provider || 'callook';
    $('#s-lk-account').value = lk.account || '';
    lookupNote('');
    refreshLookup();
    showCacheSize();
    $('#s-lk-user').value = lk.user || '';
    // Secrets are never sent to us, so the box starts empty and only says
    // whether one is stored. Leaving it alone keeps whatever is there.
    secretBox('#s-lk-pass', 'callsign_lookup.password');
    const wx = config.wsjtx || {};
    $('#s-wx-enabled').checked = !!wx.enabled;
    $('#s-wx-bind').value = wx.bind || '127.0.0.1';
    $('#s-wx-port').value = wx.port || 2237;
    refreshWsjtx();
    gridNote('');
    showTab('station');
  }

  /* ---- WSJT-X ---------------------------------------------------------
   *
   * The only question anybody has about this is whether it is actually
   * working, and a stored `enabled: true` cannot answer it: the port can
   * be open and perfectly correct while WSJT-X points somewhere else, and
   * the two look identical from here until a heartbeat arrives. So the
   * line says which of the three states it is in, and names the fix for
   * the two that are not "working".
   */
  let wsjtxTimer = null;

  async function refreshWsjtx() {
    const el = $('#s-wx-status');
    if (!el) return;
    let s;
    try { s = await HD.get('/api/wsjtx'); } catch (e) { return; }
    let msg;
    let kind = '';
    if (!s.enabled) {
      msg = 'Off. Tick the box and save to start listening.';
    } else if (!s.listening && s.error) {
      // Still trying, so this is what is in the way rather than a
      // failure: closing whatever holds the port is enough, and
      // restarting hamdash is not needed.
      msg = `${s.error} Still trying.`;
      kind = 'bad';
    } else if (!s.listening) {
      msg = 'Not listening — save to apply.';
    } else if (!s.connected) {
      // The common failure, and the one worth spelling out: our end is
      // fine and nothing is arriving.
      msg = `Listening on ${s.bind}:${s.port}, but WSJT-X has not said `
          + 'anything yet. Check its Reporting settings point here — it '
          + 'sends a heartbeat every 15 seconds, so this changes within the '
          + 'minute once they do.';
    } else if (s.adif_missing) {
      // Contacts are arriving as the structured message without the ADIF
      // one, so nothing can reach the log. Without this it reads as
      // nothing happening for no reason.
      msg = `WSJT-X ${s.version} is sending contacts but no ADIF records, so `
          + 'none of them can be logged. That version is too old; 2.0 and '
          + 'later send them.';
      kind = 'bad';
    } else {
      msg = `WSJT-X ${s.version || ''} connected on ${s.bind}:${s.port}`.trim()
          + ` · ${s.logged} contact${s.logged === 1 ? '' : 's'} logged`
          + (s.last_call ? ` · last ${s.last_call}` : '');
    }
    el.textContent = msg;
    el.className = 'sheet-note' + (kind ? ` ${kind}` : '');
  }

  // Polled only while the sheet is open: a heartbeat is every 15 s and
  // there is nothing to show when nobody is looking at it.
  function watchWsjtx(on) {
    clearInterval(wsjtxTimer);
    wsjtxTimer = null;
    if (on) {
      refreshWsjtx();
      wsjtxTimer = setInterval(refreshWsjtx, 5000);
    }
  }

  // ---- callsign lookup -------------------------------------------------
  function lookupNote(msg, kind) {
    const n = $('#s-lk-note');
    n.textContent = msg;
    n.className = 'sheet-note' + (kind ? ` ${kind}` : '');
  }

  // Which service the username and password are for only needs asking under
  // `auto`; naming a provider outright has already answered it.
  function refreshLookup() {
    $('#s-lk-account-row').hidden = $('#s-lk-provider').value !== 'auto';
  }

  // How much is remembered, so the button says whether it has anything to do.
  async function showCacheSize() {
    const btn = $('#s-lk-forget');
    try {
      const c = await HD.get('/api/lookup/cache');
      btn.disabled = !c.entries;
      btn.textContent = c.entries
        ? `Clear ${c.entries} remembered lookup${c.entries === 1 ? '' : 's'}`
        : 'Nothing remembered yet';
    } catch {
      btn.disabled = false;
      btn.textContent = 'Clear remembered lookups';
    }
  }

  $('#s-lk-forget').onclick = async (e) => {
    e.target.disabled = true;
    try {
      // An answer is kept for a month, which is a long time to be shown a
      // grid a station has since corrected with no way to say "ask again".
      const r = await HD.del('/api/lookup/cache');
      lookupNote(`Forgot ${r.cleared} remembered lookup`
        + `${r.cleared === 1 ? '' : 's'}. The next one asks the provider.`, 'ok');
    } catch (err) {
      lookupNote(err.message, 'warn');
    }
    showCacheSize();
  };
  $('#s-lk-provider').onchange = refreshLookup;

  $('#s-lk-test').onclick = async (e) => {
    e.target.disabled = true;
    lookupNote('Looking up\u2026');
    try {
      // Saved first, or the test would check whatever was configured last
      // time rather than what is on screen.
      await saveSettings({ quiet: true });
      const r = await HD.post('/api/lookup/test', {});
      lookupNote(`${r.source || r.provider} answered for ${r.callsign}`
        + `${r.name ? `: ${r.name}` : ''}${r.locator ? ` (${r.locator})` : ''}`,
        'ok');
    } catch (err) {
      lookupNote(err.message, 'warn');
    } finally {
      e.target.disabled = false;
      showCacheSize();
    }
  }

  // ---- filling in the grid --------------------------------------------
  function gridNote(msg, kind) {
    const n = $('#s-grid-note');
    n.textContent = msg;
    n.className = 'sheet-note' + (kind ? ` ${kind}` : '');
  }

  function setGrid(locator, how) {
    $('#s-grid').value = locator;
    gridNote(`${locator} — ${how}. Save to keep it.`, 'good');
  }

  // The operator's position. Asked for only on a click, turned into a locator
  // by the host and never written into a URL, so it does not end up in a log.
  $('#s-geo').onclick = () => {
    if (!navigator.geolocation || !window.isSecureContext) {
      gridNote('This browser will not give a location to a non-secure page. '
               + 'Use "From my callsign", or type the locator in.', 'warn');
      return;
    }
    gridNote('Asking your browser for a position…');
    navigator.geolocation.getCurrentPosition(
      async ({ coords }) => {
        try {
          const r = await HD.post('/api/grid/locator',
                                  { lat: coords.latitude, lon: coords.longitude });
          const acc = coords.accuracy ? ` (±${Math.round(coords.accuracy)} m)` : '';
          setGrid(r.locator, `from your device's position${acc}`);
        } catch (e) { gridNote(e.message, 'warn'); }
      },
      (err) => {
        const why = {
          1: 'Permission was refused. Allow location for this page, or use '
             + '"From my callsign".',
          2: 'Your device could not work out where it is. In the desktop app '
             + 'this is normal — the webview has no location provider. Use '
             + '"From my callsign", or type the locator in.',
          3: 'The request timed out.',
        }[err.code] || err.message;
        gridNote(why, 'warn');
      },
      // A 6-character locator is about 9 km across, so precise fixes buy
      // nothing and cost battery, a slower answer and a more invasive prompt.
      { enableHighAccuracy: false, timeout: 12000, maximumAge: 600000 });
  };

  $('#s-from-call').onclick = async () => {
    const call = $('#s-call').value.trim().toUpperCase();
    if (!call) { gridNote('Enter your callsign first.', 'warn'); return; }
    gridNote(`Looking up ${call}…`);
    try {
      const r = await HD.get(`/api/lookup/${encodeURIComponent(call)}`);
      if (r.note) { gridNote(r.note, 'warn'); return; }
      if (!r.locator) {
        gridNote(`${r.source} has no locator for ${call}.`, 'warn');
        return;
      }
      if (r.approximate) {
        // The prefix table answers with the middle of a country. Filling that
        // in would look like it worked and make every distance wrong.
        gridNote(`Only a rough guess for ${call} (${r.locator} is the middle `
                 + `of ${r.country || 'the country'}). Set a real lookup `
                 + `provider below, or type your locator in.`, 'warn');
        return;
      }
      setGrid(r.locator, `from ${r.source}`);
    } catch (e) { gridNote(e.message, 'warn'); }
  };
  $('#settings').onclick = () => {
    fillSettings();
    $('#settings-sheet').showModal();
    watchWsjtx(true);
  };
  $('#settings-cancel').onclick = () => $('#settings-sheet').close();
  // Whichever way the sheet closes - Cancel, Save, or Escape, which a
  // <dialog> handles itself and no button ever hears about.
  $('#settings-sheet').addEventListener('close', () => watchWsjtx(false));
  async function saveSettings({ quiet = false } = {}) {
    await HD.put('/api/config', {
      station: {
        callsign: $('#s-call').value.trim().toUpperCase(),
        grid: $('#s-grid').value.trim().toUpperCase(),
        operator: $('#s-op').value.trim(),
      },
      units: $('#s-units').value,
      theme: $('#s-theme').value,
      callsign_lookup: withSecret({
        provider: $('#s-lk-provider').value,
        account: $('#s-lk-account').value,
        user: $('#s-lk-user').value.trim(),
      }, 'password', '#s-lk-pass'),
      wsjtx: {
        enabled: $('#s-wx-enabled').checked,
        bind: $('#s-wx-bind').value.trim() || '127.0.0.1',
        port: Number($('#s-wx-port').value) || 2237,
      },
      logbook_sync: withSecret(withSecret(withSecret({
        lotw_user: $('#s-lotw-user').value.trim(),
      }, 'lotw_password', '#s-lotw-pass'),
        'qrz_api_key', '#s-qrz-key'),
        'wrl_api_key', '#s-wrl-key'),
    });
    // Radios live on their own endpoint, so Save has to send them as well -
    // without this, editing a COM port and pressing Save threw the edit away
    // unless the operator happened to press Test or Use this one first.
    try {
      await saveRigs();
    } catch (e) {
      throw new Error(`Radios: ${e.message}`);
    }
    config = await HD.get('/api/config');
    // The boxes now hold a stored secret again, not the text just typed.
    secretBox('#s-lk-pass', 'callsign_lookup.password');
    secretBox('#s-lotw-pass', 'logbook_sync.lotw_password');
    secretBox('#s-qrz-key', 'logbook_sync.qrz_api_key');
    secretBox('#s-wrl-key', 'logbook_sync.wrl_api_key');
    applyTheme(config.theme);
    paintStation();
    if (!quiet) {
      $('#settings-sheet').close();
      toast('Station saved');
    }
  }

  $('#settings-save').onclick = async () => {
    try {
      await saveSettings();
    } catch (e) {
      toast(e.message);
    }
  };

  function paintStation() {
    const s = config.station || {};
    $('#station').textContent = `${s.callsign || 'N0CALL'} · ${s.grid || '----'}`;
  }

  // ---- bus wiring -----------------------------------------------------
  HD.on('card.status', (p) => shell.status(p.id, p.status));
  HD.on('config.changed', (p) => { config = p; paintStation(); });

  // ---- boot -----------------------------------------------------------
  try {
    [cards, themes, config] = await Promise.all([
      HD.get('/api/cards'), HD.get('/api/themes'), HD.get('/api/config'),
    ]);
  } catch (e) {
    toast(`Host not answering: ${e.message}`);
    return;
  }

  const themeOpts = themes
    .map((t) => `<option value="${t.id}">${t.name || t.id}</option>`).join('');
  $('#theme-pick').innerHTML = themeOpts;
  $('#s-theme').innerHTML = themeOpts;
  $('#theme-pick').onchange = (e) => {
    applyTheme(e.target.value);
    HD.put('/api/config', { theme: e.target.value }).catch(() => {});
  };
  // In the sheet it previews straight away; Save is what makes it stick.
  $('#s-theme').onchange = (e) => applyTheme(e.target.value);
  applyTheme(config.theme);
  paintStation();

  HDPopout.onReturn = (uid) => {
    // window closed from the OS or from inside the card: put the tile back
    if (shell.get(uid)) shell.markPopped(uid, false);
  };
  HD.on('ui.pop_in', (p) => {
    if (p && p.uid && shell.get(p.uid)) shell.markPopped(p.uid, false);
  });

  const layout = await HD.get('/api/layout');
  buildShell(wantPhone() ? 'phone' : 'board');
  if (shellKind === 'board') {
    shell.cols = layout.cols || 12;
    shell.rowPx = layout.row_px || 80;
  }
  // With one shared stream, events reach a card page through its parent
  // rather than through its own filtered connection, so the filtering has to
  // happen here. The board already has every manifest.
  window.__hamdashRelayFilter = (frame, topic) => {
    const uid = new URL(frame.src, location.href).searchParams.get('uid');
    const entry = shell.get(uid);
    const card = entry && cards.find((c) => c.id === entry.card);
    if (!card) return true;                       // not a card frame
    const want = card.subscribes || [];
    if (!want.length) return true;                // undeclared means everything
    const ALWAYS = ['card.settings_changed', 'config.changed'];
    if (ALWAYS.includes(topic)) return true;
    if (topic === card.id || topic.startsWith(card.id + '.')) return true;
    return want.some((w) => topic === w
      || topic.startsWith(w.replace(/\*$/, '').replace(/\.$/, '') + '.'));
  };

  shell.setTiles(layout.tiles || []);
  cards.forEach((c) => shell.warn(c.id, c.warnings));
  renderCardsMenu();
  $('#empty').style.display = (layout.tiles || []).length ? 'none' : '';

  // Backend detection waits for the native bridge, which can take a moment.
  // Do it *after* the board is painted: the cards matter more than the
  // pop-out buttons, and blocking boot on it left an empty window.
  //
  // Not on the phone shell at all. There is no pop-out button there - a
  // second OS window means nothing on a phone - and `detect()` in a mobile
  // browser answers `none`, which would greet every phone with a toast
  // about installing pywebview.
  if (shellKind === 'board') HDPopout.detect().then(async (backend) => {
    document.body.dataset.popout = backend;
    if (backend === 'none') {
      toast('Pop-out unavailable: the desktop bridge did not load. '
        + 'pip install -U pywebview, or run with --browser.');
      return;
    }
    if (backend === 'pywebview') {
      // A popped tile holds exactly one card; the window is its page.
      for (const t of shell.tiles.filter((x) => x.popped)) {
        const entry = shell.get(t.active) || t.cards[0];
        try { await HDPopout.popOut(entry); } catch (e) { toast(e.message); }
      }
    }
  });

  // reopen windows that were popped out last session. Only the native shell
  // can do this unprompted; browsers need a click, so those tiles show a
  // button instead of silently failing a blocked window.open.
  shell.tiles.filter((t) => t.popped)
    .forEach((t) => shell.markPopped(t.active, true));

  // keep tile status dots honest
  setInterval(async () => {
    try {
      const fresh = await HD.get('/api/cards');
      fresh.forEach((c) => {
        shell.status(c.id, (c.runtime || {}).status);
        shell.warn(c.id, c.warnings);
      });
      cards = fresh;
    } catch { /* host restarting; the next tick will catch up */ }
  }, 5000);
})();
