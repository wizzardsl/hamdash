/* hamdash.js — the browser-side bridge.
 *
 * Loaded by the shell and by any card's HTML (cards are proxied through the
 * host, so they're same-origin and can use this directly):
 *
 *   <script src="/static/js/hamdash.js"></script>
 *   HD.on('radio.state', s => ...)        // subscribe to the bus
 *   HD.publish('mycard.tuned', {hz})      // publish to the bus
 *   await HD.get('/api/radio/state')      // shared services
 *   await HD.card('logbook', '/api/rows') // this card's own backend
 */
(function (global) {
  const listeners = new Map();
  let es = null;
  let nativeShell = false;

  // One fetch per page: tells us whether we are inside the desktop webview.
  fetch('/api/shell').then((r) => r.json()).then((info) => {
    nativeShell = !!info.native;
    if (nativeShell) installLinkGuard();
  }).catch(() => { /* browser, or host restarting */ });

  /* Inside a webview, a target=_blank link or window.open() is not a popup -
     it is an unhandled new-window request that escapes to the OS. Catch both
     and hand the URL to the real browser instead. */
  function installLinkGuard() {
    global.document.addEventListener('click', (e) => {
      const a = e.target.closest && e.target.closest('a[href]');
      if (!a) return;
      const href = a.getAttribute('href') || '';
      const external = /^https?:/i.test(href) && a.host !== global.location.host;
      if (a.target === '_blank' || external) {
        e.preventDefault();
        HD.openExternal(a.href).catch(() => {});
      }
    }, true);

    const nativeOpen = global.open;
    global.open = function guarded(url, name, features) {
      // the pop-out manager passes its own marker; everything else goes out
      if (features && String(features).includes('hamdash-internal')) {
        return nativeOpen.call(global, url, name, features);
      }
      if (url) HD.openExternal(String(url)).catch(() => {});
      return null;
    };
  }

  function connect() {
    if (es) return;
    es = new EventSource('/api/events');
    es.onmessage = (e) => dispatch(e);
    es.onerror = () => { /* EventSource retries on its own */ };
  }

  function dispatch(e) {
    let evt;
    try { evt = JSON.parse(e.data); } catch { return; }
    for (const [pattern, fns] of listeners) {
      if (match(pattern, evt.topic)) fns.forEach((f) => {
        try { f(evt.payload, evt); } catch (err) { console.error(pattern, err); }
      });
    }
  }

  function match(pattern, topic) {
    if (pattern === '*' || pattern === topic) return true;
    return pattern.endsWith('.*') && topic.startsWith(pattern.slice(0, -1));
  }

  async function req(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(path, opts);
    const text = await r.text();
    let data = text;
    try { data = text ? JSON.parse(text) : {}; } catch { /* leave as text */ }
    if (!r.ok) {
      const msg = (data && data.error) || `${method} ${path} failed (${r.status})`;
      throw new Error(msg);
    }
    return data;
  }

  const HD = {
    cardId: null,   // cards set this: HD.cardId = 'logbook'

    // Which tile this page is. One card process serves every tile of its
    // kind, so anything per-instance hangs off this.
    tileUid: new URLSearchParams(location.search).get('uid') || '',

    on(pattern, fn) {
      connect();
      if (!listeners.has(pattern)) listeners.set(pattern, new Set());
      listeners.get(pattern).add(fn);
      return () => listeners.get(pattern).delete(fn);
    },

    publish(topic, payload) {
      return req('POST', '/api/publish', {
        topic, payload: payload || {}, source: HD.cardId || 'ui',
      });
    },

    get: (p, params) => req('GET', params ? `${p}?${new URLSearchParams(params)}` : p),
    post: (p, body) => req('POST', p, body || {}),
    put: (p, body) => req('PUT', p, body || {}),
    del: (p) => req('DELETE', p),

    // this card's own backend, without hardcoding the mount path
    card(id, path, body) {
      const cid = id || HD.cardId;
      return body === undefined
        ? req('GET', `/cards/${cid}${path}`)
        : req('POST', `/cards/${cid}${path}`, body);
    },

    // shared services, spelled out so cards read clearly
    radio: {
      state: () => HD.get('/api/radio/state'),
      freq: (hz) => HD.post('/api/radio/freq', { hz }),
      mode: (mode, width) => HD.post('/api/radio/mode', { mode, width: width || 0 }),
      ptt: (on) => HD.post('/api/radio/ptt', { on: !!on }),
      modes: () => HD.get('/api/radio/modes'),
    },
    lookup: (call) => HD.get(`/api/lookup/${encodeURIComponent(call)}`),
    distance: (to, from) => HD.get('/api/grid/distance', from ? { to, from } : { to }),
    log: {
      recent: (limit) => HD.get('/api/log', { limit: limit || 25 }),
      add: (qso) => HD.post('/api/log', qso),
      remove: (id) => HD.del(`/api/log/${id}`),
      stats: () => HD.get('/api/log/stats'),
    },
    station: async () => (await HD.get('/api/config')).station,

    // this tile's settings, merged over the manifest defaults
    async settings(uid) {
      const id = uid || HD.tileUid;
      if (!id) return {};
      return (await HD.get(`/api/tiles/${id}/settings`)).values || {};
    },
    saveSettings(values, uid) {
      return HD.put(`/api/tiles/${uid || HD.tileUid}/settings`, { values });
    },
    // fires when the operator edits this tile's settings
    onSettings(fn) {
      return HD.on('card.settings_changed', (p) => {
        if (!HD.tileUid || p.uid === HD.tileUid) fn(p.values || {}, p);
      });
    },

    // Open a link outside the dashboard. In the desktop shell this goes to
    // the real browser; a plain target=_blank there becomes an unhandled
    // new-window request, which Windows answers by offering the Store.
    async openExternal(url) {
      if (nativeShell && window.pywebview && window.pywebview.api
          && window.pywebview.api.open_external) {
        const r = await window.pywebview.api.open_external(url);
        if (r && r.ok === false) throw new Error(r.error);
        return r;
      }
      window.open(url, '_blank', 'noopener');
      return { ok: true };
    },

    fmtHz(hz) {
      const mhz = (Number(hz) || 0) / 1e6;
      return mhz.toFixed(6);
    },
    bearingName(deg) {
      const pts = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
        'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
      return pts[Math.round(((Number(deg) || 0) % 360) / 22.5) % 16];
    },
  };

  global.HD = HD;
})(window);
