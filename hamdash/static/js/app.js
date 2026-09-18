/* app.js — the shell: themes, card palette, settings, layout persistence. */
(async function () {
  const $ = (s) => document.querySelector(s);
  const gridEl = $('#grid');
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

  // ---- themes ---------------------------------------------------------
  function applyTheme(id) {
    const t = themes.find((x) => x.id === id) || themes[0];
    if (!t) return;
    document.documentElement.dataset.theme = t.id;
    Object.entries(t.vars || {}).forEach(([k, v]) => {
      document.documentElement.style.setProperty(k, v);
    });
    $('#theme-pick').value = t.id;
  }

  // ---- board ----------------------------------------------------------
  const board = new Board(gridEl, {
    cols: 12,
    rowPx: 80,
    onChange: saveLayout,
    onPopOut: (tile) => HDPopout.popOut(tile),
    onPopIn: (tile) => HDPopout.popIn(tile.uid),
    onReopen: (tile) => HDPopout.popOut(tile),
    isPoppedOpen: (uid) => HDPopout.isOpen(uid),
    onSettings: (tile) => openTileSettings(tile),
    renderBody: (tile, host) => {
      const card = cards.find((c) => c.id === tile.card);
      const frame = document.createElement('iframe');
      const st = tile.settings || {};
      if (card && card.kind === 'url') {
        frame.src = st.url || tile.url || card.url;
      } else {
        // the tile uid goes with it: one process, many tiles
        frame.src = `/cards/${tile.card}/?uid=${encodeURIComponent(tile.uid)}`;
      }
      frame.setAttribute('title', tile.title || tile.card);
      frame.setAttribute('referrerpolicy', 'no-referrer');
      host.appendChild(frame);
      board.setConfigurable(tile.uid, !!(card && card.schema && card.schema.length));
      if (card && card.kind === 'python') {
        HD.post(`/api/cards/${card.id}/start`).then((i) => {
          board.status(card.id, i.status);
        }).catch(() => board.status(card.id, 'failed'));
      } else {
        board.status(tile.card, 'n/a');
      }
    },
  });

  let saveTimer;
  function saveLayout(tiles) {
    $('#empty').style.display = tiles.length ? 'none' : '';
    renderCardsMenu();      // a tile closed with its own x unticks it here
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      HD.put('/api/layout', {
        tiles: tiles.map(({ uid, card, x, y, w, h, title, url, settings,
                           popped, pop_w, pop_h, on_top }) =>
          ({ uid, card, x, y, w, h, title, url, settings: settings || {},
             popped: !!popped, pop_w, pop_h, on_top })),
      }).catch((e) => toast(e.message));
    }, 300);
  }

  board.onError = (msg) => toast(msg);

  // ---- per-tile settings ----------------------------------------------
  let settingTile = null;

  function fieldRow(f, value) {
    const row = document.createElement('label');
    row.className = f.type === 'bool' ? 'row' : '';
    const id = `set-${f.name}`;
    let input;
    if (f.type === 'select') {
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
      else input.value = value ?? f.default ?? '';
      if (f.placeholder) input.placeholder = f.placeholder;
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
      values = (await HD.get(`/api/tiles/${tile.uid}/settings`)).values || values;
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
      board.markPopped(settingTile.uid, !!settingTile.popped);   // repaint
      const node = document.querySelector(`.tile[data-uid="${settingTile.uid}"] iframe`);
      if (node) node.src = node.src;
      $('#tile-sheet').close();
      toast('Settings saved');
    } catch (e) {
      toast(e.message);
    }
  };

  // ---- palette --------------------------------------------------------
  function renderPalette() {
    const ul = $('#palette-list');
    ul.innerHTML = '';
    cards.forEach((c) => {
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
        board.add(def);
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
  // Ticking a card puts one tile on the board; unticking takes its tiles off.
  // A card can legitimately have several tiles (two web_embed tiles are two
  // pages), so the row shows the count and unticking several asks first -
  // each tile carries its own settings and they go with it.
  const menuEl = () => $('#cards-menu');

  function tilesOf(cardId) {
    return board.tiles.filter((t) => t.card === cardId);
  }

  function renderCardsMenu() {
    const ul = $('#cards-menu-list');
    if (!ul) return;
    ul.innerHTML = '';
    cards.forEach((c) => {
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
      ul.appendChild(li);
    });
  }

  function toggleCard(c) {
    const mine = tilesOf(c.id);
    if (!mine.length) {
      board.add({ card: c.id, title: c.name, w: c.w, h: c.h,
                  min_w: c.min_w, min_h: c.min_h, url: c.url });
      toast(`${c.name} added`);
    } else {
      // Undo rather than a confirm dialog. A card can have several tiles and
      // each carries its own settings, so taking them off is worth being able
      // to take back - and a browser confirm() is the one bit of chrome this
      // app has never used, for the same reason it never uses window.open.
      const restore = mine.map((t) => ({ ...t, popped: false }));
      mine.forEach((t) => board.remove(t.uid));
      toast(`${c.name} removed${mine.length > 1 ? ` (${mine.length} tiles)` : ''}`,
            { label: 'Undo',
              fn: () => {
                restore.forEach((t) => board.add(t));   // add() keeps the uid,
                renderCardsMenu();                      // so settings come back
                toast(`${c.name} restored`);
              } });
    }
    renderCardsMenu();
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
        syncNote('lotw', 'Enter your LoTW username and password, then save.');
      } else if (!s.lotw.can_push) {
        syncNote('lotw', 'Ready to pull. Push needs TQSL, which is not '
                 + 'installed here — pulling works either way.');
      } else {
        syncNote('lotw', `Ready. TQSL found at ${s.lotw.tqsl}`, 'good');
      }
      syncNote('qrz', s.qrz.configured
        ? 'Ready.' : 'Paste the logbook API key, then save.',
        s.qrz.configured ? 'good' : '');
    } catch (e) { /* settings can open before the host is warm */ }
  }

  const VERBS = { pull: 'Pulling', push: 'Pushing', test: 'Testing' };

  function wireSync(service, direction) {
    const el = $(`#s-${service}-${direction}`);
    if (!el) return;
    el.onclick = async () => {
      syncNote(service, `${VERBS[direction]}…`);
      try {
        await HD.post(`/api/log/sync/${service}/${direction}`, {
          since: $('#s-sync-since').value.trim(),
          // Defaults to true server side too, so a caller that forgets this
          // counts rather than uploads.
          dry_run: direction === 'push' ? $('#s-sync-dry').checked : false,
        });
      } catch (e) { syncNote(service, e.message, 'warn'); }
    };
  }
  ['lotw', 'qrz'].forEach((s) =>
    ['test', 'pull', 'push'].forEach((d) => wireSync(s, d)));

  HD.on('log.sync', (p) => {
    const which = (p.job || '').startsWith('qrz') ? 'qrz' : 'lotw';
    if (p.running) { syncNote(which, p.message); return; }
    // Deliberately not calling refreshSync() here: it would immediately
    // overwrite "1,234 QSOs on QRZ" with "Ready.", which is how the most
    // useful message in the whole panel disappeared half a second after
    // arriving.
    syncNote(which, p.message, p.ok ? 'good' : 'warn');
  });

  // ---- settings -------------------------------------------------------
  function fillSettings() {
    $('#s-call').value = config.station.callsign || '';
    $('#s-grid').value = config.station.grid || '';
    $('#s-op').value = config.station.operator || '';
    $('#s-mock').checked = !!config.radio.mock;
    $('#s-rig-host').value = config.radio.rigctld_host || '';
    $('#s-rig-port').value = config.radio.rigctld_port || 4532;
    const sy = config.logbook_sync || {};
    $('#s-lotw-user').value = sy.lotw_user || '';
    $('#s-lotw-pass').value = sy.lotw_password || '';
    $('#s-qrz-key').value = sy.qrz_api_key || '';
    refreshSync();
    const lk = config.callsign_lookup || {};
    $('#s-lk-provider').value = lk.provider || 'callook';
    $('#s-lk-user').value = lk.user || '';
    $('#s-lk-pass').value = lk.password || '';
    gridNote('');
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
  $('#settings').onclick = () => { fillSettings(); $('#settings-sheet').showModal(); };
  $('#settings-cancel').onclick = () => $('#settings-sheet').close();
  $('#settings-save').onclick = async () => {
    try {
      await HD.put('/api/config', {
        station: {
          callsign: $('#s-call').value.trim().toUpperCase(),
          grid: $('#s-grid').value.trim().toUpperCase(),
          operator: $('#s-op').value.trim(),
        },
        radio: {
          mock: $('#s-mock').checked,
          rigctld_host: $('#s-rig-host').value.trim(),
          rigctld_port: Number($('#s-rig-port').value) || 4532,
        },
        callsign_lookup: {
          provider: $('#s-lk-provider').value,
          user: $('#s-lk-user').value.trim(),
          password: $('#s-lk-pass').value,
        },
        logbook_sync: {
          lotw_user: $('#s-lotw-user').value.trim(),
          lotw_password: $('#s-lotw-pass').value,
          qrz_api_key: $('#s-qrz-key').value.trim(),
        },
      });
      config = await HD.get('/api/config');
      paintStation();
      $('#settings-sheet').close();
      toast('Station saved');
    } catch (e) {
      toast(e.message);
    }
  };

  function paintStation() {
    const s = config.station || {};
    $('#station').textContent = `${s.callsign || 'N0CALL'} · ${s.grid || '----'}`;
  }

  // ---- bus wiring -----------------------------------------------------
  HD.on('card.status', (p) => board.status(p.id, p.status));
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

  $('#theme-pick').innerHTML = themes
    .map((t) => `<option value="${t.id}">${t.name || t.id}</option>`).join('');
  $('#theme-pick').onchange = (e) => {
    applyTheme(e.target.value);
    HD.put('/api/config', { theme: e.target.value }).catch(() => {});
  };
  applyTheme(config.theme);
  paintStation();

  HDPopout.onReturn = (uid) => {
    // window closed from the OS or from inside the card: put the tile back
    if (board.get(uid)) board.markPopped(uid, false);
  };
  HD.on('ui.pop_in', (p) => {
    if (p && p.uid && board.get(p.uid)) board.markPopped(p.uid, false);
  });

  const layout = await HD.get('/api/layout');
  board.cols = layout.cols || 12;
  board.rowPx = layout.row_px || 80;
  // With one shared stream, events reach a card page through its parent
  // rather than through its own filtered connection, so the filtering has to
  // happen here. The board already has every manifest.
  window.__hamdashRelayFilter = (frame, topic) => {
    const uid = new URL(frame.src, location.href).searchParams.get('uid');
    const tile = (board.tiles || []).find((t) => t.uid === uid);
    const card = tile && cards.find((c) => c.id === tile.card);
    if (!card) return true;                       // not a card frame
    const want = card.subscribes || [];
    if (!want.length) return true;                // undeclared means everything
    const ALWAYS = ['card.settings_changed', 'config.changed'];
    if (ALWAYS.includes(topic)) return true;
    if (topic === card.id || topic.startsWith(card.id + '.')) return true;
    return want.some((w) => topic === w
      || topic.startsWith(w.replace(/\*$/, '').replace(/\.$/, '') + '.'));
  };

  board.setTiles(layout.tiles || []);
  renderCardsMenu();
  $('#empty').style.display = (layout.tiles || []).length ? 'none' : '';

  // Backend detection waits for the native bridge, which can take a moment.
  // Do it *after* the board is painted: the cards matter more than the
  // pop-out buttons, and blocking boot on it left an empty window.
  HDPopout.detect().then(async (backend) => {
    document.body.dataset.popout = backend;
    if (backend === 'none') {
      toast('Pop-out unavailable: the desktop bridge did not load. '
        + 'pip install -U pywebview, or run with --browser.');
      return;
    }
    if (backend === 'pywebview') {
      for (const t of board.tiles.filter((x) => x.popped)) {
        try { await HDPopout.popOut(t); } catch (e) { toast(e.message); }
      }
    }
  });

  // reopen windows that were popped out last session. Only the native shell
  // can do this unprompted; browsers need a click, so those tiles show a
  // button instead of silently failing a blocked window.open.
  board.tiles.filter((t) => t.popped).forEach((t) => board.markPopped(t.uid, true));

  // keep tile status dots honest
  setInterval(async () => {
    try {
      const fresh = await HD.get('/api/cards');
      fresh.forEach((c) => board.status(c.id, (c.runtime || {}).status));
      cards = fresh;
    } catch { /* host restarting; the next tick will catch up */ }
  }, 5000);
})();
