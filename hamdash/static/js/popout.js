/* popout.js — one pop-out API, three backends, picked at runtime.
 *
 *   pywebview  real OS window, always-on-top, several at once  (desktop app)
 *   dpip       Document Picture-in-Picture: on top, ONE at a time (Chromium)
 *   window     window.open fallback: resizable, never on top
 *
 * Every backend shows the same page (/popout), which frames the same
 * /cards/<id>/ URL the tile used. The card process doesn't know or care.
 *
 * Backend choice is NOT a guess. The host says whether we are inside the
 * native shell (/api/shell), because inside a webview both fallbacks are
 * actively harmful: WebView2 hands an unhandled new-window request to the
 * Windows shell, which then offers to find an app in the Store to open the
 * resulting 'about:' URL. So in a native shell it is the pywebview bridge or
 * nothing, and we wait for the bridge to be injected rather than racing it.
 */
(function (global) {
  const open = new Map();          // uid -> {backend, ref, tile}
  let backend = 'window';
  let caps = { on_top: false, multiple: true };
  let onReturn = () => {};

  /* pywebview injects window.pywebview asynchronously and fires
     'pywebviewready' when the bridge is usable. Checking for it at boot is a
     race we lose about half the time on Windows. */
  function awaitBridge(timeoutMs = 8000) {
    const ready = () => !!(global.pywebview && global.pywebview.api
                           && global.pywebview.api.popout_open);
    if (ready()) return Promise.resolve(true);
    return new Promise((resolve) => {
      let done = false;
      const finish = (ok) => { if (!done) { done = true; resolve(ok); } };
      global.addEventListener('pywebviewready', () => finish(ready()), { once: true });
      const started = Date.now();
      const poll = setInterval(() => {
        if (ready()) { clearInterval(poll); finish(true); }
        else if (Date.now() - started > timeoutMs) { clearInterval(poll); finish(false); }
      }, 120);
    });
  }

  async function detect() {
    let native = false;
    try {
      native = !!(await (await fetch('/api/shell')).json()).native;
    } catch { /* host not answering; treat as a browser */ }

    if (native) {
      const ok = await awaitBridge();
      if (ok) {
        try {
          const info = await global.pywebview.api.popout_backend();
          backend = 'pywebview';
          caps = { on_top: !!info.on_top, multiple: true };
          return backend;
        } catch { /* bridge present but not answering */ }
      }
      // Never fall through to window.open or PiP here: inside a webview that
      // escapes to the OS instead of opening a window.
      backend = 'none';
      caps = { on_top: false, multiple: false };
      return backend;
    }

    if ('documentPictureInPicture' in global) {
      backend = 'dpip';
      caps = { on_top: true, multiple: false };
      return backend;
    }
    backend = 'window';
    caps = { on_top: false, multiple: true };
    return backend;
  }

  function url(tile) {
    const q = new URLSearchParams({
      uid: tile.uid, card: tile.card, title: tile.title || tile.card,
    });
    if (tile.url) q.set('url', tile.url);
    return `/popout?${q}`;
  }

  function px(tile) {
    const cell = 110, row = 90;   // rough tile -> pixel guess for first open
    return {
      w: Math.max(320, Math.round((tile.pop_w || tile.w * cell))),
      h: Math.max(240, Math.round((tile.pop_h || tile.h * row + 40))),
    };
  }

  async function popOut(tile) {
    if (open.has(tile.uid)) return { ok: true, already: true };
    const size = px(tile);

    if (backend === 'none') {
      throw new Error('Pop-out needs the desktop bridge, which did not load. '
        + 'Update pywebview (pip install -U pywebview) and restart, '
        + 'or run with --browser.');
    }

    if (backend === 'pywebview') {
      const r = await global.pywebview.api.popout_open({
        uid: tile.uid, card: tile.card, title: tile.title || tile.card,
        url: tile.url || '', w: size.w, h: size.h,
        on_top: tile.on_top !== false,
      });
      if (!r || r.ok === false) throw new Error((r && r.error) || 'window refused');
      open.set(tile.uid, { backend, ref: null, tile });
      return r;
    }

    if (backend === 'dpip') {
      if (open.size) {
        const [other] = open.keys();
        await popIn(other);           // the API allows exactly one PiP window
      }
      const win = await global.documentPictureInPicture.requestWindow({
        width: size.w, height: size.h,
      });
      const frame = win.document.createElement('iframe');
      frame.src = url(tile);
      frame.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;border:0';
      win.document.body.style.margin = '0';
      win.document.body.appendChild(frame);
      win.addEventListener('pagehide', () => finish(tile.uid));
      open.set(tile.uid, { backend, ref: win, tile });
      return { ok: true };
    }

    const feat = `popup=yes,hamdash-internal,width=${size.w},height=${size.h}`;
    const win = global.open(url(tile), `hamdash_${tile.uid}`, feat);
    if (!win) throw new Error('the browser blocked the pop-up window');
    const poll = setInterval(() => {
      if (win.closed) { clearInterval(poll); finish(tile.uid); }
    }, 800);
    open.set(tile.uid, { backend, ref: win, tile, poll });
    return { ok: true };
  }

  async function popIn(uid) {
    const rec = open.get(uid);
    if (!rec) return;
    try {
      if (rec.backend === 'pywebview') await global.pywebview.api.popout_close(uid);
      else if (rec.ref && !rec.ref.closed) rec.ref.close();
    } catch { /* already gone */ }
    finish(uid);
  }

  function finish(uid) {
    const rec = open.get(uid);
    if (rec && rec.poll) clearInterval(rec.poll);
    open.delete(uid);
    onReturn(uid);
  }

  async function setOnTop(uid, on) {
    if (backend !== 'pywebview') {
      return { ok: false, error: `always-on-top is not available in ${backend} mode` };
    }
    return global.pywebview.api.popout_set_on_top(uid, !!on);
  }

  // the popped-out page asks to come back via the bus; python calls these directly
  global.HDPopout = {
    detect,
    get backend() { return backend; },
    get caps() { return caps; },
    popOut,
    popIn,
    setOnTop,
    isOpen: (uid) => open.has(uid),
    list: () => [...open.keys()],
    set onReturn(fn) { onReturn = fn || (() => {}); },
    onClosed: (uid) => finish(uid),          // called by python on window close
    onPopInRequest: (uid) => popIn(uid),
  };

  // closing the board should not leave orphan windows behind
  global.addEventListener('beforeunload', () => {
    if (backend === 'pywebview') return;     // python owns those
    open.forEach((rec) => { try { rec.ref && rec.ref.close(); } catch {} });
  });
})(window);
