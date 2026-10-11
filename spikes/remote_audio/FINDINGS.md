# Remote audio: can it be done by polling?

**Yes.** The transport sustains the radio's receive audio to a browser with
no new dependency, no new open port, no held server thread and no loss.
Measured over 60 s: **50.0 blocks/s delivered against 50.0 produced, 0
dropped, arrival lag p50 40 ms / p95 54 ms, 192 kbit/s at 12 kHz.**

That is the whole question this spike existed to answer, and the answer
means the expensive options are not needed. No WebRTC, so no `aiortc` and
no bundled ffmpeg codecs — which matters more than it looks, because a
frozen card runs inside the hamdash binary (invariant 6), so a card's
dependency is every user's dependency and every build's size.

## It did not work at first, and the reasons were all elsewhere

The audio design was fine from the start. Everything that was wrong was in
the card path that **every** card already uses, and each cost about one
Windows timer tick (15.6 ms) — the same tick that already has an entry in
`CLAUDE.md` for the spots tie-break.

| | before | after |
|---|---|---|
| the card proxy built a fresh `requests.Session` per call | 15.6 ms | 1.1 ms |
| it returned a generator, which waitress pays a tick for | 15.1 ms | 1.1 ms |
| a **failed** `card.settings()` was retried every request | 30.8 ms | 2.5 ms |
| cards served on werkzeug's development server | 7.7 / 26.4 p95 | 1.0 / 1.5 p95 |
| **one card request, end to end** | **34.5 / 43.7 p95** | **2.3 / 3.5 p95** |

None of that is specific to audio. Every polled card — spectrum, decoder,
psk_reporter, spots — was paying it, and the p95 is the half that shows,
because a polled card turns jitter into visible stutter. Those four fixes
are in the app and guarded by `tests/test_card_latency.py`; the spike
itself is just what found them.

The failing-settings one is worth singling out: a card asked about a uid
the host will not answer for — a page opened outside the board, a tile
that has gone — paid a full round trip to the host on *every* request for
the life of the process, and printed a log line each time. Thousands of
lines in a minute. Nothing reports it, the card simply answers slowly for
ever. It is the same bug the callsign lookup already had and already
fixed, in a second place.

## Three things about playing audio in a browser

**Start at the live edge, not at `since=0`.** "Everything since N" is right
for a waterfall, which wants the history. A page that asks audio for it is
handed the whole ring, plays it end to end and is permanently that far
behind the radio — measured at 8.2 s, with no dropouts and nothing
reporting an error.

**Track the head; never play catch-up.** Rescheduling what is queued is not
enough, because the stale blocks are still in the ring and still get
fetched. Latency sat pinned at 570 ms, perfectly stable, looking healthy.
Throwing the backlog away and rejoining live took it to 209 ms.

**The player's own cost decides whether it keeps up** once the network
stops being the bottleneck. One `AudioBuffer` per poll rather than one per
20 ms block, and a scope that redraws at 10 Hz rather than per poll.

## What is not answered

- **Transmit.** `getUserMedia` needs a secure context, so a mic will not
  work over plain `http://10.0.1.212` in any browser. That forces HTTPS
  with a self-signed certificate, or a tailnet with real ones. Receive has
  no such restriction, which is why it is the sensible first half.
- **Real network jitter.** Everything here is one machine. A LAN adds
  single-digit milliseconds and Wi-Fi adds bursts; the jitter buffer is
  the knob and it was not tuned against a real link.
- **Device contention.** If the spectrum card already holds the radio's
  input, two cards want one capture device, which PortAudio on Windows
  will not reliably give. The existing `_wanted` reaping handles letting
  go, not sharing. One capture fanned out to several consumers is probably
  the answer and is not built.
- **Decimation is a box average**, which is a crude low-pass. Good enough
  to answer the transport question; a real one needs a proper anti-alias
  filter or the hiss above the cut folds back into the passband.
- **Authentication.** There is none, here or in the host. Remote control
  of a transmitter makes that non-optional rather than a nicety.
- **The browser harness is a hidden webview pane**, where `setTimeout(0)`
  costs 4.6 ms and the page cannot tell it is hidden. The in-browser
  figures here are pessimistic; the 60 s transport measurement at the top
  has no browser in it and is the one to trust.

## Running it

It is not loaded from `spikes/`. Copy it into the cards folder and press
**Cards → Rescan**. It defaults to a mock source carrying a click on every
whole second, which is what makes latency measurable with no radio
attached — set the input to `rig` to follow the active radio's audio in.
