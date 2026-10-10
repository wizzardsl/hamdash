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
  // Cards are iframes: their own document, which only ever saw app.css's
  // built-in defaults. The board setting custom properties on its own
  // documentElement could not reach them, so every page that loads this file
  // themes itself and re-themes when the operator changes it.
  async function applyTheme() {
    try {
      const [themes, cfg] = await Promise.all([
        fetch('/api/themes').then((r) => r.json()),
        fetch('/api/config').then((r) => r.json()),
      ]);
      const t = (themes || []).find((x) => x.id === cfg.theme) || (themes || [])[0];
      if (!t) return;
      document.documentElement.dataset.theme = t.id;
      Object.entries(t.vars || {}).forEach(([k, v]) => {
        document.documentElement.style.setProperty(k, v);
      });
      // Anything that copies the variables rather than reading them live -
      // canvas, or a library that writes colours into SVG attributes - needs
      // telling, because applying a theme is async and lands after boot.
      document.dispatchEvent(new CustomEvent('hamdash:theme', { detail: t }));
      HD.units = cfg.units || 'km';
      document.dispatchEvent(new CustomEvent('hamdash:units', { detail: HD.units }));
    } catch (e) {
      // Not silent: a card sitting in last week's colours with no clue why is
      // exactly the sort of thing an empty catch block hides for months.
      console.warn('hamdash: could not apply theme', e);
    }
  }
  applyTheme();
  document.addEventListener('DOMContentLoaded', () => {
    HD.on('config.changed', applyTheme);
  });

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

  // One event stream per browser page, shared by every card on it.
  //
  // A card page is an iframe on the same origin as the board, and browsers
  // allow only about six concurrent connections per origin over HTTP/1.1. A
  // card opening its own EventSource therefore spent one of them for as long
  // as the tile existed: with seven tiles the whole budget was gone, and the
  // next request the board made - any request - queued for ever behind them.
  // It looked like a card that would not load rather than like a connection
  // limit, which is the worst way for this to present.
  //
  // So the top page owns the single stream and hands events down to its
  // frames; a framed page asks its parent instead of opening a socket.
  const framed = (() => {
    try { return window.parent !== window && !!window.parent.document; }
    catch { return false; }          // cross-origin parent: we are on our own
  })();

  function connect() {
    if (es || framed) return;
    es = new EventSource('/api/events');
    es.onmessage = (e) => {
      let evt;
      try { evt = JSON.parse(e.data); } catch { return; }
      deliver(evt);
      relay(evt);
    };
    es.onerror = () => { /* EventSource retries on its own */ };
  }

  // Hand an event to every same-origin frame below this page. The board may
  // install a filter so a card still only sees what its manifest declares.
  function relay(evt) {
    const allow = window.__hamdashRelayFilter;
    document.querySelectorAll('iframe').forEach((f) => {
      try {
        if (!f.contentWindow) return;
        if (allow && !allow(f, evt.topic)) return;
        f.contentWindow.postMessage({ __hamdash: 'event', evt }, window.origin);
      } catch { /* cross-origin frame, not ours to talk to */ }
    });
  }

  if (framed) {
    // The server sends a snapshot of the last value per topic when a stream
    // opens. A framed page has no stream of its own, so it asks for recent
    // history once instead - otherwise a card that renders from the last
    // lookup.result comes up blank until something new happens.
    let primed = false;
    window.__hamdashPrime = () => {
      if (primed) return;
      primed = true;
      setTimeout(() => {
        req('GET', '/api/events/recent', { limit: 60 })
          .then((evts) => (evts || []).forEach(deliver))
          .catch(() => { /* live events still arrive */ });
      }, 120);
    };
    window.addEventListener('message', (e) => {
      if (e.source !== window.parent) return;
      const d = e.data;
      if (d && d.__hamdash === 'event' && d.evt) deliver(d.evt);
    });
  }

  function deliver(evt) {
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

  // GET and DELETE carry their arguments in the query string, everything else
  // in a JSON body. Sending them as a body on a GET is what fetch will happily
  // do and what no server reads: request.args stays empty, the call looks like
  // it worked, and the argument is silently dropped.
  const QUERY = new Set(['GET', 'DELETE', 'HEAD']);

  async function req(method, path, args) {
    const opts = { method, headers: {} };
    let url = path;
    if (args !== undefined && args !== null) {
      if (QUERY.has(method)) {
        const q = new URLSearchParams();
        for (const [k, v] of Object.entries(args)) {
          if (v !== undefined && v !== null && v !== '') q.append(k, v);
        }
        const s = q.toString();
        if (s) url += (path.includes('?') ? '&' : '?') + s;
      } else {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(args);
      }
    }
    const r = await fetch(url, opts);
    const text = await r.text();
    let data = text;
    try { data = text ? JSON.parse(text) : {}; } catch { /* leave as text */ }
    if (!r.ok) {
      const msg = (data && data.error) || `${method} ${url} failed (${r.status})`;
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
      if (framed && window.__hamdashPrime) window.__hamdashPrime();
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

    // The band table, fetched once from the host rather than kept here. Three
    // copies in python had already drifted apart; a fourth in the browser
    // would be a fourth answer to "what band is this".
    bands() {
      if (!HD._bands) HD._bands = req('GET', '/api/bands')
        .then((d) => { HD._bandTable = d.bands || []; return d; })
        .catch(() => { HD._bandTable = []; return { bands: [], names: [] }; });
      return HD._bands;
    },
    // Synchronous once bands() has resolved; '' before that and for anything
    // off band, which is a real answer rather than a guess.
    bandFor(mhz) {
      const f = Number(mhz);
      if (!f || !HD._bandTable) return '';
      const hit = HD._bandTable.find(([lo, hi]) => f >= lo && f <= hi);
      return hit ? hit[2] : '';
    },
    put: (p, body) => req('PUT', p, body || {}),
    // A raw body, for endpoints that take a document rather than JSON -
    // ADIF import being the one that exists.
    async postText(path, text, contentType) {
      const r = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': contentType || 'text/plain' },
        body: text,
      });
      const body = await r.text();
      let data = body;
      try { data = body ? JSON.parse(body) : {}; } catch { /* leave as text */ }
      if (!r.ok) {
        throw new Error((data && data.error)
          || `POST ${path} failed (${r.status})`);
      }
      return data;
    },
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

    // One distance, in the operator's units, everywhere. Showing km and miles
    // side by side asks the reader to pick, which is the card failing to
    // decide rather than being helpful.
    // Flags are served from this app, not a CDN: which countries you work is
    // nobody else's business, and a shack with no internet still gets them.
    flag(iso, title) {
      const code = (iso || '').toLowerCase();
      if (!/^[a-z]{2}$/.test(code)) return '';
      const t = (title || '').replace(/"/g, '&quot;');
      return `<img class="flag" src="/static/flags/${code}.svg" alt="" `
           + `title="${t}" loading="lazy">`;
    },

    units: 'km',
    distanceUnit() {
      return { km: 'km', mi: 'mi', nmi: 'nmi' }[HD.units] || 'km';
    },
    fmtDistance(km, digits) {
      if (km === undefined || km === null || Number.isNaN(km)) return '—';
      const v = HD.units === 'mi' ? km * 0.621371
              : HD.units === 'nmi' ? km * 0.539957 : km;
      const d = digits === undefined ? (v < 100 ? 1 : 0) : digits;
      return `${v.toLocaleString(undefined, { maximumFractionDigits: d })} `
           + HD.distanceUnit();
    },
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
