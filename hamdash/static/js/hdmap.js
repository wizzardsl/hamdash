/* hdmap.js — the map machinery any card can build a map out of.

   Leaflet gives you a map. This gives you the parts that took a while to get
   right and that every map card here would otherwise have to get right again:
   a world that repeats, an azimuthal projection centred on the operator, the
   offline basemap, the greyline, and the space-weather overlays.

   It is deliberately not a card and not a widget. A card still builds its own
   map, draws its own data and owns its own layer control; this hands it the
   pieces. Load it after leaflet.js, proj4.js, proj4leaflet.js and world.js.

   Everything here is client-side except the space overlays, which go through
   the host (`space` permission) - see /api/space/* for why.
*/
window.HDMap = (function () {
  const D2R = Math.PI / 180;

  // Leaflet writes colours into SVG presentation attributes, where var(--x)
  // does not resolve. Read the theme's values instead, so a map still follows
  // whichever theme the board is on - and re-read them when it changes,
  // because anything that copied a colour is now holding an old one.
  const css = (name, fallback) =>
    getComputedStyle(document.body).getPropertyValue(name).trim() || fallback;
  const theme = {};
  function readTheme() {
    Object.assign(theme, {
      bg: css('--bg', '#0d1419'),
      ink: css('--ink', '#d7e3ea'),
      rule: css('--rule', '#26363f'),
      grip: css('--grip', '#3b4d58'),
      panel2: css('--panel-2', '#1c2932'),
      accent: css('--accent', '#e9a13b'),
      signal: css('--signal', '#56b6a8'),
      alert: css('--alert', '#d9604a'),
    });
    return theme;
  }
  readTheme();
  window.addEventListener('hamdash:theme', readTheme);

  // ---- where the world repeats ---------------------------------------------
  // Unwrapping a path across the Pacific puts its far end in the world copy
  // next door - a station in Japan lands at -221, which is where Japan is one
  // world west. A tile basemap repeats for ever, so that is a real place with
  // land under it; the vector one has to be drawn three times to match. See
  // the note on `copies`.
  const COPY_SPAN = 360;
  const WORLD_EDGE = COPY_SPAN * 1.5;
  let azimuthal = false;          // set by create(); there are no copies in aeqd

  const copies = () => (azimuthal ? [0] : [-COPY_SPAN, 0, COPY_SPAN]);

  // ---- Maidenhead ----------------------------------------------------------
  // Two to eight characters. PSK Reporter reports ten - FT8 stations that
  // know their position to a few metres - and the extra pair is finer than a
  // pixel at any zoom this draws at, so it is read and discarded rather than
  // rejecting the whole locator and losing the station.
  function toLatLon(loc) {
    const s = (loc || '').trim().toUpperCase().slice(0, 8);
    if (!/^[A-R]{2}[0-9]{2}([A-X]{2}([0-9]{2})?)?$/.test(s)) return null;
    const A = (c) => c.charCodeAt(0) - 65;
    let lon = A(s[0]) * 20 - 180, lat = A(s[1]) * 10 - 90;
    lon += (+s[2]) * 2; lat += (+s[3]);
    if (s.length >= 6) {
      lon += A(s[4]) * (2 / 24); lat += A(s[5]) * (1 / 24);
      lon += 1 / 24; lat += 1 / 48;
    } else { lon += 1; lat += 0.5; }
    return { lat, lon };
  }

  // The other way round, for turning a click on the map into somewhere a
  // service that speaks Maidenhead will understand.
  function toGrid(lat, lon, precision = 6) {
    // Fold back into one world first: a click at -221 is a real place, but
    // its locator is the one at +139.
    let x = ((lon + 180) % 360 + 360) % 360;
    let y = Math.min(179.999, Math.max(0, lat + 90));
    const A = (n) => String.fromCharCode(65 + n);
    let out = A(Math.floor(x / 20)) + A(Math.floor(y / 10))
            + Math.floor((x % 20) / 2) + Math.floor(y % 10);
    if (precision >= 6) {
      out += String.fromCharCode(97 + Math.floor(((x % 2) / 2) * 24))
           + String.fromCharCode(97 + Math.floor((y % 1) * 24));
    }
    return out;
  }

  // ---- great circle --------------------------------------------------------
  // Longitudes are unwrapped, never split at the antimeridian. Splitting a
  // great circle into two polylines leaves one stopping dead at +-180 with no
  // station on the end - it reads as a dot that failed to draw, and the real
  // end is 360 degrees away off the other side.
  function unwrapLon(fromLon, lon) {
    let out = lon;
    while (out - fromLon > 180) out -= 360;
    while (out - fromLon < -180) out += 360;
    return out;
  }

  // Where to put the far end so it lines up with the path drawn from `from`.
  function endpoint(from, to) {
    return { lat: to.lat, lon: from ? unwrapLon(from.lon, to.lon) : to.lon };
  }

  function greatCircle(a, b, steps = 180) {
    const vec = (p) => {
      const la = p.lat * D2R, lo = p.lon * D2R;
      return [Math.cos(la) * Math.cos(lo), Math.cos(la) * Math.sin(lo), Math.sin(la)];
    };
    const [v1, v2] = [vec(a), vec(b)];
    const dot = Math.max(-1, Math.min(1, v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2]));
    const om = Math.acos(dot);
    const run = []; let prev = null;
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      let p = v1;
      if (om >= 1e-9) {
        const s1 = Math.sin((1 - t) * om) / Math.sin(om);
        const s2 = Math.sin(t * om) / Math.sin(om);
        p = [v1[0] * s1 + v2[0] * s2, v1[1] * s1 + v2[1] * s2, v1[2] * s1 + v2[2] * s2];
      }
      const lat = Math.atan2(p[2], Math.hypot(p[0], p[1])) / D2R;
      let lon = Math.atan2(p[1], p[0]) / D2R;
      if (prev !== null) lon = unwrapLon(prev, lon);
      run.push([lat, lon]);
      prev = lon;
    }
    return run;
  }

  // Draw one thing once per world copy. Everything on a flat map goes through
  // here, or it exists in only one of the three and vanishes as you pan.
  function each(fn) { copies().forEach(fn); }
  const shift = (run, dx) => run.map(([lat, lon]) => [lat, lon + dx]);

  // ---- greyline ------------------------------------------------------------
  // Where the sun is, from the clock. No network and no service: this is a few
  // lines of trigonometry and asking a server would be a round trip for an
  // answer the browser already has everything to compute.
  function subsolar(when) {
    const n = when.getTime() / 86400000 + 2440587.5 - 2451545.0;   // days from J2000
    const L = (280.460 + 0.9856474 * n) % 360;
    const g = ((357.528 + 0.9856003 * n) % 360) * D2R;
    const lam = (L + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * D2R;
    const eps = (23.439 - 0.0000004 * n) * D2R;
    const dec = Math.asin(Math.sin(eps) * Math.sin(lam));
    const ra = Math.atan2(Math.cos(eps) * Math.sin(lam), Math.cos(lam));
    const gmst = (18.697374558 + 24.06570982441908 * n) % 24;
    let lon = (ra / D2R - gmst * 15) % 360;
    if (lon > 180) lon -= 360;
    if (lon < -180) lon += 360;
    return { lat: dec / D2R, lon, dec };
  }

  function nightPolygon(when) {
    const sun = subsolar(when);
    const ring = [];
    for (let lon = -180; lon <= 180; lon += 1) {
      // Terminator latitude for this meridian: the circle 90 degrees from the
      // subsolar point.
      const lat = Math.atan(-Math.cos((lon - sun.lon) * D2R) / Math.tan(sun.dec)) / D2R;
      ring.push([lat, lon]);
    }
    // Close the ring over whichever pole is in darkness.
    const pole = sun.dec > 0 ? -90 : 90;
    ring.push([pole, 180], [pole, -180]);
    return { ring, sun };
  }

  function drawGreyline(group) {
    if (!group) return;
    group.clearLayers();
    const { ring, sun } = nightPolygon(new Date());
    each((dx) => {
      L.polygon(shift(ring, dx),
                { stroke: false, fillColor: '#000', fillOpacity: 0.34,
                  interactive: false }).addTo(group);
    });
    // A marker, not a circleMarker: the subsolar point is a symbol, not
    // another station, and it was reading as one more dot on the map. A
    // divIcon also keeps it the same size however far the map is zoomed.
    const rays = Array.from({ length: 8 }, (_, i) => {
      const a = (i * 45) * D2R;
      return `<line x1="${11 + 6 * Math.cos(a)}" y1="${11 + 6 * Math.sin(a)}"`
           + ` x2="${11 + 9.5 * Math.cos(a)}" y2="${11 + 9.5 * Math.sin(a)}"/>`;
    }).join('');
    each((dx) => L.marker([sun.lat, sun.lon + dx], {
      interactive: false,
      keyboard: false,
      icon: L.divIcon({
        className: 'sun-icon',
        iconSize: [22, 22],
        iconAnchor: [11, 11],
        html: `<svg viewBox="0 0 22 22" width="22" height="22">
                 <g stroke="${theme.accent}" stroke-width="1.6"
                    stroke-linecap="round">${rays}</g>
                 <circle cx="11" cy="11" r="4.6" fill="${theme.accent}"
                         stroke="${theme.bg}" stroke-width="1"/>
               </svg>`,
      }),
    }).addTo(group));
  }

  // ---- projection ----------------------------------------------------------
  // Azimuthal equidistant centred on the operator: every bearing leaves the
  // middle as a straight line and every distance from the centre is to scale,
  // which is the projection a beam heading actually lives in.
  //
  // It cannot be combined with tiles. No tile service publishes tiles in this
  // projection, so the base map is forced to the built-in vector one - which
  // is GeoJSON, and reprojects like any other geometry.
  function azimuthalCRS(at) {
    const proj = `+proj=aeqd +lat_0=${at.lat} +lon_0=${at.lon} `
               + '+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs';
    const half = 20037508.34;            // half the planet, in metres
    const res = [];
    for (let z = 0; z < 12; z++) res.push((2 * half / 256) / Math.pow(2, z));
    return new L.Proj.CRS('AEQD:hamdash', proj, {
      resolutions: res,
      origin: [-half, half],
      bounds: L.bounds([-half, -half], [half, half]),
    });
  }

  // ---- basemaps ------------------------------------------------------------
  function builtInBasemap() {
    // In its own pane, under everything else, which is where a tile basemap
    // goes. It used to share the overlay pane: its land is opaque, so anything
    // drawn on a surface Leaflet stacks lower - a canvas, which it gives
    // z-index 100 against an SVG's 200 - was painted underneath the
    // continents. One copy per world, because a tile layer repeats and
    // anything drawn beside it has to as well.
    const land = (dx) => L.geoJSON(window.WORLD_LAND, {
      pane: 'basemap',
      coordsToLatLng: (c) => L.latLng(c[1], c[0] + dx),
      style: { color: theme.grip, weight: 0.6, fillColor: theme.panel2,
               fillOpacity: 1 },
      interactive: false,
      attribution: 'Natural Earth (public domain)',
    });
    return L.layerGroup(copies().map(land));
  }

  function basemap(conf) {
    // Tiles are square in Web Mercator and nothing else, so azimuthal always
    // gets the vector map. Saying so beats drawing a broken one.
    if (azimuthal) return builtInBasemap();
    const pick = (conf || {}).basemap || 'built-in (offline)';
    if (pick === 'OpenStreetMap') {
      return L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 18,
        attribution: '&copy; OpenStreetMap contributors',
      });
    }
    if (pick === 'custom tiles' && (conf.tile_url || '').trim()) {
      return L.tileLayer(conf.tile_url.trim(), {
        maxZoom: 19,
        attribution: conf.tile_attribution || 'custom tiles',
      });
    }
    return builtInBasemap();
  }

  // ---- the map itself ------------------------------------------------------
  function create(el, opts = {}) {
    azimuthal = !!opts.azimuthal;
    const o = { zoomControl: true, attributionControl: true, minZoom: 0 };
    if (azimuthal) {
      o.crs = azimuthalCRS(opts.centre);
      o.worldCopyJump = false;         // there are no world copies in aeqd
    } else {
      // Not worldCopyJump: it teleports the view back to the middle copy,
      // which is precisely the pan being made to reach something drawn in the
      // one next door. The copies are really drawn instead, and panning stops
      // where they do rather than at an arbitrary edge.
      o.worldCopyJump = false;
      o.minZoom = 1;
      o.maxBounds = L.latLngBounds([[-85, -WORLD_EDGE], [85, WORLD_EDGE]]);
      o.maxBoundsViscosity = 1;
    }
    const map = L.map(el, Object.assign(o, opts.leaflet || {}));
    // Same z-index as the tile pane: the built-in basemap is a basemap, and
    // every overlay belongs on top of it.
    map.createPane('basemap').style.zIndex = 200;
    if (azimuthal) map.setView([opts.centre.lat, opts.centre.lon], 2);
    else map.setView(opts.view || [20, 0], opts.zoom || 1);
    return map;
  }

  // A control with one button, for "put the view back".
  function resetControl(map, onClick, title = 'Back to the default view') {
    const Reset = L.Control.extend({
      onAdd() {
        const a = L.DomUtil.create('a', 'leaflet-bar');
        a.href = '#'; a.title = title;
        a.style.cssText = 'width:26px;height:26px;line-height:26px;'
                        + 'text-align:center;display:block';
        a.innerHTML = '&#8962;';
        L.DomEvent.on(a, 'click', L.DomEvent.stop).on(a, 'click', onClick);
        return a;
      },
    });
    new Reset({ position: 'topleft' }).addTo(map);
  }

  // ---- space weather -------------------------------------------------------
  // These go through the host, not straight from the card: NOAA's aurora grid
  // is ~1.5 MB of mostly zero, they publish every five minutes, and a card
  // fetching a third-party URL itself depends on that server's CORS headers,
  // which are not ours. The host caches all of it for five minutes.
  function spaceLayers(map, opts = {}) {
    const say = opts.report || (() => {});
    const canvas = L.canvas({ padding: 0.5 }).addTo(map);
    const groups = {
      greyline: L.layerGroup(),
      aurora: L.layerGroup(),
      drap: L.layerGroup(),
      muf: L.layerGroup(),
      weather: null,
    };

    async function drawAurora() {
      try {
        const d = await HD.get('/api/space/aurora', { threshold: 8 });
        groups.aurora.clearLayers();
        if (!d.ok) { say({ error: d.error || 'no aurora data' }); return; }
        say({ error: '' });
        // Canvas, not SVG: this is a couple of thousand cells and one DOM
        // node each would make panning crawl.
        d.cells.forEach(([lon, lat, pct]) => {
          each((dx) => L.circleMarker([lat, lon + dx], {
            renderer: canvas, radius: 3, stroke: false,
            fillColor: pct >= 40 ? theme.alert
              : pct >= 20 ? theme.accent : theme.signal,
            fillOpacity: Math.min(0.8, 0.12 + pct / 100),
            interactive: false,
          }).addTo(groups.aurora));
        });
      } catch (e) { say({ error: `aurora: ${e.message}` }); }
    }

    async function drawDrap() {
      try {
        const d = await HD.get('/api/space/drap', { threshold: 1 });
        groups.drap.clearLayers();
        if (!d.ok) { say({ error: d.error }); return; }
        say({ error: '' });
        if (d.quiet) {
          // An empty layer here is the good news, not a failure to load.
          say({ detail: `no HF absorption anywhere (${d.valid_at})` });
          return;
        }
        d.cells.forEach(([lon, lat, mhz]) => {
          each((dx) => L.circleMarker([lat, lon + dx], {
            renderer: canvas, radius: 4, stroke: false,
            fillColor: mhz >= 20 ? theme.alert
              : mhz >= 10 ? theme.accent : theme.signal,
            fillOpacity: Math.min(0.75, 0.2 + mhz / 40), interactive: false,
          }).addTo(groups.drap));
        });
        say({ detail: `absorption up to `
                      + `${Math.max(...d.cells.map((c) => c[2]))} MHz (${d.valid_at})` });
      } catch (e) { say({ error: `D-RAP: ${e.message}` }); }
    }

    async function drawMuf() {
      try {
        const d = await HD.get('/api/space/muf');
        groups.muf.clearLayers();
        if (!d.ok) { say({ error: d.error }); return; }
        say({ error: '' });
        d.stations.forEach((st) => {
          const muf = st.mufd;
          // Measured points, not a field. The familiar smooth MUF map is an
          // interpolation of exactly these stations; drawing the measurements
          // says what is known and where, without inventing the rest.
          const colour = muf === null ? theme.grip
            : muf >= 28 ? theme.signal : muf >= 14 ? theme.accent : theme.alert;
          each((dx) => L.circleMarker([st.lat, st.lon + dx], {
            radius: 5, weight: 1, color: theme.bg, fillColor: colour,
            fillOpacity: 1,
          }).bindTooltip(muf === null ? st.name : `${st.name}: ${muf} MHz`,
                         { direction: 'top' })
            .bindPopup(`<h3>${st.code || st.name}</h3><dl>`
              + `<dt>Station</dt><dd>${st.name}</dd>`
              + (muf !== null ? `<dt>MUF(3000)</dt><dd>${muf} MHz</dd>` : '')
              + (st.fof2 !== null ? `<dt>foF2</dt><dd>${st.fof2} MHz</dd>` : '')
              + `<dt>Measured</dt><dd>${(st.time || '').replace('T', ' ')}</dd>`
              + '</dl>')
            .addTo(groups.muf));
        });
        say({ detail: `${d.stations.length} ionosondes · ${d.credit}` });
      } catch (e) { say({ error: `MUF: ${e.message}` }); }
    }

    // Clouds need somebody's API key, so the layer only exists once there is
    // one. An entry that always fails is worse than no entry.
    const key = (opts.owmKey || '').trim();
    if (key && !azimuthal) {
      groups.weather = L.tileLayer(
        `https://tile.openweathermap.org/map/clouds_new/{z}/{x}/{y}.png?appid=${key}`,
        { opacity: 0.45, attribution: '&copy; OpenWeatherMap' });
    }

    const overlays = {
      'Greyline': groups.greyline,
      'Aurora (NOAA)': groups.aurora,
      'Absorption (D-RAP)': groups.drap,
      'Ionosondes (MUF)': groups.muf,
    };
    if (groups.weather) overlays.Clouds = groups.weather;

    // Draw only what is switched on: these are somebody else's servers.
    function sync() {
      if (map.hasLayer(groups.greyline)) drawGreyline(groups.greyline);
      else groups.greyline.clearLayers();
      [[groups.aurora, drawAurora], [groups.drap, drawDrap],
       [groups.muf, drawMuf]].forEach(([g, draw]) => {
        if (map.hasLayer(g)) draw(); else g.clearLayers();
      });
    }

    return { groups, overlays, sync, canvas,
             draw: { greyline: () => drawGreyline(groups.greyline),
                     aurora: drawAurora, drap: drawDrap, muf: drawMuf } };
  }

  return {
    theme, readTheme, D2R,
    toLatLon, toGrid, unwrapLon, endpoint, greatCircle,
    copies, each, shift, WORLD_EDGE,
    subsolar, nightPolygon, drawGreyline,
    azimuthalCRS, builtInBasemap, basemap,
    create, resetControl, spaceLayers,
    get azimuthal() { return azimuthal; },
  };
})();
