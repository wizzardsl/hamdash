/* The phone shell: one card at a time, chosen from a bar along the bottom.
 *
 * The board is a pixel grid you drag tiles around on. At 375 px that is
 * twelve columns of thirty pixels - measured, a six-card board came out as
 * ~190 px windows two abreast, each one scrolled inside itself, with every
 * card mounted and polling at once. Nothing about that is fixable by making
 * the tiles narrower: the grid is the wrong shape for the screen.
 *
 * So the phone gets a different presentation of the *same* layout. No
 * second source of truth: what is on the board is what is in the bar, in
 * the order an operator reaches for it.
 *
 * Two rules it must keep, both of which the board already learned the hard
 * way:
 *
 *  - **Only one shell is ever live.** Build the board and the phone
 *    together and every card is mounted twice - two iframes, two
 *    listeners, two openers of one sound card. That is the bug
 *    `tests/test_rx_listeners.py` exists for, arrived at from the other
 *    end.
 *  - **A card that is not on screen is told so** (invariant 5b). Here that
 *    is most of them, all the time, which is exactly why it matters: a
 *    phone must not hold the radio's audio for a card nobody has opened
 *    since this morning.
 *
 * Mounting is lazy - a card is built the first time it is chosen - and
 * then kept, because switching back has to be instant and a rebuild
 * reloads the page and loses what was on it.
 */
(function (global) {
  // The same model the board uses, so "what is on the board" has one
  // implementation and the two shells cannot drift about what a layout is
  // (invariant 5a). All this file owns is the painting.
  const L = global.HDLayout;

  // What an operator reaches for on a phone, in that order. Working a
  // contact and seeing what is on the air are the two things you do
  // standing in a field; the radio and the audio are what you do sitting
  // in front of it. Anything else keeps its board order behind these.
  const PRIORITY = ['logbook', 'spots', 'rig_control', 'rx_audio'];

  // Four and More. Five fit across 375 px at 75 px each, and the fifth is
  // the way to everything else - a bar that scrolls sideways hides exactly
  // what it is for.
  const BAR = 4;

  const ICON = {
    logbook: '<path d="M4 3h9a3 3 0 0 1 3 3v12H7a3 3 0 0 0-3 3z"/><path d="M4 3v15"/>',
    spots: '<circle cx="11" cy="11" r="2.2"/><path d="M6.5 6.5a6.4 6.4 0 0 0 0 9M15.5 6.5a6.4 6.4 0 0 1 0 9"/><path d="M3.5 3.5a10.6 10.6 0 0 0 0 15M18.5 3.5a10.6 10.6 0 0 1 0 15"/>',
    rig_control: '<rect x="2.5" y="5.5" width="17" height="11" rx="2"/><circle cx="7" cy="11" r="2.4"/><path d="M12 9h5M12 12h5M12 15h3"/>',
    rx_audio: '<path d="M4 9v5h3l4 3V6L7 9z"/><path d="M14 9a3.6 3.6 0 0 1 0 5"/><path d="M16.5 6.5a7 7 0 0 1 0 10"/>',
    _: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="12" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="12" width="7" height="7" rx="1.5"/><rect x="12" y="12" width="7" height="7" rx="1.5"/>',
    _more: '<circle cx="5" cy="11" r="1.6"/><circle cx="11" cy="11" r="1.6"/><circle cx="17" cy="11" r="1.6"/>',
  };

  /* Is this screen one the board cannot be used on?
   *
   * Width, not the user agent string. A phone is a narrow viewport and a
   * narrow window is a narrow window - sniffing the UA gets this wrong in
   * both directions for ever, and the operator can overrule it either way
   * in one tap, which is the part that actually has to work.
   *
   * The second clause is a phone turned sideways: 812 px wide is not
   * narrow, but 375 px tall with a finger for a pointer is still no place
   * to drag a tile by a 28 px header.
   */
  function wants() {
    try {
      // A width of zero is a window that has not been laid out yet - a
      // hidden pane, a tab being restored - and it matches every
      // `max-width` query there is. Caught by booting into the phone
      // shell on a 1280 px window, where `wants()` was perfectly correct
      // a moment later; "narrow" and "nothing measured yet" are not the
      // same answer and must not look like one.
      if (!global.innerWidth) return false;
      return global.matchMedia('(max-width: 760px)').matches
        || (global.matchMedia('(pointer: coarse)').matches
            && global.innerHeight <= 560);
    } catch { return false; }
  }

  /* Call `fn` when the answer to `wants()` may have changed.
   *
   * A phone turned sideways, or a desktop window dragged narrow. The
   * media queries fire on crossing the threshold rather than on every
   * pixel, so this does not run during a drag - but `resize` does, hence
   * the settle: rebuilding a shell reloads every card in it. */
  function onChange(fn) {
    let t;
    const later = () => { clearTimeout(t); t = setTimeout(fn, 250); };
    try {
      global.matchMedia('(max-width: 760px)').addEventListener('change', later);
      global.matchMedia('(pointer: coarse)').addEventListener('change', later);
    } catch { /* older browser: the resize below still covers it */ }
    global.addEventListener('resize', later);
  }

  /* Which cards go in the bar, and in what order.
   *
   * Pure, and separated for that reason: the ordering is the one piece of
   * judgement in this file and `tests/js/phone.test.js` drives it without
   * a browser.
   */
  function order(tiles) {
    const out = [];
    (tiles || []).forEach((t) => (t.cards || []).forEach((c) => out.push(c)));
    const rank = (c) => {
      const i = PRIORITY.indexOf(c.card);
      return i < 0 ? PRIORITY.length : i;
    };
    // A stable sort by rank alone, so anything unranked keeps board order
    // rather than being shuffled into alphabetical nonsense.
    return out.map((c, i) => [c, i])
      .sort((a, b) => (rank(a[0]) - rank(b[0])) || (a[1] - b[1]))
      .map(([c]) => c);
  }

  /* How the bar is split: what is shown, and what goes behind More. */
  function split(entries) {
    if (entries.length <= BAR + 1) return { bar: entries, rest: [] };
    return { bar: entries.slice(0, BAR), rest: entries.slice(BAR) };
  }

  class Phone {
    constructor(el, opts) {
      this.el = el;
      this.renderBody = opts.renderBody;
      this.onChange = opts.onChange || (() => {});
      this.onSettings = opts.onSettings || (() => {});
      this.titleOf = opts.titleOf || ((c) => c.card);
      this.onError = null;
      this.tiles = [];
      this.active = null;                 // card uid
      this._holders = new Map();          // card uid -> holder element
      this._state = new Map();            // card id -> process status
      this._configurable = new Set();     // card uids with settings
      el.innerHTML = `
        <div class="phone-bar">
          <span class="phone-title" id="phone-title"></span>
          <span class="phone-dot" id="phone-dot" title="status"></span>
          <span class="phone-grow"></span>
          <button class="tile-btn" data-act="settings" title="Card settings" hidden>⚙</button>
          <button class="tile-btn" data-act="reload" title="Reload card">↻</button>
        </div>
        <div class="phone-stage" id="phone-stage"></div>
        <p class="phone-empty" id="phone-empty" hidden>Nothing on the board yet.</p>
        <nav class="phone-nav" id="phone-nav" aria-label="Cards"></nav>`;
      this.stage = el.querySelector('#phone-stage');
      this.nav = el.querySelector('#phone-nav');
      el.querySelector('[data-act="reload"]').onclick = () => {
        const f = this.stage.querySelector(
          `.tile-card[data-uid="${this.active}"] iframe`);
        if (f) f.src = f.src;
      };
      el.querySelector('[data-act="settings"]').onclick = () => {
        const c = this.get(this.active);
        if (c) this.onSettings(c);
      };
    }

    setTiles(tiles) {
      this.tiles = L.normalise(tiles || []);
      const entries = order(this.tiles);
      // A card that has left the board takes its iframe with it.
      const want = new Set(entries.map((c) => c.uid));
      [...this._holders.keys()].forEach((uid) => {
        if (want.has(uid)) return;
        const h = this._holders.get(uid);
        if (h) h.remove();
        this._holders.delete(uid);
      });
      if (!entries.length) {
        this.active = null;
      } else if (!want.has(this.active)) {
        this.active = entries[0].uid;
      }
      this.el.querySelector('#phone-empty').hidden = !!entries.length;
      this._paintNav(entries);
      if (this.active) this.show(this.active);
    }

    get(cardUid) { return L.cardOf(this.tiles, cardUid); }

    stackOf(cardUid) { return L.stackOf(this.tiles, cardUid); }

    get cards() { return L.allCards(this.tiles); }

    /* Adding and removing are model operations, so they go through the
       same `HDLayout` the board uses - a card added from a phone has to
       land somewhere sensible on the board the operator opens later.
       A phone has no geometry of its own to offer, so the defaults are
       the board's. */
    add(tile) {
      const t = L.asStack({ x: 0, y: L.nextRow(this.tiles), w: 4, h: 3, ...tile });
      this.setTiles([...this.tiles, t]);
      // Adding a card on a phone means you want to look at it.
      if (t.active) this.show(t.active);
      this.onChange(this.tiles);
      return t;
    }

    remove(cardUid) {
      const got = L.removeCard(this.tiles, cardUid);
      if (!got.changed) return;
      this.setTiles(got.tiles);
      this.onChange(this.tiles);
    }

    /* The board calls this a tile; here it is just which card is up. */
    show(cardUid) {
      const entry = this.get(cardUid);
      if (!entry) return;
      this.active = cardUid;
      this._mount(entry);
      this._holders.forEach((holder, uid) => {
        const on = uid === cardUid;
        holder.classList.toggle('on', on);
        global.HDFrames.tellVisible(holder, on);
      });
      this.nav.querySelectorAll('.pnav').forEach((b) => {
        const on = b.dataset.uid === cardUid;
        b.classList.toggle('on', on);
        b.setAttribute('aria-current', on ? 'true' : 'false');
      });
      this.el.querySelector('#phone-title').textContent = this.titleOf(entry);
      this.el.querySelector('[data-act="settings"]').hidden =
        !this._configurable.has(cardUid);
      this._paintStatus();
    }

    /* Built the first time it is asked for, and kept afterwards.
     *
     * Mounting all of them up front is what the board does, and on a phone
     * that is six card processes started and six pages polling so that one
     * of them can be looked at. Keeping them once built is the other half:
     * a rebuild reloads the iframe, which loses a half-typed contact. */
    _mount(entry) {
      if (this._holders.has(entry.uid)) return;
      const holder = document.createElement('div');
      holder.className = 'tile-card';
      holder.dataset.uid = entry.uid;
      this.stage.appendChild(holder);
      this._holders.set(entry.uid, holder);
      if (this.renderBody) this.renderBody(entry, holder, null);
    }

    _paintNav(entries) {
      const { bar, rest } = split(entries);
      const btn = (c) => `
        <button class="pnav" type="button" data-uid="${esc(c.uid)}"
                aria-current="false">
          <svg class="pnav-i" viewBox="0 0 22 22" fill="none" stroke="currentColor"
               stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"
               aria-hidden="true">${ICON[c.card] || ICON._}</svg>
          <span class="pnav-n">${esc(this.titleOf(c))}</span>
          <span class="pnav-dot" data-card="${esc(c.card)}"></span>
        </button>`;
      this.nav.innerHTML = bar.map(btn).join('') + (rest.length ? `
        <button class="pnav" type="button" data-act="more">
          <svg class="pnav-i" viewBox="0 0 22 22" fill="none" stroke="currentColor"
               stroke-width="1.6" stroke-linecap="round" aria-hidden="true">${ICON._more}</svg>
          <span class="pnav-n">More</span>
        </button>` : '');
      this.nav.querySelectorAll('.pnav[data-uid]').forEach((b) => {
        b.onclick = () => this.show(b.dataset.uid);
      });
      const more = this.nav.querySelector('[data-act="more"]');
      if (more) more.onclick = () => this._more(rest);
      this._paintStatus();
    }

    _more(rest) {
      const sheet = document.querySelector('#phone-more');
      const list = sheet.querySelector('#phone-more-list');
      list.innerHTML = rest.map((c) => `
        <li><button class="btn btn-quiet" data-uid="${esc(c.uid)}">
          ${esc(this.titleOf(c))}</button></li>`).join('');
      list.querySelectorAll('button[data-uid]').forEach((b) => {
        b.onclick = () => { sheet.close(); this.show(b.dataset.uid); };
      });
      sheet.showModal();
    }

    /* The same amber/green/red the tab dots carry, so a card that is not
       running says so without being opened. */
    status(cardId, state) {
      this._state.set(cardId, state);
      this._paintStatus();
    }

    _paintStatus() {
      const mark = (el, cardId) => {
        if (!el) return;
        const s = this._state.get(cardId);
        el.classList.toggle('up', s === 'running' || s === 'n/a');
        el.classList.toggle('down', s === 'failed' || s === 'stopped');
      };
      this.nav.querySelectorAll('.pnav-dot').forEach((d) => {
        mark(d, d.dataset.card);
      });
      const live = this.get(this.active);
      mark(this.el.querySelector('#phone-dot'), live && live.card);
    }

    setConfigurable(cardUid, yes) {
      if (yes) this._configurable.add(cardUid);
      else this._configurable.delete(cardUid);
      if (cardUid === this.active) {
        this.el.querySelector('[data-act="settings"]').hidden = !yes;
      }
    }

    /* The board has these and app.js calls them whichever shell is up.
       Nothing on a phone drags, pops out or warns in a tile header. */
    warn() {}
    reflow() {}
    markPopped() {}

    /* Every card's iframe goes, and each one is told it is hidden on the
       way out - a card that is torn down without being told can be left
       holding a sound card for the length of its own reaper. */
    destroy() {
      this._holders.forEach((holder) => {
        global.HDFrames.tellVisible(holder, false);
        holder.remove();
      });
      this._holders.clear();
      this.el.innerHTML = '';
    }
  }

  const esc = (v) => String(v ?? '').replace(/[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  global.HDPhone = { Phone, wants, onChange, order, split, PRIORITY, BAR };
})(window);
