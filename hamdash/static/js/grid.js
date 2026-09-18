/* grid.js — absolute-positioned tiles snapped to a column grid.
 *
 * Tiles are {uid, card, x, y, w, h, title, url, popped}. Drag the header to
 * move, the corner to resize, and drag a tile off the board (or hit ⧉) to pop
 * it out into its own window. Layout is persisted by the caller's onChange.
 *
 * Overlap is resolved by pushing tiles down, never sideways: horizontal
 * position is the thing people arrange deliberately, vertical order isn't.
 */
(function (global) {
  const POP_MARGIN = 48;      // px outside the board before a drag means "pop out"

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
      this.isPoppedOpen = opts.isPoppedOpen || (() => true);
      this.tiles = [];
      this.arranging = false;
      this._nodes = new Map();
      global.addEventListener('resize', () => this.reflow());
      this._dropTarget();
    }

    get cellW() {
      const inner = this.el.clientWidth - this.gap * 2;
      return (inner - this.gap * (this.cols - 1)) / this.cols;
    }

    setTiles(tiles) {
      this.tiles = tiles.map((t) => ({ ...t, uid: t.uid || uid() }));
      this.el.querySelectorAll('.tile').forEach((n) => n.remove());
      this._nodes.clear();
      this.tiles.forEach((t) => this._mount(t));
      this.reflow();
    }

    add(tile) {
      const t = { uid: uid(), x: 0, y: this._nextRow(), w: 4, h: 3, ...tile };
      t.x = clamp(t.x, 0, Math.max(0, this.cols - t.w));
      this.tiles.push(t);
      this._mount(t);
      this._resolve(t);
      this.reflow();
      this.onChange(this.tiles);
      return t;
    }

    remove(uidToDrop) {
      const t = this.get(uidToDrop);
      if (t && t.popped) this.onPopIn(t);        // don't orphan its window
      this.tiles = this.tiles.filter((x) => x.uid !== uidToDrop);
      this._nodes.get(uidToDrop)?.remove();
      this._nodes.delete(uidToDrop);
      this.onChange(this.tiles);
    }

    get(tileUid) {
      return this.tiles.find((t) => t.uid === tileUid);
    }

    setArranging(on) {
      this.arranging = on;
      this.el.classList.toggle('arranging', on);
      this.reflow();
    }

    /* ---- pop out / pop in ------------------------------------------- */

    markPopped(tileUid, popped) {
      const t = this.get(tileUid);
      if (!t) return;
      t.popped = !!popped;
      const node = this._nodes.get(tileUid);
      if (node) this._paintBody(t, node);
      this.onChange(this.tiles);
    }

    async popOut(tileUid) {
      const t = this.get(tileUid);
      if (!t || t.popped) return;
      try {
        await this.onPopOut(t);
        this.markPopped(tileUid, true);
      } catch (e) {
        const node = this._nodes.get(tileUid);
        if (node) node.classList.add('tile-error');
        throw e;
      }
    }

    async popIn(tileUid) {
      const t = this.get(tileUid);
      if (!t) return;
      await this.onPopIn(t);
      this.markPopped(tileUid, false);
    }

    /* ---- mounting ---------------------------------------------------- */

    _mount(t) {
      const node = document.createElement('section');
      node.className = 'tile';
      node.dataset.uid = t.uid;
      node.innerHTML = `
        <header class="tile-head">
          <span class="tile-grip" aria-hidden="true">···</span>
          <span class="tile-status" title="status"></span>
          <span class="tile-title"></span>
          <span class="tile-spacer"></span>
          <button class="tile-btn" data-act="settings" title="Card settings" hidden>⚙</button>
          <button class="tile-btn" data-act="popout" title="Pop out into its own window">⧉</button>
          <button class="tile-btn" data-act="reload" title="Reload card">↻</button>
          <button class="tile-btn" data-act="close" title="Remove card">✕</button>
        </header>
        <div class="tile-body"></div>
        <div class="resize" data-act="resize" title="Resize"></div>`;
      node.querySelector('.tile-title').textContent = t.title || t.card;
      this.el.appendChild(node);
      this._nodes.set(t.uid, node);
      this._paintBody(t, node);

      node.querySelector('[data-act="close"]').onclick = () => this.remove(t.uid);
      node.querySelector('[data-act="reload"]').onclick = () => {
        const f = node.querySelector('iframe');
        if (f) f.src = f.src;
      };
      node.querySelector('[data-act="settings"]').onclick = () => this.onSettings(t);
      node.querySelector('[data-act="popout"]').onclick = () => {
        (t.popped ? this.popIn(t.uid) : this.popOut(t.uid))
          .catch((e) => this.onError && this.onError(e.message));
      };
      node.querySelector('.tile-head').addEventListener('pointerdown', (e) => {
        if (e.target.closest('.tile-btn')) return;
        this._drag(e, t, node, 'move');
      });
      node.querySelector('.resize').addEventListener('pointerdown', (e) => {
        this._drag(e, t, node, 'resize');
      });
      return node;
    }

    /* Only show the gear on cards that actually declare settings. */
    setConfigurable(tileUid, yes) {
      const btn = this._nodes.get(tileUid)?.querySelector('[data-act="settings"]');
      if (btn) btn.hidden = !yes;
    }

    _paintBody(t, node) {
      const body = node.querySelector('.tile-body');
      body.innerHTML = '';
      node.classList.toggle('popped', !!t.popped);
      node.querySelector('[data-act="popout"]').textContent = t.popped ? '⤓' : '⧉';
      node.querySelector('[data-act="popout"]').title = t.popped
        ? 'Bring this card back onto the board'
        : 'Pop out into its own window';
      if (t.popped) {
        // Two states: the window is up, or it isn't yet (browsers only open
        // pop-ups from a click, so after a reload the tile offers a button).
        const live = this.isPoppedOpen(t.uid);
        const ph = document.createElement('div');
        ph.className = 'tile-placeholder';
        ph.innerHTML = `
          <p>${live ? 'Running in its own window.' : 'Popped out, window closed.'}</p>
          <div class="ph-actions">
            ${live ? '' : '<button class="btn" data-act="reopen">Open window</button>'}
            <button class="btn btn-quiet" data-act="popin">Bring it back</button>
          </div>`;
        ph.querySelector('[data-act="popin"]').onclick = () =>
          this.popIn(t.uid).catch((e) => this.onError && this.onError(e.message));
        const re = ph.querySelector('[data-act="reopen"]');
        if (re) {
          re.onclick = () => Promise.resolve(this.onReopen(t))
            .then(() => this._paintBody(t, node))
            .catch((e) => this.onError && this.onError(e.message));
        }
        body.appendChild(ph);
        return;
      }
      if (this.renderBody) this.renderBody(t, body);
    }

    /* ---- dragging ---------------------------------------------------- */

    _drag(e, t, node, mode) {
      if (e.button !== 0) return;
      e.preventDefault();
      const startX = e.clientX;
      const startY = e.clientY;
      const orig = { x: t.x, y: t.y, w: t.w, h: t.h };
      const cw = this.cellW + this.gap;
      const ch = this.rowPx + this.gap;
      let willPop = false;
      node.classList.add('dragging');
      node.setPointerCapture(e.pointerId);

      const move = (ev) => {
        const dx = Math.round((ev.clientX - startX) / cw);
        const dy = Math.round((ev.clientY - startY) / ch);
        if (mode === 'move') {
          const r = this.el.getBoundingClientRect();
          const out = ev.clientX < r.left - POP_MARGIN || ev.clientX > r.right + POP_MARGIN
                   || ev.clientY < r.top - POP_MARGIN;
          if (out !== willPop) {
            willPop = out;
            node.classList.toggle('will-pop', out);
          }
          t.x = clamp(orig.x + dx, 0, this.cols - t.w);
          t.y = Math.max(0, orig.y + dy);
        } else {
          t.w = clamp(orig.w + dx, t.min_w || 2, this.cols - t.x);
          t.h = Math.max(t.min_h || 2, orig.h + dy);
        }
        this._place(t, node);
      };

      const up = () => {
        node.classList.remove('dragging', 'will-pop');
        node.removeEventListener('pointermove', move);
        node.removeEventListener('pointerup', up);
        node.removeEventListener('pointercancel', up);
        if (willPop && !t.popped) {
          Object.assign(t, orig);                 // snap back, then pop out
          this._place(t, node);
          this.popOut(t.uid).catch((err) => this.onError && this.onError(err.message));
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

    _overlap(a, b) {
      return a.uid !== b.uid && a.x < b.x + b.w && b.x < a.x + a.w
          && a.y < b.y + b.h && b.y < a.y + a.h;
    }

    _resolve(moved, seen = new Set()) {
      if (seen.has(moved.uid)) return;
      seen.add(moved.uid);
      this.tiles
        .filter((o) => this._overlap(moved, o))
        .sort((p, q) => p.y - q.y)
        .forEach((o) => {
          o.y = moved.y + moved.h;
          this._resolve(o, seen);
        });
    }

    _nextRow() {
      return this.tiles.reduce((m, t) => Math.max(m, t.y + t.h), 0);
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
      this.tiles.filter((t) => t.card === cardId).forEach((t) => {
        const dot = this._nodes.get(t.uid)?.querySelector('.tile-status');
        if (!dot) return;
        dot.classList.toggle('up', state === 'running');
        dot.classList.toggle('down', state !== 'running' && state !== 'n/a');
        dot.title = state;
      });
    }
  }

  const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
  const uid = () => Math.random().toString(36).slice(2, 9);

  global.Board = Board;
})(window);
