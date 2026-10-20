/* grid.js — absolute-positioned tiles snapped to a column grid.
 *
 * A tile is a box holding one or more cards, shown one at a time behind a
 * tab strip:
 *
 *   {uid, x, y, w, h, active, popped, cards: [{uid, card, title, url}, ...]}
 *
 * A plain tile is a box of one, so there is one shape and one code path
 * rather than a special case for stacks. Drag the header to move, the corner
 * to resize, drag a tile off the board (or hit ⧉) to pop it out, drop a tile
 * onto another to dock it as a tab, and drag a tab off its strip to pull it
 * back out into a tile of its own.
 *
 * **A card keeps its uid wherever it goes.** Per-instance state hangs off it,
 * so docking and undocking must move the entry, never rebuild it.
 *
 * **Every card in a stack stays mounted.** Switching tabs would otherwise
 * reload the card and lose whatever was on screen - a decoder's text, a
 * half-typed contact. The hidden ones are told they are hidden instead
 * (`_tellVisible`), because a CSS-hidden iframe gets no visibilitychange of
 * its own and a hidden card that owns the sound card would hold it for ever.
 *
 * Overlap is resolved by pushing tiles down, never sideways: horizontal
 * position is the thing people arrange deliberately, vertical order isn't.
 */
(function (global) {
  const POP_MARGIN = 48;      // px outside the board before a drag means "pop out"
  const TEAR_OFF = 14;        // px a tab must travel before it leaves its stack
  const DOCK_BAND = 40;       // px of a tile's top that means "dock as a tab"
  const EDGE = 60;            // px inside the board's edge where a drag scrolls it
  // Pixels per *second*, not per frame. A frame-based rate is whatever the
  // machine happens to be managing - measured here swinging between 830 and
  // 34 px/s on the same board for the same gesture - so the distance is
  // worked out from the clock and the frame rate only decides how smooth
  // it looks.
  const EDGE_SLOW = 140;      // px/s once the pointer is just inside the edge
  const EDGE_FAST = 1500;     // px/s hard against it

  // What a board *is* lives in layout.js, with no DOM: normalising, docking,
  // undocking, overlap. This file owns the pixels and the event handlers and
  // calls in for the rest, so there is one implementation of the model and
  // `tests/js/layout.test.js` can drive it without a browser.
  const L = global.HDLayout;

  class Board {
    constructor(el, opts) {
      this.el = el;
      this.cols = opts.cols || 12;
      this.rowPx = opts.rowPx || 80;
      this.gap = opts.gap || 10;
      this.onChange = opts.onChange || (() => {});
      this.renderBody = opts.renderBody;
      this.onPopOut = opts.onPopOut || (() => {});
      this.onPopIn = opts.onPopIn || (() => {});
      this.onReopen = opts.onReopen || (() => {});
      this.onSettings = opts.onSettings || (() => {});
      // Closing a card throws away its per-tile settings, so the board gets
      // to decide what that costs - it is the only part that can offer Undo.
      this.onClose = opts.onClose || ((uid) => this.remove(uid));
      // "You can drop this on a tab strip" is worth saying once, and only
      // to somebody who has never done it. The board decides that; this
      // just says when a drag starts and when one ends in a dock.
      this.onHint = opts.onHint || (() => {});
      this.onDocked = opts.onDocked || (() => {});
      this.isPoppedOpen = opts.isPoppedOpen || (() => true);
      this.tiles = [];
      this.arranging = false;
      this._nodes = new Map();          // tile uid -> element
      this._state = new Map();          // card id -> process status
      this._warnings = new Map();       // card id -> manifest complaints
      global.addEventListener('resize', () => this.reflow());
      this._dropTarget();
    }

    get cellW() {
      const inner = this.el.clientWidth - this.gap * 2;
      return (inner - this.gap * (this.cols - 1)) / this.cols;
    }

    setTiles(tiles) {
      this.tiles = L.normalise(tiles);
      this.el.querySelectorAll('.tile').forEach((n) => n.remove());
      this._nodes.clear();
      this.tiles.forEach((t) => this._mount(t));
      this.reflow();
    }

    /* `tile` may be a card ({card, uid, ...}) or an already-built stack. */
    add(tile) {
      const t = L.asStack({ x: 0, y: this._nextRow(), w: 4, h: 3, ...tile });
      t.x = clamp(t.x, 0, Math.max(0, this.cols - t.w));
      this.tiles.push(t);
      this._mount(t);
      this._resolve(t);
      this.reflow();
      this.onChange(this.tiles);
      return t;
    }

    /* Remove a card. The tile goes with it only when it was the last one. */
    remove(cardUid) {
      const t = this.stackOf(cardUid);
      if (!t) return;
      if (t.popped) this.onPopIn(t);             // don't orphan its window
      const got = L.removeCard(this.tiles, cardUid);
      if (!got.changed) return;
      this.tiles = got.tiles;
      if (got.emptied) this._remount(got.emptied);
      if (got.dropped) this._drop(got.dropped);
      this.onChange(this.tiles);
    }

    /* Forget a tile's element. The model has already let go of the tile. */
    _drop(t) {
      this._nodes.get(t.uid)?.remove();
      this._nodes.delete(t.uid);
    }

    /* The card with this uid, wherever it is. */
    get(cardUid) {
      return L.cardOf(this.tiles, cardUid);
    }

    /* The box a card sits in. */
    stackOf(cardUid) {
      return L.stackOf(this.tiles, cardUid);
    }

    /* Every card on the board, in board order. */
    get cards() {
      return L.allCards(this.tiles);
    }

    show(cardUid) {
      const t = this.stackOf(cardUid);
      if (!t || t.active === cardUid) return;
      t.active = cardUid;
      const node = this._nodes.get(t.uid);
      if (node) this._paintTabs(t, node);
      this._showActive(t);
      this.onChange(this.tiles);
    }

    /* ---- stacking ---------------------------------------------------- */

    /* Move a card into another tile, as a tab. Returns false if it would be
       a no-op, so a drag that ends where it started is not a layout change. */
    dock(cardUid, ontoTileUid) {
      const got = L.dock(this.tiles, cardUid, ontoTileUid);
      if (!got.changed) return false;
      this.tiles = got.tiles;
      if (got.from) this._remount(got.from);
      if (got.dropped) this._drop(got.dropped);
      this._remount(got.onto);
      this.reflow();
      this.onChange(this.tiles);
      return true;
    }

    /* Pull a tab back out into a tile of its own, next to the one it left. */
    undock(cardUid, where) {
      const got = L.undock(this.tiles, cardUid, where, this.cols);
      if (!got.changed) return null;
      this.tiles = got.tiles;
      this._remount(got.from);
      this._mount(got.made);
      this._resolve(got.made);
      this.reflow();
      this.onChange(this.tiles);
      return got.made;
    }

    setArranging(on) {
      this.arranging = on;
      this.el.classList.toggle('arranging', on);
      this.reflow();
    }

    /* ---- pop out / pop in ------------------------------------------- */

    /* All three take a *card* uid. A popped-out window holds one card page
       with no tab strip of its own, so popping a tab takes it out of its
       stack first and popping it back in puts it where it came from. */

    markPopped(cardUid, popped) {
      const t = this.stackOf(cardUid);
      if (!t) return;
      t.popped = !!popped;
      const node = this._nodes.get(t.uid);
      if (node) this._paintBody(t, node);
      this.onChange(this.tiles);
    }

    async popOut(cardUid) {
      let t = this.stackOf(cardUid);
      if (!t || t.popped) return;
      const card = this.get(cardUid);
      if (t.cards.length > 1) {
        // Out of the stack and into a tile of its own, remembering where it
        // lived so it can go back there.
        const was = t.uid;
        t = this.undock(cardUid, { x: t.x, y: t.y });
        if (!t) return;
        t.from_stack = was;
      }
      try {
        await this.onPopOut(card);
        this.markPopped(cardUid, true);
      } catch (e) {
        const node = this._nodes.get(t.uid);
        if (node) node.classList.add('tile-error');
        throw e;
      }
    }

    async popIn(cardUid) {
      const t = this.stackOf(cardUid);
      if (!t) return;
      await this.onPopIn(this.get(cardUid));
      this.markPopped(cardUid, false);
      // Back into the stack it was pulled out of, if that stack is still
      // there. If it has gone, the card stays where it is rather than
      // disappearing to look for it.
      const home = t.from_stack;
      delete t.from_stack;
      if (home && this.tiles.some((x) => x.uid === home)) {
        this.dock(cardUid, home);
      }
    }

    /* ---- mounting ---------------------------------------------------- */

    _mount(t) {
      const node = document.createElement('section');
      node.className = 'tile';
      node.dataset.uid = t.uid;
      node.innerHTML = `
        <header class="tile-head" tabindex="0">
          <span class="tile-grip" aria-hidden="true">···</span>
          <span class="tile-status" title="status"></span>
          <span class="tile-tabs"></span>
          <span class="tile-spacer"></span>
          <button class="tile-btn" data-act="settings" title="Card settings" hidden>⚙</button>
          <button class="tile-btn" data-act="popout" title="Pop out into its own window">⧉</button>
          <button class="tile-btn" data-act="reload" title="Reload card">↻</button>
          <button class="tile-btn" data-act="close" title="Remove card">✕</button>
        </header>
        <div class="tile-body"></div>
        <div class="resize" data-act="resize" title="Resize"></div>`;
      this.el.appendChild(node);
      this._nodes.set(t.uid, node);
      this._paintTabs(t, node);
      this._paintBody(t, node);
      this._sayPlace(t, node.querySelector('.tile-head'));

      // The header buttons act on whichever card is showing, not on the box.
      const live = () => this.get(t.active) || t.cards[0];
      this._wireKeys(t, node);
      node.querySelector('[data-act="close"]').onclick = () =>
        this.onClose(t.active);
      node.querySelector('[data-act="reload"]').onclick = () => {
        const f = node.querySelector(`iframe[data-uid="${t.active}"]`);
        if (f) f.src = f.src;
      };
      node.querySelector('[data-act="settings"]').onclick = () =>
        this.onSettings(live());
      node.querySelector('[data-act="popout"]').onclick = () => {
        (t.popped ? this.popIn(t.active) : this.popOut(t.active))
          .catch((e) => this.onError && this.onError(e.message));
      };
      node.querySelector('.tile-head').addEventListener('pointerdown', (e) => {
        if (e.target.closest('.tile-btn') || e.target.closest('.tab')) return;
        this._drag(e, t, node, 'move');
      });
      node.querySelector('.resize').addEventListener('pointerdown', (e) => {
        this._drag(e, t, node, 'resize');
      });
      return node;
    }

    /* Rebuild a tile in place, keeping the iframes of the cards that stayed.
       Re-creating them would reload every card in the stack because one of
       its neighbours moved. */
    _remount(t) {
      const node = this._nodes.get(t.uid);
      if (!node) return;
      this._paintTabs(t, node);
      this._paintBody(t, node);
    }

    _paintTabs(t, node) {
      const strip = node.querySelector('.tile-tabs');
      const many = t.cards.length > 1;
      node.classList.toggle('stacked', many);
      strip.innerHTML = t.cards.map((c) => `
        <button class="tab${c.uid === t.active ? ' on' : ''}"
                data-uid="${c.uid}" type="button"
                aria-pressed="${c.uid === t.active}"
                aria-label="${esc(c.title || c.card)}">
          <span class="tab-dot" data-card="${esc(c.card)}"></span>
          <span class="tab-name">${esc(c.title || c.card)}</span>
          ${many ? '<span class="tab-x" data-close="1" title="Remove this card">×</span>' : ''}
        </button>`).join('');

      strip.querySelectorAll('.tab').forEach((tab) => {
        const cardUid = tab.dataset.uid;
        tab.addEventListener('pointerdown', (e) => {
          if (e.target.closest('[data-close]')) return;
          this._dragTab(e, t, node, cardUid);
        });
        tab.addEventListener('click', (e) => {
          if (e.target.closest('[data-close]')) { this.onClose(cardUid); return; }
          this.show(cardUid);
        });
      });
      this._paintStatus(t, node);
    }

    /* A card's name changed - its radio was renamed in Settings. Only the
       tab strip is repainted: rebuilding the tile would reload the card. */
    retitle(cardUid, title) {
      const t = this.stackOf(cardUid);
      const c = this.get(cardUid);
      if (!t || !c || c.title === title) return;
      c.title = title;
      const node = this._nodes.get(t.uid);
      if (node) this._paintTabs(t, node);
      this.onChange(this.tiles);
    }

    /* Only show the gear on cards that actually declare settings. */
    setConfigurable(cardUid, yes) {
      const t = this.stackOf(cardUid);
      if (!t) return;
      const card = t.cards.find((c) => c.uid === cardUid);
      if (card) card._configurable = !!yes;
      if (t.active !== cardUid) return;
      const btn = this._nodes.get(t.uid)?.querySelector('[data-act="settings"]');
      if (btn) btn.hidden = !yes;
    }

    _paintBody(t, node) {
      const body = node.querySelector('.tile-body');
      node.classList.toggle('popped', !!t.popped);
      node.querySelector('[data-act="popout"]').textContent = t.popped ? '⤓' : '⧉';
      node.querySelector('[data-act="popout"]').title = t.popped
        ? 'Bring this card back onto the board'
        : 'Pop out into its own window';
      if (!t.popped) {
        // Mount what is missing, drop what has left, keep the rest. A card
        // that is still in this tile must not be rebuilt because one of its
        // neighbours moved: rebuilding reloads the page and loses whatever
        // was on it.
        //
        // *Everything* that is not a wanted card goes, not just cards that
        // have left. This used to prune `.tile-card` only, so the "Running
        // in its own window" placeholder outlived the pop-in that ended it:
        // it is `height: 100%`, so the card came back mounted but pushed
        // out of sight underneath it, and the stale "Bring it back" button
        // did nothing because the tile was already back. It read as the
        // pop-in being broken, and only a restart cleared it.
        const want = new Set(t.cards.map((c) => c.uid));
        [...body.children].forEach((n) => {
          const keep = n.classList.contains('tile-card')
                    && want.has(n.dataset.uid);
          if (!keep) n.remove();
        });
        t.cards.forEach((c) => {
          if (body.querySelector(`.tile-card[data-uid="${c.uid}"]`)) return;
          const holder = document.createElement('div');
          holder.className = 'tile-card';
          holder.dataset.uid = c.uid;
          body.appendChild(holder);
          if (this.renderBody) this.renderBody(c, holder, t);
        });
        this._showActive(t);
        return;
      }
      // Popped out. A popped tile always holds exactly one card, because
      // popping a tab takes it out of its stack first - the window is a
      // plain card page with no tab strip of its own.
      body.innerHTML = '';
      const card = this.get(t.active) || t.cards[0];
      const live = this.isPoppedOpen(card.uid);
      const ph = document.createElement('div');
      ph.className = 'tile-placeholder';
      ph.innerHTML = `
        <p>${live ? 'Running in its own window.' : 'Popped out, window closed.'}</p>
        <div class="ph-actions">
          ${live ? '' : '<button class="btn" data-act="reopen">Open window</button>'}
          <button class="btn btn-quiet" data-act="popin">Bring it back</button>
        </div>`;
      ph.querySelector('[data-act="popin"]').onclick = () =>
        this.popIn(card.uid).catch((e) => this.onError && this.onError(e.message));
      const re = ph.querySelector('[data-act="reopen"]');
      if (re) {
        re.onclick = () => Promise.resolve(this.onReopen(card))
          .then(() => this._paintBody(t, node))
          .catch((e) => this.onError && this.onError(e.message));
      }
      body.appendChild(ph);
    }

    /* Show the active card and tell the others they are hidden.

       They stay mounted - switching tabs must not reload a card and lose
       what was on screen - so the only way a hidden card can let go of the
       sound card, or stop polling somebody's server, is to be told. A
       CSS-hidden iframe gets no visibilitychange of its own. */
    _showActive(t) {
      const node = this._nodes.get(t.uid);
      if (!node) return;
      node.querySelectorAll('.tile-card').forEach((holder) => {
        const on = holder.dataset.uid === t.active;
        holder.classList.toggle('on', on);
        this._tellVisible(holder, on);
      });
      const card = this.get(t.active);
      const btn = node.querySelector('[data-act="settings"]');
      if (btn) btn.hidden = !(card && card._configurable);
    }

    _tellVisible(holder, visible) {
      global.HDFrames.tellVisible(holder, visible);
    }

    /* Take the board down, for switching to the phone shell.
     *
     * Every card is told it is hidden on the way out. Tearing an iframe
     * out of the document without saying so leaves a card that holds a
     * sound card or a cluster socket waiting on its own reaper to notice
     * - thirty seconds in which the operator has switched views and the
     * radio's audio is still locked against everything else. */
    destroy() {
      this._nodes.forEach((node) => {
        node.querySelectorAll('.tile-card').forEach((holder) => {
          global.HDFrames.tellVisible(holder, false);
        });
        node.remove();
      });
      this._nodes.clear();
    }

    /* ---- dragging ---------------------------------------------------- */

    _drag(e, t, node, mode) {
      if (e.button !== 0) return;
      e.preventDefault();
      const startX = e.clientX;
      // Not const: when the board scrolls under a held tile, the same
      // screen position means a different row, so the origin moves with it.
      let startY = e.clientY;
      const orig = { x: t.x, y: t.y, w: t.w, h: t.h };
      const cw = this.cellW + this.gap;
      const ch = this.rowPx + this.gap;
      let willPop = false;
      let onto = null;                 // the tile this one would dock into
      let hinted = false;
      node.classList.add('dragging');
      // Throws if the pointer is no longer active - it went up inside the
      // handler, or the event was synthesised. Uncaught it leaves the drag
      // half built: the class is on, the listeners are not, and the tile
      // follows nothing and will not let go.
      try { node.setPointerCapture(e.pointerId); } catch { /* no capture */ }

      // Where the pointer is now. The auto-scroller re-applies the drag from
      // this without a new pointer event: the mouse can be held perfectly
      // still against the edge while the board moves underneath it.
      const at = { clientX: startX, clientY: startY };

      const apply = () => {
        const ev = at;
        const dx = Math.round((ev.clientX - startX) / cw);
        const dy = Math.round((ev.clientY - startY) / ch);
        if (mode === 'move') {
          const r = this.el.getBoundingClientRect();
          // Dragging up past the top is how you say "put this at the top",
          // and that is also the gesture for popping out. While there is
          // still board above to reach, it means scroll; only once there is
          // nowhere left to go does leaving the top edge mean pop out.
          const out = ev.clientX < r.left - POP_MARGIN || ev.clientX > r.right + POP_MARGIN
                   || (ev.clientY < r.top - POP_MARGIN && this.el.scrollTop <= 0);
          if (out !== willPop) {
            willPop = out;
            node.classList.toggle('will-pop', out);
          }
          // Docking wins over moving: while the pointer is over another
          // tile the drag is "put this in there", and the box underneath
          // says so rather than the grid guides pretending it is a move.
          const target = out ? null : this._tileUnder(ev, node);
          if (target !== onto) {
            this._nodes.get(onto?.uid)?.classList.remove('dock-target');
            onto = target;
            this._nodes.get(onto?.uid)?.classList.add('dock-target');
            node.classList.toggle('will-dock', !!onto);
          }
          t.x = clamp(orig.x + dx, 0, this.cols - t.w);
          t.y = Math.max(0, orig.y + dy);
          // Once it has actually left where it started this is a real
          // drag, and worth a word about where it can be dropped. From
          // `dx`/`dy` rather than from `t`: `t` is assigned on the lines
          // just above, so reading it any earlier compared the tile with
          // itself and the hint waited for a *second* pointermove.
          if (!hinted && (dx || dy)) {
            hinted = true;
            this.onHint('drag');
          }
        } else {
          t.w = clamp(orig.w + dx, t.min_w || 2, this.cols - t.x);
          t.h = Math.max(t.min_h || 2, orig.h + dy);
        }
        this._place(t, node);
        // Dragging towards the bottom used to stop scrolling at whatever
        // the board already extended to, because the spacer is only sized
        // on reflow. A tile being carried below that is a good enough
        // reason for the board to get taller; the drop recomputes it
        // properly.
        if (mode === 'move') this._growExtent(t);
      };

      const move = (ev) => {
        at.clientX = ev.clientX;
        at.clientY = ev.clientY;
        apply();
      };

      /* ---- scrolling the board while a tile is held ------------------
         Without this, moving a card from the bottom of a long board to the
         top means dropping it, scrolling, picking it up again, over and
         over. The pointer sitting inside EDGE of either end scrolls the
         board, faster the closer it gets, and the tile keeps following the
         cursor because `startY` moves by exactly what the board scrolled. */
      let scrolling = null;
      let lastTick = 0;
      // How deep into the edge band the pointer is, 0..1, past the edge
      // counting as all the way.
      const rate = (depth) =>
        EDGE_SLOW + (EDGE_FAST - EDGE_SLOW)
          * Math.min(1, Math.max(0, depth) / EDGE);

      const tick = (now) => {
        scrolling = requestAnimationFrame(tick);
        // A frame that arrives after a long stall must not teleport the
        // board, so the step is capped at what 50ms would have moved.
        const dt = Math.min(0.05, lastTick ? (now - lastTick) / 1000 : 0);
        lastTick = now;
        const r = this.el.getBoundingClientRect();
        const y = at.clientY;
        let d = 0;
        if (y < r.top + EDGE) d = -rate(r.top + EDGE - y) * dt;
        else if (y > r.bottom - EDGE) d = rate(y - (r.bottom - EDGE)) * dt;
        if (!d) return;
        const before = this.el.scrollTop;
        this.el.scrollTop = before + d;
        const moved = this.el.scrollTop - before;
        if (!moved) return;           // already at the end; nothing to do
        startY -= moved;
        apply();
      };
      scrolling = requestAnimationFrame(tick);

      // Escape puts it back. Without this a drag can only be finished, so
      // a tile heading for the wrong tab strip has to be dropped and then
      // undone by hand - and dropping is the part that is hard to undo.
      let cancelled = false;
      const abort = (ev) => {
        if (ev.key !== 'Escape' || cancelled) return;
        cancelled = true;
        ev.preventDefault();
        Object.assign(t, orig);
        this._place(t, node);
        up();
      };
      global.addEventListener('keydown', abort, true);

      const up = () => {
        cancelAnimationFrame(scrolling);
        global.removeEventListener('keydown', abort, true);
        node.classList.remove('dragging', 'will-pop', 'will-dock');
        this._nodes.get(onto?.uid)?.classList.remove('dock-target');
        node.removeEventListener('pointermove', move);
        node.removeEventListener('pointerup', up);
        node.removeEventListener('pointercancel', up);
        if (cancelled) {
          // Back where it started, and nothing is docked, popped or saved.
          this._nodes.get(onto?.uid)?.classList.remove('dock-target');
          return;
        }
        if (willPop && !t.popped) {
          Object.assign(t, orig);                 // snap back, then pop out
          this._place(t, node);
          this.popOut(t.active).catch((err) => this.onError && this.onError(err.message));
          return;
        }
        if (onto) {
          // Everything in the dragged tile goes in, in order, so dropping a
          // stack onto a stack joins them rather than keeping one card and
          // losing the rest.
          Object.assign(t, orig);
          this._place(t, node);
          const moving = t.cards.map((c) => c.uid);
          const target = onto.uid;
          moving.forEach((cardUid) => this.dock(cardUid, target));
          this.onDocked();
          return;
        }
        if (mode === 'resize') { t.pop_w = null; t.pop_h = null; }
        this._resolve(t);
        this.reflow();
        this.onChange(this.tiles);
      };

      node.addEventListener('pointermove', move);
      node.addEventListener('pointerup', up);
      node.addEventListener('pointercancel', up);
    }

    /* Which tile the pointer would dock into, ignoring the one being
       dragged. elementFromPoint would only ever answer with the dragged
       tile, which is under the cursor by definition.

       **Only the target's tab strip docks.** The whole tile used to, and on
       a full board that is almost everywhere - so moving a card across the
       board kept turning it into somebody else's tab, and plain rearranging
       became the hard case. Dropping a card on the strip where tabs already
       live is the same gesture as dropping a browser tab on a tab bar, and
       it leaves the rest of the tile meaning what it looks like it means:
       move. The band is the header, floored at DOCK_BAND so it stays a
       comfortable target on a short tile. */
    _tileUnder(ev, dragging) {
      for (const t of this.tiles) {
        const node = this._nodes.get(t.uid);
        if (!node || node === dragging || t.popped) continue;
        const r = node.getBoundingClientRect();
        if (ev.clientX < r.left || ev.clientX > r.right) continue;
        const head = node.querySelector('.tile-head');
        const deep = Math.max(DOCK_BAND,
          head ? head.getBoundingClientRect().height : 0);
        if (ev.clientY >= r.top && ev.clientY <= Math.min(r.bottom,
            r.top + deep)) return t;
      }
      return null;
    }

    /* Drag a tab: sideways to reorder is not worth the code, so this is
       only "pull it out", which needs a deliberate distance before it
       starts or every click on a tab would undock it. */
    _dragTab(e, t, node, cardUid) {
      if (e.button !== 0 || t.cards.length < 2) return;
      const startX = e.clientX;
      const startY = e.clientY;
      let pulled = false;

      const move = (ev) => {
        if (pulled) return;
        if (Math.abs(ev.clientY - startY) < TEAR_OFF
            && Math.abs(ev.clientX - startX) < TEAR_OFF * 2) return;
        pulled = true;
        done();
        const r = this.el.getBoundingClientRect();
        const made = this.undock(cardUid, {
          x: clamp(Math.floor((ev.clientX - r.left - this.gap)
                              / (this.cellW + this.gap)),
                   0, this.cols - 1),
          y: Math.max(0, Math.floor((ev.clientY - r.top - this.gap)
                                    / (this.rowPx + this.gap))),
        });
        // Carry straight on into a normal move, so the tile follows the
        // pointer instead of landing and waiting to be dragged again.
        const fresh = made && this._nodes.get(made.uid);
        if (fresh) this._drag(ev, made, fresh, 'move');
      };
      const done = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', done);
        window.removeEventListener('pointercancel', done);
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', done);
      window.addEventListener('pointercancel', done);
    }

    /* Drop a card from the palette straight onto a spot on the board. */
    _dropTarget() {
      const over = (e) => {
        if (!e.dataTransfer || ![...e.dataTransfer.types].includes('text/hamdash-card')) return;
        e.preventDefault();
        this.el.classList.add('drop-hint');
      };
      this.el.addEventListener('dragover', over);
      this.el.addEventListener('dragenter', over);
      this.el.addEventListener('dragleave', (e) => {
        if (e.target === this.el) this.el.classList.remove('drop-hint');
      });
      this.el.addEventListener('drop', (e) => {
        const raw = e.dataTransfer && e.dataTransfer.getData('text/hamdash-card');
        this.el.classList.remove('drop-hint');
        if (!raw) return;
        e.preventDefault();
        let def;
        try { def = JSON.parse(raw); } catch { return; }
        const r = this.el.getBoundingClientRect();
        const x = Math.floor((e.clientX - r.left - this.gap) / (this.cellW + this.gap));
        const y = Math.floor((e.clientY - r.top - this.gap) / (this.rowPx + this.gap));
        this.add({ ...def, x: clamp(x, 0, this.cols - (def.w || 4)), y: Math.max(0, y) });
      });
    }

    /* ---- geometry ---------------------------------------------------- */

    _resolve(moved) {
      L.resolve(this.tiles, moved);
    }

    _nextRow() {
      return L.nextRow(this.tiles);
    }

    /* Moving a tile without a pointer.
     *
     * Dragging was the only way to arrange the board, which means anybody
     * who cannot use a pointer - or is simply not using one right now -
     * could add cards and never place them. The header is the handle for a
     * drag, so it is the handle for this too: focus it and the arrows move
     * the tile, with Shift to resize.
     *
     * Only when the header itself has focus. The buttons inside it are
     * controls of their own and arrow keys belong to them.
     */
    _wireKeys(t, node) {
      const head = node.querySelector('.tile-head');
      if (!head) return;
      const STEP = {
        ArrowLeft: [-1, 0], ArrowRight: [1, 0],
        ArrowUp: [0, -1], ArrowDown: [0, 1],
      };
      head.addEventListener('keydown', (e) => {
        if (e.target !== head) return;
        const d = STEP[e.key];
        if (!d) return;
        e.preventDefault();
        if (e.shiftKey) {
          t.w = clamp(t.w + d[0], t.min_w || 2, this.cols - t.x);
          t.h = Math.max(t.min_h || 2, t.h + d[1]);
          t.pop_w = null;
          t.pop_h = null;
        } else {
          t.x = clamp(t.x + d[0], 0, this.cols - t.w);
          t.y = Math.max(0, t.y + d[1]);
        }
        this._place(t, node);
        this._resolve(t);
        this.reflow();
        this.onChange(this.tiles);
        head.scrollIntoView({ block: 'nearest' });
        this._sayPlace(t, head);
      });
    }

    /* Say where it landed, because a tile moving is the only feedback a
       sighted operator needs and no feedback at all otherwise. */
    _sayPlace(t, head) {
      const card = t.cards.find((c) => c.uid === t.active) || t.cards[0];
      const name = (card && (card.title || card.card)) || 'card';
      head.setAttribute('aria-label',
        `${name}: column ${t.x + 1}, row ${t.y + 1}, ${t.w} by ${t.h}. `
        + 'Arrow keys move it, shift and arrow keys resize it.');
    }

    /* Make room under a tile being dragged past the current bottom. Only
       ever grows: shrinking mid-drag would move the ground underfoot. */
    _growExtent(t) {
      if (!this._spacer) return;
      const need = this.gap * 2 + (t.y + t.h) * (this.rowPx + this.gap);
      if (need > (parseFloat(this._spacer.style.height) || 0)) {
        this._spacer.style.height = `${need}px`;
      }
    }

    _place(t, node) {
      const cw = this.cellW;
      node.style.left = `${this.gap + t.x * (cw + this.gap)}px`;
      node.style.top = `${this.gap + t.y * (this.rowPx + this.gap)}px`;
      node.style.width = `${t.w * cw + (t.w - 1) * this.gap}px`;
      node.style.height = `${t.h * this.rowPx + (t.h - 1) * this.gap}px`;
    }

    reflow() {
      this.el.style.setProperty('--cell-w', `${this.cellW + this.gap}px`);
      this.el.style.setProperty('--cell-h', `${this.rowPx + this.gap}px`);
      this.tiles.forEach((t) => {
        const node = this._nodes.get(t.uid);
        if (node) this._place(t, node);
      });
      // A spacer, not min-height on the scroll container. min-height beats
      // height, so setting it here made the grid itself grow past the
      // viewport - and because body is overflow:hidden, everything below the
      // fold was clipped away with no way to reach it.
      const rows = this._nextRow();
      if (!this._spacer) {
        this._spacer = document.createElement('div');
        this._spacer.className = 'grid-extent';
        this.el.appendChild(this._spacer);
      }
      this._spacer.style.height =
        `${this.gap * 2 + rows * (this.rowPx + this.gap)}px`;
    }

    status(cardId, state) {
      this._state.set(cardId, state);
      this.tiles.forEach((t) => {
        const node = this._nodes.get(t.uid);
        if (node && t.cards.some((c) => c.card === cardId)) {
          this._paintStatus(t, node);
        }
      });
    }

    /* The tile's own dot follows whichever card is showing; each tab keeps
       its own, so a card that has fallen over on a background tab says so
       without having to be looked at. */
    _paintStatus(t, node) {
      const mark = (dot, cardId) => {
        if (!dot) return;
        const state = this._state.get(cardId);
        const warn = this._warnings.get(cardId) || [];
        dot.classList.toggle('up', state === 'running' && !warn.length);
        dot.classList.toggle('down', state !== 'running' && state !== 'n/a'
                                     && state !== undefined);
        // A manifest the host had to ignore. The card runs, so it is not a
        // failure - but something the author wrote is being thrown away,
        // and it used to be computed, returned by /api/cards and shown to
        // nobody.
        dot.classList.toggle('warn', !!warn.length && state === 'running');
        dot.title = [state || '', ...warn].filter(Boolean).join(' · ');
      };
      t.cards.forEach((c) => {
        mark(node.querySelector(`.tab[data-uid="${c.uid}"] .tab-dot`), c.card);
      });
      const active = t.cards.find((c) => c.uid === t.active) || t.cards[0];
      mark(node.querySelector('.tile-status'), active.card);
    }

    /* What the host could not make sense of in a card's manifest. */
    warn(cardId, list) {
      const had = (this._warnings.get(cardId) || []).join('|');
      const now = (list || []).join('|');
      if (had === now) return;
      if (now) this._warnings.set(cardId, list.slice());
      else this._warnings.delete(cardId);
      this.tiles.forEach((t) => {
        const node = this._nodes.get(t.uid);
        if (node && t.cards.some((c) => c.card === cardId)) {
          this._paintStatus(t, node);
        }
      });
    }
  }

  const clamp = L.clamp;
  const esc = (v) => String(v ?? '').replace(/[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  global.Board = Board;
})(window);
