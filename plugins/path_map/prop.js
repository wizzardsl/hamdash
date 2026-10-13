/* prop.js — what frequency a path will carry, from measured soundings.
 *
 * No DOM, no network: lat/lon and a list of ionosonde readings in,
 * megahertz out. Split out of the card for the same reason `cw.py` and
 * `dsp.py` are split out of theirs - this is the part that can be wrong
 * in a way nobody sees. A propagation number that is 30% out still looks
 * like a propagation number.
 *
 * **It is a measurement, not a model.** The MUF here comes from the
 * nearest ionosonde that has actually sounded the ionosphere recently,
 * and the answer carries how far away that was. Only about thirty of the
 * hundred stations are live at any moment - measured - and they are
 * clustered over Europe, North America and Australia, so for a path
 * across the Pacific the nearest sounding can be thousands of kilometres
 * from where the signal actually refracts. That is worth saying rather
 * than smoothing over, which is why `from` and `far` come back with the
 * answer.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HDProp = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const R = 6371;             // km
  const D2R = Math.PI / 180;
  const R2D = 180 / Math.PI;

  /* Reflection height when the sounder does not say. 300 km is the
     conventional F2 figure and it matters: at a 3000 km hop, 250 km
     gives a factor of 3.63 and 400 km gives 2.77, so this single number
     moves the answer by 30%. */
  const H_DEFAULT = 300;

  /* The longest single F2 hop. Beyond this the ray has to come back down
     and go again, which is what makes a long path need several hops and
     several places where it can fail. */
  const MAX_HOP = 4000;

  /* A sounding older than this is history, not weather. */
  const FRESH_HOURS = 2;

  /* Past this, say so. The nearest live sounder being further away than
     this does not make the answer wrong, it makes it a guess about
     somewhere else. */
  const FAR_KM = 2000;

  /* The secant law, with the Earth's curvature in it.
   *
   * A flat-Earth secant is the textbook starting point and is badly
   * wrong at long distances: it has no limit, where the real geometry
   * tops out near 3.4 because the ray cannot leave at less than zero
   * elevation. Checked against the sounders' own M(3000)F2: this gives
   * 3.28 at 3000 km where they report 2.8 to 3.5, and reproduces one
   * station's published MUF from its published foF2 to 2%.
   */
  function secant(dKm, hKm) {
    const h = hKm > 0 ? hKm : H_DEFAULT;
    const theta = (dKm / 2) / R;                  // half-hop, at the centre
    const el = Math.atan2(Math.cos(theta) - R / (R + h), Math.sin(theta));
    const phi = Math.asin((R / (R + h)) * Math.cos(el));
    return 1 / Math.cos(phi);
  }

  const hopsFor = (km) => Math.max(1, Math.ceil(km / MAX_HOP));

  /* Great-circle distance, in km. */
  function distance(a, b) {
    const dLat = (b.lat - a.lat) * D2R;
    const dLon = (b.lon - a.lon) * D2R;
    const s = Math.sin(dLat / 2) ** 2
      + Math.cos(a.lat * D2R) * Math.cos(b.lat * D2R) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(s)));
  }

  /* A point a fraction of the way along the great circle from a to b. */
  function along(a, b, f) {
    const la1 = a.lat * D2R, lo1 = a.lon * D2R;
    const la2 = b.lat * D2R, lo2 = b.lon * D2R;
    const d = distance(a, b) / R;
    if (d < 1e-9) return { lat: a.lat, lon: a.lon };
    const A = Math.sin((1 - f) * d) / Math.sin(d);
    const B = Math.sin(f * d) / Math.sin(d);
    const x = A * Math.cos(la1) * Math.cos(lo1) + B * Math.cos(la2) * Math.cos(lo2);
    const y = A * Math.cos(la1) * Math.sin(lo1) + B * Math.cos(la2) * Math.sin(lo2);
    const z = A * Math.sin(la1) + B * Math.sin(la2);
    return { lat: Math.atan2(z, Math.hypot(x, y)) * R2D,
             lon: Math.atan2(y, x) * R2D };
  }

  /* Where the path depends on the ionosphere.
   *
   * One hop refracts once, at the middle. More than one hop is governed
   * by its end regions, and the convention is the points 1/6 and 5/6 of
   * the way along - those are the middles of the first and last hops of
   * a two-hop path, and the standard stands in for the rest.
   */
  function controlPoints(a, b, hops) {
    return hops <= 1 ? [along(a, b, 0.5)]
      : [along(a, b, 1 / 6), along(a, b, 5 / 6)];
  }

  /* The soundings, however they arrived.

     `/api/space/muf` answers `{stations: [...]}` and a caller holding
     just the list is also reasonable. Written twice against a guess at
     this shape - once here and once for `HD.bands()` - and both times
     the guess and its tests agreed with each other and not with the
     server. One place to be tolerant beats remembering at each call. */
  function asList(x) {
    if (Array.isArray(x)) return x;
    if (x && Array.isArray(x.stations)) return x.stations;
    if (x && Array.isArray(x.sounders)) return x.sounders;
    return [];
  }

  const ageHours = (iso, now) => {
    const t = Date.parse(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z');
    return Number.isFinite(t) ? (now - t) / 3600e3 : Infinity;
  };

  /* The nearest sounder with a recent reading and a usable foF2. */
  function nearest(point, sounders, now, freshHours) {
    const limit = freshHours === undefined ? FRESH_HOURS : freshHours;
    let best = null;
    asList(sounders).forEach((s) => {
      const fof2 = Number(s.fof2);
      if (!Number.isFinite(fof2) || fof2 <= 0) return;
      const lat = Number(s.lat), lon = Number(s.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
      if (ageHours(String(s.time || ''), now) > limit) return;
      const km = distance(point, { lat, lon });
      if (!best || km < best.km) {
        best = { km, name: s.name || s.code || '?', fof2,
                 md: Number(s.md) > 0 ? Number(s.md) : null,
                 hmf2: Number(s.hmf2) > 0 ? Number(s.hmf2) : null,
                 age_h: ageHours(String(s.time || ''), now) };
      }
    });
    return best;
  }

  /* The MUF one hop of this length can carry, given a sounding.
   *
   * The sounder's own M(3000)F2 is preferred over the geometry wherever
   * it reports one: it is derived from the ionogram and carries the
   * shape of the layer, which a single reflection height cannot. It is
   * scaled to this hop by the ratio of secants, so a 3000 km hop comes
   * back exactly as the sounder's own MUF(3000) and nothing else has to
   * agree with anything.
   */
  function hopMuf(sounding, hopKm) {
    const h = sounding.hmf2 || H_DEFAULT;
    const here = secant(hopKm, h);
    const at3000 = secant(3000, h);
    const factor = sounding.md ? sounding.md * (here / at3000) : here;
    return sounding.fof2 * factor;
  }

  /* What the whole path will carry.
   *
   * The weakest control point governs: a path is only as good as the
   * worst place it has to bounce. Returns null when nothing recent is
   * near enough to say anything, which is an answer and not a failure -
   * most of the Pacific is like that most of the time.
   */
  function pathMuf(a, b, sounders, opts) {
    const o = opts || {};
    const now = o.now || Date.now();
    const km = distance(a, b);
    const hops = hopsFor(km);
    const hopKm = km / hops;
    const points = controlPoints(a, b, hops);
    const controls = points.map((p) => {
      const from = nearest(p, sounders, now, o.freshHours);
      return { at: p, from, muf: from ? hopMuf(from, hopKm) : null };
    });
    const usable = controls.filter((c) => c.muf !== null);
    if (!usable.length) {
      return { km, hops, hopKm, controls, mhz: null,
               why: 'no ionosonde has sounded near this path recently' };
    }
    const worst = usable.reduce((m, c) => (c.muf < m.muf ? c : m), usable[0]);
    const farthest = Math.max(...usable.map((c) => c.from.km));
    return {
      km, hops, hopKm, controls,
      mhz: worst.muf,
      from: worst.from,
      // Honest rather than smooth: how far the supporting evidence is,
      // and whether that is far enough to doubt.
      far: farthest,
      uncertain: farthest > (o.farKm || FAR_KM),
      partial: usable.length < controls.length,
    };
  }

  /* Which ham bands sit inside a window.

     Takes what `HD.bands()` actually returns, which is
     `{bands: [[startMHz, endMHz, name], ...], names: [...]}` - triples,
     wrapped. Written against an invented shape first, which passed its
     own tests perfectly and threw the moment it met the real thing: the
     tests were asserting against the same guess as the code.

     A bare array is accepted too, so a caller holding just the list does
     not have to wrap it. */
  function bandsBetween(bands, lowMhz, highMhz) {
    const list = Array.isArray(bands) ? bands
      : (bands && Array.isArray(bands.bands)) ? bands.bands : [];
    const lo = Number.isFinite(lowMhz) ? lowMhz : 0;
    const hi = Number.isFinite(highMhz) ? highMhz : Infinity;
    if (hi < lo) return [];
    return list.filter((b) => {
      const start = Number(b[0]), end = Number(b[1]);
      if (!Number.isFinite(start) || !Number.isFinite(end)) return false;
      // Any overlap counts: a band half inside the window is usable.
      return end >= lo && start <= hi;
    }).map((b) => String(b[2]));
  }
  /* The same calculation swept across the whole map.

     For every point: what is the MUF for the path from here to there.
     That is a different thing from the familiar MUF map, which is
     MUF(3000) plotted at each place and says nothing about where you
     are standing - this one is centred on the operator by construction,
     because every value is a path from them.

     Coarse on purpose. The field is smooth, so three degrees and let the
     browser interpolate costs a fraction of the work and looks the same
     - the aurora overlay settled on exactly this trade.

     Returns the distance to the supporting sounding alongside the
     frequency, because that is what lets the drawing fade out over the
     oceans instead of pretending.
  */
  function mufField(me, sounders, opts) {
    const o = opts || {};
    const step = o.step || 3;
    const now = o.now || Date.now();
    const w = Math.round(360 / step);
    const h = Math.round(180 / step) + 1;
    const muf = new Float32Array(w * h);
    const far = new Float32Array(w * h);
    // Filter to usable sounders once rather than per point: this runs
    // tens of thousands of times and `nearest` would otherwise re-check
    // every station's age and foF2 on each one.
    const live = asList(sounders).filter((s) => {
      const f = Number(s.fof2);
      return Number.isFinite(f) && f > 0
        && Number.isFinite(Number(s.lat)) && Number.isFinite(Number(s.lon))
        && ageHours(String(s.time || ''), now) <= (o.freshHours === undefined
                                                   ? FRESH_HOURS
                                                   : o.freshHours);
    });
    for (let iy = 0; iy < h; iy++) {
      const lat = -90 + iy * step;
      for (let ix = 0; ix < w; ix++) {
        const lon = -180 + ix * step;
        const i = iy * w + ix;
        const p = pathMuf(me, { lat, lon }, live,
                          { now, freshHours: Infinity, farKm: o.farKm });
        muf[i] = p.mhz === null ? 0 : p.mhz;
        far[i] = p.mhz === null ? Infinity : p.far;
      }
    }
    return { w, h, step, lon0: -180, lat0: -90,
             step_lon: step, step_lat: step, muf, far };
  }

  return { secant, hopsFor, distance, along, controlPoints, nearest,
           asList,
           mufField,
           hopMuf, pathMuf, bandsBetween,
           R, H_DEFAULT, MAX_HOP, FRESH_HOURS, FAR_KM };
}));
