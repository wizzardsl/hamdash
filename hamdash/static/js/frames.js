/* Telling a card's iframe whether anybody is looking at it.
 *
 * Invariant 5b: a background tab stays loaded so that switching back is
 * instant, which means a hidden spectrum, decoder or rx_audio would hold
 * the sound card for ever. A CSS-hidden iframe gets no `visibilitychange`
 * of its own, so the page that owns it has to say.
 *
 * It lives here rather than on `Board` because there are two shells now -
 * the board and the phone - and they ask the same question. The recurring
 * lesson in this tree is that two copies of an answer become two different
 * answers, and this one is only noticed when a radio's audio is locked
 * against another program.
 */
(function (global) {
  function tellVisible(holder, visible) {
    if (holder._visible === visible) return;   // don't shout every reflow
    holder._visible = visible;
    const frame = holder.querySelector('iframe');
    if (!frame) return;
    const tell = () => {
      try {
        frame.contentWindow.postMessage(
          { __hamdash: 'visible', visible }, location.origin);
      } catch { /* not loaded yet, or not ours */ }
    };
    tell();
    // A card mounted hidden has not loaded when it is first told, and it
    // would then poll merrily behind a tab nobody is looking at.
    if (!frame._hdVisibleWired) {
      frame._hdVisibleWired = true;
      frame.addEventListener('load', () => {
        try {
          frame.contentWindow.postMessage(
            { __hamdash: 'visible', visible: !!holder._visible },
            location.origin);
        } catch { /* ignore */ }
      });
    }
  }

  global.HDFrames = { tellVisible };
})(window);
