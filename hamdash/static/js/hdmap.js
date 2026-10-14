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

  /* How many worlds are drawn, and why it is not a constant.
   *
   * It was exactly three, with `maxBounds` stopping the pan at 1.5 worlds
   * either side so you could never reach the edge of them. That is a wall:
   * drag west far enough and the map simply refuses, where a tile basemap
   * repeats for ever. The wall was the honest thing to do while the number
   * was fixed - panning into blank background is worse - but the number
   * does not have to be fixed.
   *
   * It follows the view instead. Crossing into a world nobody has drawn
   * yet moves the span and redraws whatever registered an interest, so
   * there is always land - and aurora, and greyline - under wherever you
   * have got to.
   *
   * The span *follows*; it does not only grow. Growing was the first
   * version and it leaks: four drags reached nine worlds west and 630
   * land paths, and nothing was ever going to give them back. What is off
   * screen by more than a world is dropped, so the cost is flat however
   * far somebody drags.
   *
   * Wrapping the view instead - Leaflet's `worldCopyJump` - would be
   * cheaper still and is the wrong trade here: paths are unwrapped into
   * the neighbouring copy on purpose, so a contact at -221 is drawn at
   * -221, and teleporting the view back to the middle copy is precisely
   * the pan somebody is making to reach it.
   */
  let spanLo = -1;
  let spanHi = 1;
  const redrawers = new Set();

  const copies = () => (azimuthal ? [0]
    : Array.from({ length: spanHi - spanLo + 1 },
                 (_, i) => (spanLo + i) * COPY_SPAN));

  /* Register something that draws per world copy. Returns an unsubscribe,
     because a map that is rebuilt (projection change, a weather key) must
     not leave its old layers being redrawn for ever. */
  /* Which world copies a view from `west` to `east` needs.

     Exactly what is on screen, no margin: a margin of one world sounds
     safer and costs five copies at rest where three will do - measured,
     290 land paths against 174, for nothing anybody can see. Growing
     during the drag is what makes a margin unnecessary. */
  function worldSpan(west, east) {
    if (!Number.isFinite(west) || !Number.isFinite(east)) {
      return { lo: -1, hi: 1 };
    }
    return { lo: Math.floor(west / COPY_SPAN),
             hi: Math.ceil(east / COPY_SPAN) };
  }

  function onCopies(fn) {
    redrawers.add(fn);
    return () => redrawers.delete(fn);
  }

  function watchCopies(map) {
    if (azimuthal) return;
    const look = () => {
      const b = map.getBounds();
      // Exactly what is on screen, no margin. A margin of one world
      // sounds safer and costs five copies at rest where three will do -
      // measured: 290 land paths against 174, for nothing anybody can
      // see. Growing *during* the drag is what makes the margin
      // unnecessary, which is why this listens to `move` and not only to
      // `moveend`; the check is two divisions, so it is cheap enough to
      // run on every frame of a pan.
      const { lo, hi } = worldSpan(b.getWest(), b.getEast());
      if (lo === spanLo && hi === spanHi) return;
      spanLo = lo;
      spanHi = hi;
      // One bad drawer must not stop the others: the result of that is a
      // map missing one layer, not a map missing all of them.
      redrawers.forEach((fn) => {
        try { fn(); } catch (e) { /* keep going */ }
      });
    };
    map.on('move moveend zoomend', look);
    look();
  }

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
  function builtInBasemap(opts = {}) {
    // In its own pane, under everything else, which is where a tile basemap
    // goes. It used to share the overlay pane: its land is opaque, so anything
    // drawn on a surface Leaflet stacks lower - a canvas, which it gives
    // z-index 100 against an SVG's 200 - was painted underneath the
    // continents. One copy per world, because a tile layer repeats and
    // anything drawn beside it has to as well.
    const layer = (data, dx, style) => L.geoJSON(data, {
      pane: 'basemap',
      coordsToLatLng: (c) => L.latLng(c[1], c[0] + dx),
      style,
      interactive: false,
      attribution: 'Natural Earth (public domain)',
    });
    const out = [];
    const drawn = new Set();
    const build = (dx) => {
      out.push(layer(window.WORLD_LAND, dx,
                     { color: theme.grip, weight: 0.6,
                       fillColor: theme.panel2, fillOpacity: 1 }));
      // Borders sit on the same pane as the land, added after it so they
      // draw on top of the fill rather than under it. Thinner and dimmer
      // than the coastline: a border is a fact about the land, not an edge
      // of it, and at these zooms a crisp one competes with the data.
      if (opts.borders !== false && window.WORLD_BORDERS) {
        out.push(layer(window.WORLD_BORDERS, dx,
                       { color: theme.grip, weight: 0.7, opacity: 0.95,
                         fill: false, dashArray: '3 2' }));
      }
      if (opts.subdivisions && window.WORLD_SUBDIVISIONS) {
        out.push(layer(window.WORLD_SUBDIVISIONS, dx,
                       { color: theme.grip, weight: 0.5, opacity: 0.6,
                         fill: false }));
      }
      drawn.add(dx);
    };
    const group = L.layerGroup();
    // Land has to reach wherever the pan has got to, or the overlays that
    // follow the view float over blank background - which is precisely
    // what the old hard edge existed to prevent. Rebuilt rather than
    // appended to, because the span moves as well as grows, and three or
    // four GeoJSON layers only change hands when a world boundary is
    // crossed.
    const paint = () => {
      out.length = 0;
      drawn.clear();
      group.clearLayers();
      copies().forEach(build);
      out.forEach((l) => group.addLayer(l));
    };
    paint();
    onCopies(paint);
    return group;
  }

  function basemap(conf) {
    conf = conf || {};
    const lines = { borders: conf.borders !== false,
                    subdivisions: !!conf.subdivisions };
    // Tiles are square in Web Mercator and nothing else, so azimuthal always
    // gets the vector map. Saying so beats drawing a broken one.
    if (azimuthal) return builtInBasemap(lines);
    const pick = conf.basemap || 'built-in (offline)';
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
    return builtInBasemap(lines);
  }

  // ---- the wheel, which is two gestures ------------------------------------
  // A trackpad pinch arrives as a wheel event with `ctrlKey` set - every
  // major browser synthesises it that way - and a two-finger scroll arrives
  // without it. So the two are told apart by asking, rather than by guessing
  // at deltas, and a plain scroll is simply not ours: left alone, it chains
  // out of the iframe and scrolls the board behind it.
  //
  // A mouse has no pinch, so ctrl+wheel does the same job, and the hint says
  // so the first time somebody scrolls over a map. The zoom buttons are
  // there for anyone who does neither.
  const PINCH_PER_LEVEL = 50;   // px of pinch that makes one zoom level
  const GESTURE_GAP = 300;      // ms of quiet that ends a gesture
  const HINT_FOR = 1400;        // ms the hint stays up
  const HINT_EVERY = 8000;      // ms before it is worth saying again

  // How many zoom levels a gesture has earned, and what is left over.
  // Counted first and applied once: Leaflet applies a zoom asynchronously,
  // so a loop that re-read `getZoom()` between steps asked for the same
  // level twice - a pinch worth two levels moved one.
  function zoomSteps(acc, deltaY) {
    acc += deltaY;
    let steps = 0;
    while (acc <= -PINCH_PER_LEVEL) { acc += PINCH_PER_LEVEL; steps += 1; }
    while (acc >= PINCH_PER_LEVEL) { acc -= PINCH_PER_LEVEL; steps -= 1; }
    return { acc, steps };
  }

  function wheelZoom(map) {
    // Not the `el` create() was handed: that may be an element *or* an id,
    // because `L.map` takes either and both map cards pass a string. The
    // container is the one thing that is always an element.
    const el = map.getContainer();
    let acc = 0;
    let last = 0;
    let saidAt = 0;
    let hint = null;
    let hide = null;

    const say = () => {
      const now = Date.now();
      if (now - saidAt < HINT_EVERY) return;
      saidAt = now;
      if (!hint) {
        hint = L.DomUtil.create('div', '', el);
        hint.textContent = 'Pinch, or hold Ctrl and scroll, to zoom';
        hint.style.cssText =
          'position:absolute;inset:auto 0 8px 0;margin:0 auto;width:fit-content;'
          + 'z-index:1000;pointer-events:none;padding:4px 10px;border-radius:999px;'
          + 'font:12px/1 system-ui,sans-serif;opacity:0;transition:opacity .15s;'
          + 'background:var(--panel-2,#222);color:var(--ink,#eee);'
          + 'border:1px solid var(--rule,#444)';
      }
      hint.style.opacity = '1';
      clearTimeout(hide);
      hide = setTimeout(() => { hint.style.opacity = '0'; }, HINT_FOR);
    };

    el.addEventListener('wheel', (e) => {
      if (!e.ctrlKey) {
        // Not ours. No preventDefault, so it scrolls the board.
        say();
        return;
      }
      // Ours, and the browser would otherwise zoom the whole page.
      e.preventDefault();
      const now = Date.now();
      if (now - last > GESTURE_GAP) acc = 0;
      last = now;
      const got = zoomSteps(acc, e.deltaY);
      acc = got.acc;
      if (got.steps) {
        map.setZoomAround(map.mouseEventToContainerPoint(e),
                          map.getZoom() + got.steps);
      }
    }, { passive: false });
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
      // one next door. The copies are really drawn instead.
      o.worldCopyJump = false;
      o.minZoom = 1;
      // Latitude is bounded - there is nothing above the pole and falling
      // off the top is only ever a mistake. Longitude is not: the copies
      // grow to meet the view (see `watchCopies`), so east and west go on
      // as long as somebody keeps dragging.
      o.maxBounds = L.latLngBounds([[-85, -Infinity], [85, Infinity]]);
      o.maxBoundsViscosity = 1;
    }
    // **Scrolling the board must not zoom the map.** A tile is something
    // you scroll past, and a map that grabs the wheel stops the dashboard
    // dead under the cursor. Leaflet's own handler zooms on any wheel, so
    // it is off and `wheelZoom` takes its place.
    o.scrollWheelZoom = false;
    const map = L.map(el, Object.assign(o, opts.leaflet || {}));
    wheelZoom(map);
    // Same z-index as the tile pane: the built-in basemap is a basemap, and
    // every overlay belongs on top of it.
    map.createPane('basemap').style.zIndex = 200;
    if (azimuthal) map.setView([opts.centre.lat, opts.centre.lon], 2);
    else map.setView(opts.view || [20, 0], opts.zoom || 1);
    // After the view is set, so the first look sees where it actually is.
    watchCopies(map);
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

  // ---- the aurora ramp ---------------------------------------------------

  /* Per cent at which the ramp is fully saturated.

     Not 100. OVATION is a probability and a quiet day peaks near 28 - that
     was the whole trouble with the old fixed >=40 / >=20 steps, which on
     such a day put everything in one band and never drew the top one at
     all. Anything above this clamps, so a real storm still reads as the
     strongest the scale goes rather than running off the end of it. */
  const AURORA_FULL = 50;

  const hex2rgb = (h) => {
    const v = String(h).trim().replace('#', '');
    const n = v.length === 3
      ? v.split('').map((c) => c + c).join('')
      : v.slice(0, 6);
    const i = parseInt(n, 16);
    return Number.isFinite(i) ? [(i >> 16) & 255, (i >> 8) & 255, i & 255]
      : [255, 255, 255];
  };

  const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));

  /* Intensity to colour and alpha, continuously.

     Both at once, which is the half that was missing: colour alone has to
     end somewhere, and wherever it ends is a visible edge. Alpha carries
     the faint outer oval down to nothing, so the overlay stops where the
     aurora does instead of where the threshold was.

     Gamma 0.6 on the alpha because a straight ramp makes everything below
     about 15% invisible, and that is most of the field on most days. */
  function fieldRamp(value, full, stops) {
    // `Math.min(1, NaN)` is NaN, so a junk reading would sail straight
    // through the clamp and paint NaN into the image - which is nothing at
    // all, a hole in the overlay rather than an error anybody could see.
    const n = Number(value);
    const t = Number.isFinite(n)
      ? Math.max(0, Math.min(1, n / full)) : 0;
    const [lo, mid, hi] = stops;
    const rgb = t < 0.5 ? mix(lo, mid, t * 2) : mix(mid, hi, (t - 0.5) * 2);
    return [rgb[0], rgb[1], rgb[2], Math.round(255 * Math.pow(t, 0.6))];
  }

  const auroraRamp = (pct, stops) => fieldRamp(pct, AURORA_FULL, stops);

  /* Megahertz at which D-RAP absorption is drawn at full strength.

     A big flare reaches the mid thirties and an ordinary disturbed day is
     five to fifteen, so 30 keeps the common case readable without making
     a severe event look the same as a mild one. */
  const DRAP_FULL = 30;
  const drapRamp = (mhz, stops) => fieldRamp(mhz, DRAP_FULL, stops);

  // ---- space weather -------------------------------------------------------
  // These go through the host, not straight from the card: NOAA's aurora grid
  // is ~1.5 MB of mostly zero, they publish every five minutes, and a card
  // fetching a third-party URL itself depends on that server's CORS headers,
  // which are not ours. The host caches all of it for five minutes.
  function spaceLayers(map, opts = {}) {
    const say = opts.report || (() => {});
    /* The space overlays get their own canvas, and it must never take
       the mouse.

       It lands in `overlayPane` *after* whatever renderer the calling
       card already made, and a Leaflet canvas does not let events
       through to the canvas beneath it - so this one sat on top of the
       card's own dots and swallowed every hover and click. It presents
       as a card whose markers have gone dead, and it comes and goes:
       `psk_reporter` only puts its spots on a canvas once there are
       enough of them, so a quiet band works and a busy one does not.
       Measured on a live board - 786 stations, 2358 markers, all on
       canvas, and `elementFromPoint` over one of them returned *this*
       canvas every time.

       Nothing drawn here is interactive: the aurora and D-RAP fallback
       dots are `interactive: false` and the ionosondes, which do have
       tooltips, use the map's default SVG renderer. So there is nothing
       to lose by making it inert, and the alternative - a pane of its
       own - would reorder the drawing as well, putting the greyline on
       a different level from the aurora.

       `_container` is Leaflet's, not ours; Leaflet is vendored and
       pinned, and the guard is there so a future one cannot throw. */
    const canvas = L.canvas({ padding: 0.5 }).addTo(map);
    if (canvas._container) canvas._container.style.pointerEvents = 'none';
    const groups = {
      greyline: L.layerGroup(),
      aurora: L.layerGroup(),
      drap: L.layerGroup(),
      muf: L.layerGroup(),
      weather: null,
    };

    /* The aurora is a field, so it is drawn as an image and not as dots.
     *
     * It used to be one `circleMarker` per cell at `radius: 3`. Three
     * *screen* pixels is a fixed size at every zoom, so the dots could tile
     * the globe at one zoom and no other: zoomed out they left gaps and
     * read as stipple, zoomed in they were isolated specks. Thresholding at
     * 8% also cut the oval off square at an 8% contour, and three hard
     * colour steps (>=40, >=20, else) drew two visible bands through it -
     * and on a quiet day, when the whole field peaks near 28%, the top band
     * never appeared at all and most of the oval came out one flat colour.
     *
     * One `imageOverlay` per world copy instead, built at the grid's own
     * resolution and left for the browser to scale. The smoothness is the
     * GPU's bilinear filtering, which costs nothing: 1 image against 7636
     * cells x 3 copies = 22908 layer objects.
     */
    // Read at draw time, not captured: applying a theme is async and a
    // colour copied at boot is the stylesheet's default (see hamdash:theme).
    const auroraColour = (pct) => auroraRamp(pct, [
      hex2rgb(theme.signal), hex2rgb(theme.accent), hex2rgb(theme.alert)]);

    // base64 -> bytes. `atob` gives a string of char codes; there is no
    // shorter way without a dependency.
    function gridBytes(g) {
      const bin = atob(g.data);
      const out = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
      return out;
    }

    /* One image per world copy, placed on the grid's own extent.

       The cell values sit at cell *centres*, so the image is stretched
       half a cell beyond them on each side - without that the field is
       drawn half a cell north-west of where it belongs, which is a small
       enough error to look like nothing and large enough to matter at
       D-RAP's 4 degrees of longitude. */
    function placeField(group, g, url, opacity) {
      const hx = g.step_lon / 2;
      const hy = g.step_lat / 2;
      const south = g.lat0 - hy;
      const north = g.lat0 + (g.h - 1) * g.step_lat + hy;
      const west = g.lon0 - hx;
      const east = g.lon0 + (g.w - 1) * g.step_lon + hx;
      each((dx) => L.imageOverlay(url,
        [[south, west + dx], [north, east + dx]],
        { opacity, interactive: false }).addTo(group));
    }

    function fieldImage(g, bytes, colour) {
      const c = document.createElement('canvas');
      c.width = g.w;
      c.height = g.h;
      const img = c.getContext('2d').createImageData(g.w, g.h);
      const px = img.data;
      for (let iy = 0; iy < g.h; iy++) {
        // The grid runs south to north; image rows run top down.
        const row = (g.h - 1 - iy) * g.w;
        for (let ix = 0; ix < g.w; ix++) {
          const pct = bytes[iy * g.w + ix];
          const o = (row + ix) * 4;
          if (!pct) { px[o + 3] = 0; continue; }
          const [r, gg, b, a] = colour(pct);
          px[o] = r; px[o + 1] = gg; px[o + 2] = b; px[o + 3] = a;
        }
      }
      c.getContext('2d').putImageData(img, 0, 0);
      return c.toDataURL('image/png');
    }

    async function drawAurora() {
      try {
        const d = await HD.get('/api/space/aurora', { threshold: 1, grid: 1 });
        groups.aurora.clearLayers();
        if (!d.ok) { say({ error: d.error || 'no aurora data' }); return; }
        say({ error: '' });
        if (!d.grid || !d.grid.data) { drawAuroraDots(d); return; }
        placeField(groups.aurora, d.grid,
                   fieldImage(d.grid, gridBytes(d.grid), auroraColour), 0.75);
      } catch (e) { say({ error: `aurora: ${e.message}` }); }
    }

    /* If the host is older than the grid field, or NOAA publishes something
       that is not a regular grid, there is still an overlay. */
    function drawAuroraDots(d) {
      (d.cells || []).forEach(([lon, lat, pct]) => {
        if (pct < 8) return;
        each((dx) => L.circleMarker([lat, lon + dx], {
          renderer: canvas, radius: 3, stroke: false,
          fillColor: pct >= 40 ? theme.alert
            : pct >= 20 ? theme.accent : theme.signal,
          fillOpacity: Math.min(0.8, 0.12 + pct / 100),
          interactive: false,
        }).addTo(groups.aurora));
      });
    }

    const drapColour = (mhz) => drapRamp(mhz, [
      hex2rgb(theme.signal), hex2rgb(theme.accent), hex2rgb(theme.alert)]);

    /* If the host is older than the grid field. Same reason as the
       aurora's: an ugly overlay beats none. */
    function drawDrapDots(d) {
      (d.cells || []).forEach(([lon, lat, mhz]) => {
        each((dx) => L.circleMarker([lat, lon + dx], {
          renderer: canvas, radius: 4, stroke: false,
          fillColor: mhz >= 20 ? theme.alert
            : mhz >= 10 ? theme.accent : theme.signal,
          fillOpacity: Math.min(0.75, 0.2 + mhz / 40), interactive: false,
        }).addTo(groups.drap));
      });
    }

    async function drawDrap() {
      try {
        const d = await HD.get('/api/space/drap',
                               { threshold: 1, grid: 1 });
        groups.drap.clearLayers();
        if (!d.ok) { say({ error: d.error }); return; }
        say({ error: '' });
        if (d.quiet) {
          // An empty layer here is the good news, not a failure to load.
          say({ detail: `no HF absorption anywhere (${d.valid_at})` });
          return;
        }
        if (d.grid && d.grid.data) {
          const bytes = gridBytes(d.grid);
          placeField(groups.drap, d.grid,
                     fieldImage(d.grid, bytes, drapColour), 0.7);
          say({ detail: `absorption up to ${Math.max(...bytes)} MHz`
                        + ` (${d.valid_at})` });
          return;
        }
        drawDrapDots(d);
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

    // Whatever is switched on gets redrawn into the new world copy. sync()
    // already draws only the layers that are on, so this costs nothing for
    // the ones that are off.
    onCopies(() => sync());

    return { groups, overlays, sync, canvas,
             draw: { greyline: () => drawGreyline(groups.greyline),
                     aurora: drawAurora, drap: drawDrap, muf: drawMuf } };
  }

  return {
    zoomSteps, auroraRamp, drapRamp, fieldRamp, hex2rgb,
    AURORA_FULL, DRAP_FULL,

    theme, readTheme, D2R,
    toLatLon, toGrid, unwrapLon, endpoint, greatCircle,
    copies, each, shift, WORLD_EDGE, onCopies, worldSpan,
    subsolar, nightPolygon, drawGreyline,
    azimuthalCRS, builtInBasemap, basemap,
    create, resetControl, spaceLayers,
    get azimuthal() { return azimuthal; },
  };
})();
