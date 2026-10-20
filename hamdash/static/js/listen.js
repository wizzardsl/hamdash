/* Listening to the radio's receive audio, from any card's page.
 *
 * This was the Receive audio card's own page. It lives at the bottom of
 * the rig card now: the audio is a particular radio's, so the controls
 * belong beside that radio's dial, not in a tile of their own. The
 * capture, decimation and ring are still the `rx_audio` card's process -
 * a sound card that crashes takes that process down and leaves the rig
 * control running - and this file is the one copy of the player, so the
 * page that hosts it does not grow a second one to drift.
 *
 * The host lets a card read only its own tiles' settings, so `rx_audio`
 * cannot see which input the rig tile chose. The page that mounts this
 * passes `conf()` - device, rate, buffer_ms from its own tile - and every
 * request carries the device and rate.
 *
 *   HDListen.mount(element, { conf: () => ({ device, rate, buffer_ms }) })
 */
(function () {
  const ENGINE = 'rx_audio';
  // Volume and ears are this person's, at this screen, for this tile: kept
  // in the browser (like the volume always was), and per tile, because two
  // rig tiles playing two radios want two levels - and one in each ear.
  // The old browser-wide volume is read as the starting point, so a level
  // set before this still comes across.
  const OLD_VOL_KEY = 'rx_audio.vol';
  const key = (what, uid) => `rx_audio.${what}.${uid || 'page'}`;

  /* Where this tile's audio goes: both ears, or one.
   *
   * Two radios can be heard at once - every tile's player is mixed by the
   * browser - but mixed in both ears they are hard to tell apart. One on
   * the left and one on the right is how two receivers are listened to.
   *
   * 'stereo' does not go through a panner at all. A StereoPannerNode at
   * its centre turns a mono source down by 3 dB (equal power), so routing
   * the ordinary case through one would make it quieter than it was.
   * Left and right are a panner at -1 and +1, which for a mono source is
   * full level in that ear and nothing in the other.
   */
  const ROUTES = { stereo: 0, left: -1, right: 1 };

  function route(ctx, from, choice, prev) {
    try { from.disconnect(); } catch (e) { /* not connected yet */ }
    if (prev) { try { prev.disconnect(); } catch (e) { /* gone */ } }
    const pan = ROUTES[choice];
    if (!pan || typeof ctx.createStereoPanner !== 'function') {
      from.connect(ctx.destination);
      return null;
    }
    const panner = ctx.createStereoPanner();
    panner.pan.value = pan;
    from.connect(panner);
    panner.connect(ctx.destination);
    return panner;
  }

  const CSS = `
  .listen { display: flex; flex-direction: column; gap: 6px; }
  .listen .row { display: flex; gap: 8px; align-items: center; }
  .listen .row[hidden] { display: none; }  /* author display beats UA [hidden] */
  .listen button.play { min-width: 76px; }
  .listen input[type=range] { flex: 1; min-width: 60px;
                              accent-color: var(--accent); }
  .listen select { font: inherit; font-size: 12px; color: var(--ink);
                   background: var(--bg); border: 1px solid var(--rule);
                   border-radius: var(--radius); padding: 2px 4px; }
  /* The level meter: the one thing that says "audio is actually moving"
     before you have turned the volume up. */
  .listen .lmeter { height: 8px; border: 1px solid var(--rule);
                    border-radius: var(--radius); overflow: hidden; flex: 1;
                    background: var(--panel-2); }
  .listen .lmeter i { display: block; height: 100%; width: 0;
                      background: var(--signal); transition: width .08s linear; }
  .listen .lmeter.hot i { background: var(--alert); }
  .listen .lnote { color: var(--muted); font-size: 11px; margin: 0;
                   overflow-wrap: anywhere; }
  .listen .lnote.warn { color: var(--alert); }
  .listen .link { display: flex; gap: 10px; font-size: 11px;
                  color: var(--muted); font-family: var(--mono); }
  .listen .link[hidden] { display: none; }
  .listen .link b { font-weight: 600; color: var(--ink); }
  .listen .link.bad b { color: var(--alert); }`;

  const HTML = `
  <div class="row">
    <button class="play" data-l="go">Listen</button>
    <button class="play" data-l="stop" hidden>Stop</button>
    <div class="lmeter" data-l="meter"><i></i></div>
  </div>
  <div class="row">
    <span class="lnote">Volume</span>
    <input type="range" data-l="vol" min="0" max="100" value="70"
           aria-label="Volume">
    <span class="lnote" data-l="volnum">70</span>
    <select data-l="ears" aria-label="Which ear"
            title="Both ears, or one - for listening to two radios at once">
      <option value="stereo">Stereo</option>
      <option value="left">Left</option>
      <option value="right">Right</option>
    </select>
  </div>
  <div class="link" data-l="link" hidden>
    <span>delay <b data-l="lat">—</b></span>
    <span>dropouts <b data-l="drops">0</b></span>
    <span><b data-l="kbps">—</b></span>
  </div>
  <p class="lnote" data-l="status">Starting…</p>`;

  /* Who this page is, for as long as it is loaded.
   *
   * The engine counts listeners so it knows when to let the sound card go,
   * and it used to count them by *tile* uid - which is the same for every
   * browser showing that tile. Two browsers on one board, which is the
   * whole point of playing the radio's audio over the network, were one
   * listener as far as the engine could tell: either one pressing Stop took
   * the device from the other, whose next poll took it straight back. It
   * reads as audio that will not start.
   *
   * `crypto.randomUUID` is not used: it is secure-context only, and this
   * board is plain HTTP on a LAN address exactly when it matters most.
   * `getRandomValues` has no such restriction.
   */
  const WHO = (() => {
    try {
      const a = new Uint32Array(2);
      crypto.getRandomValues(a);
      return a[0].toString(36) + a[1].toString(36);
    } catch (e) {
      return Math.random().toString(36).slice(2) + Date.now().toString(36);
    }
  })();

  const med = (a) => {
    if (!a.length) return null;
    const s = [...a].sort((x, y) => x - y);
    return s[Math.floor(s.length / 2)];
  };

  function mount(root, opts) {
    if (!document.getElementById('hd-listen-css')) {
      const style = document.createElement('style');
      style.id = 'hd-listen-css';
      style.textContent = CSS;
      document.head.appendChild(style);
    }
    root.classList.add('listen');
    root.innerHTML = HTML;
    const $ = (k) => root.querySelector(`[data-l="${k}"]`);
    const conf = () => (opts && opts.conf && opts.conf()) || {};

    // Which input and quality, on every request: the engine cannot read
    // this page's tile settings itself.
    const params = () => {
      const c = conf();
      const q = new URLSearchParams({ who: WHO });
      if (c.device) q.set('device', c.device);
      if (c.rate) q.set('rate', c.rate);
      // 'rig' means the audio of *this tile's* radio, so say which.
      if (c.radio && c.radio !== 'selected') q.set('rig', c.radio);
      return q.toString();
    };

    let ctx = null, gain = null, panner = null;
    const uid = (opts && opts.uid) || (window.HD && HD.tileUid) || '';
    let running = false, timer = null;
    // The audio request on its way, if any. Stop waits for it: a request
    // already sent when Stop is pressed can reach the engine *after* the
    // stop, re-open the sound card and keep it for the 20 s the reaper
    // allows - measured on the FT-900's USB interface, `last listener
    // left` followed twenty seconds later by a second close.
    let inflight = null;
    let nextAt = 0, lastSeq = 0, lastDraw = 0;
    let clockOffset = 0, bestRtt = Infinity;
    // The loudest thing seen since the status line was last painted, so
    // "the radio is sending silence" is a claim about a window rather than
    // about whichever single block happened to land on the paint.
    let peakWindow = 0, saidWhy = null;
    const fresh = () => ({ lat: [], drops: 0, bytes: 0,
                           t0: performance.now(), played: 0 });
    let st = fresh();

    function prebufBlocks(blockMs) {
      const ms = Math.max(40, Number(conf().buffer_ms) || 120);
      return Math.max(2, Math.round(ms / (blockMs || 20)));
    }

    async function pull() {
      if (!running) return;
      const t0 = performance.now();
      let r;
      try {
        inflight = HD.cardRaw(ENGINE, `/api/pcm?since=${lastSeq}&${params()}`);
        r = await inflight;
        if (!r.ok) throw new Error(`audio request failed (${r.status})`);
      } catch (e) {
        say(String(e.message || e), true);
        return schedule();
      } finally {
        inflight = null;
      }
      if (!running) return;      // stopped while it was on its way
      const rtt = performance.now() - t0;
      const buf = await r.arrayBuffer();

      const head = +r.headers.get('X-Head-Seq');
      const first = +r.headers.get('X-First-Seq');
      const last = +r.headers.get('X-Last-Seq');
      const rate = +r.headers.get('X-Rate');
      const blockN = +r.headers.get('X-Block-N');
      const blockMs = 1000 * blockN / rate;
      const capturedAt = parseFloat(r.headers.get('X-Captured-At')) * 1000;
      const serverNow = parseFloat(r.headers.get('X-Server-Now')) * 1000;
      const peak = parseFloat(r.headers.get('X-Peak')) || 0;
      peakWindow = Math.max(peakWindow, peak);
      meter(peak);
      if (r.headers.get('X-Status') === 'error') {
        // The input failed to open: say so rather than play silence.
        poll(true);
      }

      // Offset from the exchange with the SMALLEST round trip, the way NTP
      // does it: the error is at most half the trip, so the quickest one is
      // the most trustworthy. Without this the delay reading is biased by
      // whatever the first poll happened to cost.
      if (serverNow && rtt < bestRtt) {
        bestRtt = rtt;
        clockOffset = (Date.now() - rtt / 2) - serverNow;
      }

      const want = prebufBlocks(blockMs);
      // Live audio tracks the head; it never plays catch-up. Anything still
      // in the ring behind us is simply late, and playing it would make the
      // delay permanent rather than momentary.
      if (head - lastSeq > want * 4) {
        if (st.played) st.drops++;
        lastSeq = Math.max(0, head - want);
        nextAt = 0;
        return schedule();
      }

      if (buf.byteLength) {
        st.bytes += buf.byteLength;
        if (lastSeq && first > lastSeq + 1) st.drops++;
        lastSeq = last;

        // One AudioBuffer for everything this poll brought, not one per
        // block: the player's own cost is what decides whether it keeps up
        // once the network is no longer the bottleneck.
        const pcm = new Int16Array(buf);
        const ab = ctx.createBuffer(1, pcm.length, rate);
        const ch = ab.getChannelData(0);
        for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
        const src = ctx.createBufferSource();
        src.buffer = ab;
        src.connect(gain);

        const prebuf = want * blockN / rate;
        if (nextAt < ctx.currentTime + 0.002) {
          if (st.played) st.drops++;          // the player ran dry
          nextAt = ctx.currentTime + prebuf;
        }
        src.start(nextAt);

        const playAtPage = Date.now() + (nextAt - ctx.currentTime) * 1000;
        st.lat.push(playAtPage - (capturedAt + clockOffset));
        if (st.lat.length > 200) st.lat.shift();
        nextAt += pcm.length / rate;
        st.played += pcm.length / blockN;
      }
      paintLink();
      schedule();
    }

    function schedule() {
      if (running) timer = setTimeout(pull, 40);
    }

    function meter(peak) {
      const pct = Math.min(100, Math.round(Math.sqrt(peak) * 100));
      $('meter').firstElementChild.style.width = pct + '%';
      $('meter').classList.toggle('hot', peak > 0.95);
    }

    function paintLink() {
      if (performance.now() - lastDraw < 500) return;
      lastDraw = performance.now();
      const secs = (performance.now() - st.t0) / 1000;
      const m = med(st.lat);
      $('lat').textContent = m === null ? '—' : `${Math.round(m)} ms`;
      $('drops').textContent = st.drops;
      $('kbps').textContent = secs > 1
        ? `${Math.round(st.bytes * 8 / secs / 1000)} kbit/s` : '—';
      $('link').classList.toggle('bad', st.drops > 0);
      sayWhy();
      peakWindow = 0;
    }

    /* Why there is no sound, when there is no sound.
     *
     * The card printed "Listening." whether or not a single sample reached
     * the speakers, which is the one thing the operator actually cares
     * about. It was reported as "the button works but I get no audio" -
     * and the card went on insisting it was listening while the radio, the
     * card and the browser were all working perfectly. It knows enough to
     * say which of these it is, so it should.
     *
     * Nothing here is a failure: every one of them is a thing the operator
     * can put right in a second once they are told which it is.
     */
    function why() {
      // The browser suspends a context when the tab is backgrounded, and on
      // some it needs a fresh gesture to come back.
      if (ctx && ctx.state !== 'running') {
        return 'the browser has paused audio — press Listen again';
      }
      // Stored per browser, so a refresh does not reset it, which is what
      // makes a zeroed slider survive every attempt to fix it by reloading.
      if (gain && gain.gain.value === 0) return 'the volume here is at zero';
      // Arriving, and silent. The radio is muted or on a dead band, or the
      // wrong input is selected in this tile's settings.
      if (st && st.played > 0 && peakWindow === 0) {
        return 'the audio arriving is silent — check the radio and this '
             + "tile's input";
      }
      return '';
    }

    function sayWhy() {
      const reason = why();
      if (reason === saidWhy) return;          // do not repaint every 500 ms
      saidWhy = reason;
      say(reason ? `Listening, but ${reason}.` : 'Listening.', !!reason);
    }

    function say(text, warn) {
      $('status').textContent = text;
      $('status').className = 'lnote' + (warn ? ' warn' : '');
    }

    async function start() {
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      if (!gain) {
        gain = ctx.createGain();
        panner = route(ctx, gain, $('ears').value, null);
        setVolume();
      }
      await ctx.resume();
      st = fresh();
      nextAt = 0; bestRtt = Infinity; clockOffset = 0;
      peakWindow = 0; saidWhy = null;          // say it again for this run
      // Start at the live edge. Asking for everything the ring holds is
      // right for a waterfall and wrong for audio: it hands over seconds of
      // backlog and every block of it is permanent delay.
      try {
        lastSeq = (await HD.card(ENGINE, `/api/info?${params()}`)).seq;
      } catch (e) { lastSeq = 0; }
      running = true;
      $('go').hidden = true; $('stop').hidden = false; $('link').hidden = false;
      say('Listening.');
      pull();
    }

    async function stop(quiet) {
      running = false;
      clearTimeout(timer);
      $('go').hidden = false; $('stop').hidden = true; $('link').hidden = true;
      meter(0);
      if (!quiet) say('Stopped.');
      if (inflight) {
        try { await inflight; } catch (e) { /* it is the stop that matters */ }
      }
      // Tell the engine now rather than letting the reaper notice in 20 s:
      // the reason to press Stop is usually to give the sound card to
      // another program, and "in twenty seconds" is not that.
      // `who`, not the tile: this releases *this page's* listening and
      // leaves any other browser on the same tile playing.
      try {
        await HD.card(ENGINE, `/api/stop?who=${WHO}`, {});
      } catch (e) { /* gone */ }
    }

    function setVolume() {
      const v = +$('vol').value;
      $('volnum').textContent = v;
      // Perceived loudness is nearer the square of the slider than the
      // slider, so a linear control spends its top third doing nothing.
      if (gain) gain.gain.value = (v / 100) ** 2;
      try { localStorage.setItem(key('vol', uid), v); } catch (e) { /* private */ }
    }

    $('go').onclick = start;
    $('stop').onclick = () => stop();
    $('vol').oninput = setVolume;
    // Applies at once, mid-listen: the point is to hear which radio moved.
    $('ears').onchange = () => {
      const choice = $('ears').value;
      try { localStorage.setItem(key('ears', uid), choice); } catch (e) { /* private */ }
      if (ctx && gain) panner = route(ctx, gain, choice, panner);
    };

    /* Audio is heard, not seen, so being hidden is not a reason to stop.
     *
     * This used to `stop()` the moment the tile went to the background, on
     * the invariant-5b reasoning that a hidden card holding the radio's
     * sound card has nobody listening. That reasoning is right for the
     * spectrum and the decoder - nobody is reading a waterfall they cannot
     * see - and backwards for this one: listening while looking at
     * something else is the entire point of it.
     *
     * The phone shell is where it showed, because there every card except
     * the one on screen is hidden: press Listen, go to another card to
     * change something, and the audio stopped - which reads as the card
     * failing rather than as a rule firing.
     *
     * Playing is an explicit state: the operator pressed a button, and
     * Stop is the way to end it - which is what hands the sound card to
     * another program. Nothing is held while not playing (`look()` reports
     * without opening), so the device is not kept by a tile merely
     * existing. `pagehide` still stops, because a page that is going away
     * cannot press anything.
     */
    window.addEventListener('pagehide', () => { if (running) stop(true); });

    try {
      const v = localStorage.getItem(key('vol', uid))
        ?? localStorage.getItem(OLD_VOL_KEY);
      if (v !== null) $('vol').value = v;
      const ears = localStorage.getItem(key('ears', uid));
      if (ears in ROUTES) $('ears').value = ears;
    } catch (e) { /* private window */ }
    setVolume();

    // Status while not listening: whether the input opened at all, and what
    // to do if it did not. Audio cannot start without a click, so the card
    // has to be honest about sitting there waiting for one.
    let pollTimer = null;
    async function poll(once) {
      clearTimeout(pollTimer);
      try {
        const i = await HD.card(ENGINE, `/api/info?${params()}`);
        if (i.status === 'error') {
          say(`${i.error}${i.hint ? ' — ' + i.hint : ''}`, true);
        } else if (!running) {
          const what = i.device === 'mock' ? 'a test tone'
            : (i.device || 'the default input');
          if (i.status === 'idle') {
            // Idle means the input is not open, which is the point: a card
            // nobody is listening to must not be holding the sound card.
            say(`Press Listen to hear ${what}.`);
          } else if (i.status === 'running') {
            say(`Ready — ${what} at ${i.rate} Hz. Press Listen.`);
          } else {
            say(`${i.status}…`);
          }
        }
      } catch (e) { /* host restarting */ }
      if (!once) pollTimer = setTimeout(poll, running ? 5000 : 2000);
    }
    poll();

    return { start, stop, poll: () => poll(true), get running() { return running; } };
  }

  window.HDListen = { mount, WHO, route, ROUTES, key };
})();
