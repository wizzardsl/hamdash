"""RTTY decoding, as pure signal processing.

The same shape as `cw.py`, for the same reason: a decoder prints letters
whether it is right or wrong, so this is a module that takes samples and
returns text, and `tests/test_rtty.py` drives it with synthesised FSK whose
content is known exactly. No audio device and no Flask here.

Amateur RTTY: 45.45 baud, 170 Hz shift, ITA2 with LTRS/FIGS shifts, one
start bit, five data bits sent least significant first, 1.5 stop bits. Mark
is the idle state and the stop bit; space is the start bit.

How it works, and why each part is the way it is:

* **The tone pair is found, not configured**, and it is the pair that is
  *keying*, not the loudest. FSK is two tones taking turns: when mark is up
  space is down, so at every bit edge one rises as the other falls. The
  pair is scored by how anti-correlated their changes in level are over the
  last few seconds. A carrier is loud and steady and does not move; noise
  correlates with nothing; an SSB voice raises both bins together, which is
  correlation of the wrong sign. Among the pairs that do take turns, the
  strongest wins.

* **Drift is followed, a different signal is not.** A best pair a few bins
  from the current one is the same station drifting, and the demodulator is
  retuned in place with its bit timing intact. A pair far away has to be
  clearly better before the decoder will move, the same margin rule as the
  CW tracker - and moving there replays the audio ring through the new
  tones, so the start of that over is not the price of finding it.

* **The raw audio is replayed at lock.** The pair can only be found once
  the keying has been going for a moment, which is by definition after the
  first characters. A few seconds of audio are kept and demodulated again
  the moment a pair is chosen, so the opening of a transmission is read.

* **Mixer, matched filter, normalised discriminator.** Each tone is mixed
  to zero and integrated over one bit (the matched filter for a bit of
  tone in white noise); `(mark - space) / (mark + space)` is the decision,
  which is the same at any level - a USB codec and a mixer left at -40 dB
  read the same.

* **The UART slides, it does not jump.** A start bit is a mark-to-space
  edge followed by five bits and a stop bit that is mark. When the stop bit
  is wrong the edge was not a start bit - usually a data transition, because
  the audio started mid-character - and the search resumes just after that
  edge rather than after the whole failed character. That is what gets a
  decoder joining continuous text into step within a character or two.

* **A character has to look like a signal to be printed.** Once locked
  the UART reads the noise between overs too, and noise makes edges that
  pass the framing checks now and then. So each character is also judged on
  how decisively its seven sample points went one way or the other - and,
  where that alone cannot tell (a weak signal scores much like noise), on
  whether its neighbours did. Noise sits near the middle and so do its
  neighbours; a real character is next to other real ones.
"""
from __future__ import annotations

import math

import numpy as np

# The same passband the CW decoder uses: an SSB receiver passes 300-2700 Hz
# and the operator tunes wherever they like inside it. Both tones of the
# pair have to be inside it.
TONE_LOW_HZ = 250.0
TONE_HIGH_HZ = 2700.0

BAUD = 45.45
SHIFT_HZ = 170.0
STOP_BITS = 1.5
DATA_BITS = 5

# Decimated samples per bit. Sixteen leaves the edge timing well inside a
# bit at no real cost: at 45.45 baud that is 727 samples a second.
SAMPLES_PER_BIT = 16

# The tracker's spectrum: a frame about one bit long, so a frame mostly
# holds one tone or the other, zero-padded four times so 170 Hz is a whole
# number of bins to within a few hertz.
TRACK_FRAME_SECONDS = 0.021
TRACK_PAD = 4
# About three seconds of levels: long enough to hold plenty of both tones,
# short enough to follow a drift. Re-decided every 16 frames (~170 ms).
TRACK_SECONDS = 3.0
TRACK_EVERY = 16
TRACK_SETTLE_SECONDS = 0.8

# How anti-correlated a pair's changes in level must be to count as keying.
# Measured over three seconds: the best of the ~200 pairs in a passband of
# noise reaches -0.22; a clean signal sits at -0.78 and one at -6 dB SNR in
# 2.5 kHz at -0.58. Around -10 dB it fades into the noise, which is about
# where the characters stop being readable anyway.
LOCK_CORR = -0.4
# Both bins of a keying pair go up and down by at least this much over the
# window. Noise alone swings about 14 dB in one bin; a carrier at any useful
# level swings far less, which is what keeps it out.
MIN_SWING_DB = 6.0
# Frames apart that a change in level is measured over: one frame length,
# since the hop is half a frame.
CHANGE_LAG = 2
# The widest span a bin's level is judged over, as in `cw.MAX_RANGE_DB`.
MAX_RANGE_DB = 40.0
# How much stronger a far pair has to be before the decoder leaves the one
# it has. The same idea as `cw.TRACK_MARGIN_DB`.
MOVE_MARGIN_DB = 6.0
# Within this many hertz the best pair is the current station drifting:
# retuned in place, nothing reset.
DRIFT_HZ = 40.0

# Audio kept for replay at lock: the tracker's window plus a little.
RING_SECONDS = 4.0

# How decisively a character's seven sample points went one way or the
# other: the mean of |discriminator| at them, 0 undecided and 1 a clean
# tone. Measured on four minutes of noise forced through a locked decoder
# (1186 candidates that passed the start and stop bit checks): median 0.30,
# worst 0.57. A signal at -6 dB SNR in 2.5 kHz has a median of 0.57 - the
# same number - so one character on its own cannot be judged there.
# Its neighbours can: noise's three-character mean never passed 0.45, and
# a real character is next to other real ones. So a character prints if it
# is decisive alone, or if it is not hopeless and its neighbourhood is
# decisive, at the cost of holding each one until the next has arrived.
SURE_QUALITY = 0.6
SURE_START = 0.5
# The start bit's level against the median of the character's, -6 dB. Only
# a character that clears it can be sure on its own.
START_LEVEL = 10 ** (-6 / 20)
NEIGHBOUR_QUALITY = 0.5
MIN_QUALITY = 0.4
# Characters further apart than this, in bits, are not neighbours: two
# characters back to back are 7.5 bits apart.
NEIGHBOUR_BITS = 16.0

# ITA2, five bits, first-sent bit least significant. Letters are common to
# every variant; figures are the US TTY set, which is what North American
# amateur RTTY uses (fldigi and MMTTY default to it too). BELL prints
# nothing; CR prints nothing because LF does the job and most stations send
# both.
LTRS, FIGS, CR, LF, SPACE, NUL = 0x1F, 0x1B, 0x08, 0x02, 0x04, 0x00
LETTERS = {
    0x01: "E", 0x03: "A", 0x05: "S", 0x06: "I", 0x07: "U", 0x09: "D",
    0x0A: "R", 0x0B: "J", 0x0C: "N", 0x0D: "F", 0x0E: "C", 0x0F: "K",
    0x10: "T", 0x11: "Z", 0x12: "L", 0x13: "W", 0x14: "H", 0x15: "Y",
    0x16: "P", 0x17: "Q", 0x18: "O", 0x19: "B", 0x1A: "G", 0x1C: "M",
    0x1D: "X", 0x1E: "V",
}
FIGURES = {
    0x01: "3", 0x03: "-", 0x05: "", 0x06: "8", 0x07: "7", 0x09: "$",
    0x0A: "4", 0x0B: "'", 0x0C: ",", 0x0D: "!", 0x0E: ":", 0x0F: "(",
    0x10: "5", 0x11: '"', 0x12: ")", 0x13: "2", 0x14: "#", 0x15: "6",
    0x16: "0", 0x17: "1", 0x18: "9", 0x19: "?", 0x1A: "&", 0x1C: ".",
    0x1D: "/", 0x1E: ";",
}
# The other way round, for generating audio. Built from the tables above so
# they cannot disagree. BELL's empty string is not something to encode.
ENC_LETTERS = {ch: code for code, ch in LETTERS.items()}
ENC_FIGURES = {ch: code for code, ch in FIGURES.items() if ch}


def _pow2(x: float) -> int:
    return 1 << max(4, int(round(math.log2(max(16.0, x)))))


class RttyDecoder:
    """Audio in, text out. Call `feed()` with whatever samples arrived.

    `reverse` swaps which tone is mark: off is right for a receiver in USB
    (mark is the higher audio tone), on for LSB. `usos` is unshift on space:
    a space received in figures returns to letters, which is what nearly
    every transmitter assumes.
    """

    def __init__(self, samplerate: int = 48000, reverse: bool = False,
                 usos: bool = True, baud: float = BAUD,
                 shift_hz: float = SHIFT_HZ,
                 stop_bits: float = STOP_BITS) -> None:
        self.samplerate = int(samplerate)
        self.reverse = bool(reverse)
        self.usos = bool(usos)
        self.baud = float(baud)
        self.shift_hz = float(shift_hz)
        self.stop_bits = max(1.0, float(stop_bits))

        # -- tracker -------------------------------------------------------
        self._tn = _pow2(self.samplerate * TRACK_FRAME_SECONDS)
        self._thop = self._tn // 2
        self._nfft = self._tn * TRACK_PAD
        self._twin = np.hanning(self._tn).astype(np.float32)
        self._hz_bin = self.samplerate / self._nfft
        self._lo = max(1, int(math.ceil(TONE_LOW_HZ / self._hz_bin)))
        self._hi = min(self._nfft // 2, int(TONE_HIGH_HZ / self._hz_bin))
        self._shift_bins = int(round(self.shift_hz / self._hz_bin))
        frames = max(32, int(TRACK_SECONDS * self.samplerate / self._thop))
        self._ring = np.full((frames, self._hi - self._lo), -120.0,
                             dtype=np.float32)
        self._ring_n = 0
        self._settle = int(TRACK_SETTLE_SECONDS * self.samplerate
                           / self._thop)
        self._tbuf = np.zeros(0, dtype=np.float32)
        self._locked = False
        self._keyed = False
        self.low_hz = 0.0               # the pair, lower tone first
        self.high_hz = 0.0

        # -- raw audio, for replay ----------------------------------------
        self._audio = np.zeros(0, dtype=np.float32)
        self._audio_at = 0              # global index of _audio[0]
        self._keep = int(RING_SECONDS * self.samplerate)

        # -- demodulator ---------------------------------------------------
        self._dec = max(1, int(round(self.samplerate
                                     / (self.baud * SAMPLES_PER_BIT))))
        self.spb = self.samplerate / (self.baud * self._dec)
        self._taps = max(2, int(round(self.spb)))
        self._demod_reset()

        self.snr_db = 0.0
        self._quality = 0.0

    # -- readouts ---------------------------------------------------------
    @property
    def mark_hz(self) -> float:
        return self.low_hz if self.reverse else self.high_hz

    @property
    def space_hz(self) -> float:
        return self.high_hz if self.reverse else self.low_hz

    @property
    def tone_hz(self) -> float:
        """The centre of the pair, which is what a tuning indicator shows."""
        return round((self.low_hz + self.high_hz) / 2, 1) if self._locked \
            else 0.0

    @property
    def hearing(self) -> bool:
        return self._locked and self._keyed

    # -- the pipeline ------------------------------------------------------
    def feed(self, samples) -> str:
        block = np.asarray(samples, dtype=np.float32).ravel()
        if not block.size:
            return ""
        self._audio = np.concatenate((self._audio, block))
        self._tbuf = np.concatenate((self._tbuf, block))
        while self._tbuf.size >= self._tn:
            frame = self._tbuf[:self._tn] * self._twin
            self._tbuf = self._tbuf[self._thop:]
            self._track_frame(frame)
        out = self._demodulate() if self._locked else ""
        if self._audio.size > self._keep:
            drop = self._audio.size - self._keep
            self._audio = self._audio[drop:]
            self._audio_at += drop
        return out

    def flush(self) -> str:
        """The character held back for its neighbour, if the audio ended.

        A character is complete at its stop bit, but each one waits for the
        next before it is judged, so the last one of a recording would
        otherwise never be printed. Live audio never needs this: the time
        limit in `_judge` lets it go once no neighbour is coming.
        """
        return self._judge(math.inf) if self._locked else ""

    # -- finding the pair -------------------------------------------------
    def _track_frame(self, frame: np.ndarray) -> None:
        spec = np.abs(np.fft.rfft(frame, self._nfft))[self._lo:self._hi]
        db = 20.0 * np.log10(np.maximum(spec / self._tn, 1e-6))
        rows = self._ring.shape[0]
        self._ring[self._ring_n % rows] = db
        self._ring_n += 1
        if self._ring_n < self._settle or self._ring_n % TRACK_EVERY:
            return
        filled = min(self._ring_n, rows)
        hist = (self._ring[:filled] if self._ring_n <= rows else
                np.roll(self._ring, -(self._ring_n % rows), axis=0))
        self._choose(hist)

    def _choose(self, hist: np.ndarray) -> None:
        corr, strength = pair_scores(hist, self._shift_bins)
        if not corr.size:
            return
        here = None
        if self._locked:
            here = int(round(self.low_hz / self._hz_bin)) - self._lo
            here = min(max(here, 0), corr.size - 1)
            self._keyed = bool(corr[here] <= LOCK_CORR / 2)
        keying = corr <= LOCK_CORR
        if not keying.any():
            if not self._locked:
                self._keyed = False
            return
        best = int(np.argmax(np.where(keying, strength, -np.inf)))
        self._keyed = True
        low, high = self._refine(hist, best)
        if not self._locked:
            self._lock(low, high)
            return
        if abs(low - self.low_hz) <= DRIFT_HZ:
            self._retune(low, high)     # the same station, drifting
            return
        if strength[best] < strength[here] + MOVE_MARGIN_DB \
                and corr[here] <= LOCK_CORR:
            return                      # not clearly better than ours
        self._lock(low, high)

    def _refine(self, hist: np.ndarray, k: int) -> tuple[float, float]:
        """The pair to better than a bin, from the average spectrum.

        Zero padding puts a bin every ~12 Hz and the tracker's pick can be a
        bin off either way; a parabola through the peak of each tone in the
        time-averaged spectrum puts it within a hertz or two, which matters
        because the matched filter is only 45 Hz wide.
        """
        mean = 10.0 * np.log10(np.mean(10.0 ** (hist / 10.0), axis=0)
                               + 1e-12)

        def peak(c: int) -> float:
            lo, hi = max(1, c - 2), min(mean.size - 2, c + 2)
            if lo > hi:
                return float(c)
            c = lo + int(np.argmax(mean[lo:hi + 1]))
            a, b, d = mean[c - 1], mean[c], mean[c + 1]
            den = a - 2 * b + d
            off = 0.5 * (a - d) / den if den < 0 else 0.0
            return c + max(-0.5, min(0.5, off))

        lo_b = peak(k)
        hi_b = peak(k + self._shift_bins)
        low = (lo_b + self._lo) * self._hz_bin
        high = (hi_b + self._lo) * self._hz_bin
        # A real transmitter's shift is 170 give or take a few hertz; a
        # refinement that wandered further than that latched onto something
        # else, so keep the shift and trust the centre.
        if abs((high - low) - self.shift_hz) > 25.0:
            mid = (low + high) / 2
            low, high = mid - self.shift_hz / 2, mid + self.shift_hz / 2
        return low, high

    def _lock(self, low: float, high: float) -> None:
        # Plain floats: the refinement is numpy float32, which the card's
        # JSON reply refuses - the readout broke the whole poll.
        self.low_hz, self.high_hz = float(low), float(high)
        self._locked = True
        # Resetting starts the demodulator at the front of the audio ring,
        # so the characters that went past while the pair was being found
        # are read after all - but not before the last character already
        # printed, or moving to a new station would print the end of the
        # old one's over a second time.
        self._demod_reset()
        self._done = self._d_base = max(self._audio_at, self._printed_to)

    def _retune(self, low: float, high: float) -> None:
        self.low_hz, self.high_hz = float(low), float(high)

    # -- demodulation ------------------------------------------------------
    def _demod_reset(self) -> None:
        self._done = getattr(self, "_audio_at", 0)  # global samples consumed
        self._d_base = self._done       # the audio sample _d's index 0 is
        if not hasattr(self, "_printed_to"):
            self._printed_to = 0        # audio sample the last char ended at
        self._ph = [0.0, 0.0]           # mixer phases, low and high tone
        self._left = [np.zeros(0, np.complex128), np.zeros(0, np.complex128)]
        self._tail = [np.zeros(0, np.complex128), np.zeros(0, np.complex128)]
        self._d = np.zeros(0, dtype=np.float64)     # discriminator
        self._r = np.zeros(0, dtype=np.float64)     # on/off ratio, dB
        self._e = np.zeros(0, dtype=np.float64)     # mark + space level
        self._d_at = 0.0                # decimated index of _d[0]
        self._pos = 1.0                 # where the UART searches from
        self._figs = False
        self._cand: list[tuple[float, float, float, int]] = []
        self._prev: tuple[float, float, float, int] | None = None

    def _demodulate(self) -> str:
        start = self._done - self._audio_at
        x = self._audio[start:]
        if not x.size:
            return ""
        self._done += x.size
        n = np.arange(x.size, dtype=np.float64)
        mags = []
        for i, f in enumerate((self.low_hz, self.high_hz)):
            w = 2 * math.pi * f / self.samplerate
            z = x * np.exp(-1j * (self._ph[i] + w * n))
            self._ph[i] = (self._ph[i] + w * x.size) % (2 * math.pi)
            # First stage: sum and decimate.
            buf = np.concatenate((self._left[i], z))
            whole = buf.size // self._dec * self._dec
            self._left[i] = buf[whole:]
            zd = buf[:whole].reshape(-1, self._dec).sum(axis=1)
            # Second: one bit of integration, the matched filter.
            buf = np.concatenate((self._tail[i], zd))
            if buf.size < self._taps:
                self._tail[i] = buf
                mags.append(np.zeros(0))
                continue
            c = np.concatenate(([0], np.cumsum(buf)))
            y = c[self._taps:] - c[:-self._taps]
            self._tail[i] = buf[-(self._taps - 1):]
            mags.append(np.abs(y))
        low, high = mags
        k = min(low.size, high.size)
        if not k:
            return ""
        low, high = low[:k], high[:k]
        mark, space = (low, high) if self.reverse else (high, low)
        d = (mark - space) / (mark + space + 1e-12)
        r = 20.0 * np.log10((np.maximum(mark, space) + 1e-12)
                            / (np.minimum(mark, space) + 1e-12))
        self._d = np.concatenate((self._d, d))
        self._r = np.concatenate((self._r, r))
        self._e = np.concatenate((self._e, mark + space))
        return self._uart()

    # -- characters --------------------------------------------------------
    def _at(self, x: float) -> float:
        """The discriminator at a fractional decimated index."""
        i = x - self._d_at
        j = int(i)
        f = i - j
        return float(self._d[j] * (1 - f) + self._d[j + 1] * f)

    def _uart(self) -> str:
        out: list[str] = []
        spb = self.spb
        # The extra stop length past one bit - half a bit for amateur RTTY.
        extra = (self.stop_bits - 1.0) * spb
        span = (DATA_BITS + 1.5) * spb + extra + 2
        end = self._d_at + self._d.size - 1
        while True:
            i = max(1, int(math.ceil(self._pos - self._d_at)))
            # The next mark-to-space edge: k is its first sample at or
            # below zero, and nothing before `_pos` is looked at again.
            seg = self._d[i - 1:]
            edges = np.nonzero((seg[:-1] > 0) & (seg[1:] <= 0))[0]
            if not edges.size:
                self._pos = self._d_at + max(1, self._d.size - 1)
                break
            k = i + int(edges[0])
            a, b = self._d[k - 1], self._d[k]
            cross = self._d_at + k - 1 + a / (a - b)
            if cross + span > end:
                self._pos = self._d_at + k     # wait for the rest of it
                break
            # Sample each bit where the matched filter has integrated the
            # whole of it: half a bit after the edge, then a bit apart.
            pts = [cross + (b_ + 0.5) * spb for b_ in range(DATA_BITS + 2)]
            vals = [self._at(p) for p in pts]
            start_ok = vals[0] < 0
            # The whole stop bit, which is longer than a data bit, is mark;
            # and so was the whole of the one before this start bit - the
            # last character's stop, or idle. Any edge has *some* mark
            # before it, so checking one bit's worth tells nothing; it is
            # the extra half bit that a data transition does not have. That
            # is what keeps a decoder that joined continuous text mid-way -
            # RYRY above all - from framing on data edges for several
            # characters at a time. Measured over 40 cut points into one
            # message: once with a one-bit check, never with this.
            stop_ok = vals[-1] > 0 and (
                not extra or self._at(pts[-1] + extra) > 0)
            mark_before = all(
                p < self._d_at + 1 or self._at(p) > 0
                for p in (cross - 0.5 * spb, cross - 0.5 * spb - extra))
            if not (start_ok and stop_ok and mark_before):
                self._pos = self._d_at + k + 1  # slide past this edge
                continue
            quality = sum(abs(v) for v in vals) / len(vals)
            idx = [int(p - self._d_at) for p in pts]
            ratio = float(np.mean(self._r[idx]))
            self._pos = cross + (DATA_BITS + 1.5) * spb + extra
            code = sum(1 << n for n, v in enumerate(vals[1:1 + DATA_BITS])
                       if v > 0)
            # Sure on its own only if the start bit is decisive and is the
            # same signal as the rest. A noise edge a couple of bits before
            # a transmitter comes up on idle mark has its last data bits
            # and the stop bit in clean mark, and scores as a good
            # character - a V in front of the over. Its start bit is noise,
            # well below the bits after it. That is not grounds to *drop*
            # a character, since HF fades one tone under the other all the
            # time, so it only has to be vouched for by its neighbours -
            # which a real one in a fade has, and the thing in front of an
            # over, followed by idle, does not.
            levels = self._e[idx]
            if abs(vals[0]) < SURE_START or \
                    levels[0] < np.median(levels) * START_LEVEL:
                quality = min(quality, SURE_QUALITY - 1e-6)
            self._cand.append((cross, quality, ratio, code))
            out.append(self._judge(end))
        out.append(self._judge(end))
        # Keep about a character of history behind the search point.
        keep_from = int(self._pos - self._d_at - 2 * spb)
        if keep_from > 0:
            self._d = self._d[keep_from:]
            self._r = self._r[keep_from:]
            self._e = self._e[keep_from:]
            self._d_at += keep_from
        return "".join(out)

    def _judge(self, end: float) -> str:
        """Print or drop every candidate whose neighbours are known.

        A candidate is decided once the next one has arrived, or once enough
        time has passed that no neighbour is coming - the end of an over.
        """
        out = []
        near = NEIGHBOUR_BITS * self.spb
        while len(self._cand) > 1 or (
                self._cand and end - self._cand[0][0] > near + 8 * self.spb):
            at, q, ratio, code = self._cand[0]
            nxt = self._cand[1] if len(self._cand) > 1 else None
            hood = []
            if self._prev is not None and at - self._prev[0] <= near:
                hood.append(self._prev[1])
            if nxt is not None and nxt[0] - at <= near:
                hood.append(nxt[1])
            # Vouched for means by a neighbour that is not itself noise:
            # a noise candidate next door is a neighbour too, and averaging
            # with it let the thing in front of an over through.
            ok = q >= SURE_QUALITY or (
                q >= MIN_QUALITY and hood
                and max(hood) >= NEIGHBOUR_QUALITY
                and (q + sum(hood)) / (1 + len(hood)) >= NEIGHBOUR_QUALITY)
            self._prev = self._cand.pop(0)
            if not ok:
                continue
            self._quality = q
            self._printed_to = int(self._d_base
                                   + (at + (DATA_BITS + 2.5) * self.spb)
                                   * self._dec)
            self.snr_db = round(float(0.8 * self.snr_db + 0.2 * ratio), 1)
            out.append(self._char(code))
        return "".join(out)

    def _char(self, code: int) -> str:
        if code == LTRS:
            self._figs = False
            return ""
        if code == FIGS:
            self._figs = True
            return ""
        if code == SPACE:
            if self.usos:
                self._figs = False
            return " "
        if code == LF:
            return "\n"
        if code in (CR, NUL):
            return ""
        return (FIGURES if self._figs else LETTERS).get(code, "")


def pair_scores(hist: np.ndarray, shift_bins: int
                ) -> tuple[np.ndarray, np.ndarray]:
    """For every pair of bins `shift_bins` apart: (correlation, strength).

    `hist` is frames x bins of levels in dB. The correlation is of the two
    bins' *changes* in level over one frame: near -1 for two tones taking
    turns, since every bit edge is one going up as the other comes down;
    near 0 for noise; positive for something wideband like a voice. Levels
    work too, once each bin is clamped to `MAX_RANGE_DB` under its peak,
    but changes separate a little better - measured at -6 dB SNR, -0.58
    against -0.49, with noise at about -0.2 either way.

    A pair whose bins barely move is not keying however the correlation
    comes out: a steady carrier's level is wobbled only by other signals
    leaking into it, and correlation does not care how small a wobble is.
    Those get a correlation of 0.

    Strength is the mean of the louder of the two, which is the level of
    whichever tone is up. Public because the test that shows a
    loudest-pair tracker failing uses the strength half on its own.
    """
    if hist.shape[1] <= shift_bins or hist.shape[0] < 4 + CHANGE_LAG:
        return np.zeros(0), np.zeros(0)
    # No bin is allowed a floor more than MAX_RANGE_DB under its own peak.
    # Real audio never has one, and digital silence before a synthetic
    # signal puts it 100 dB down - so the step into the signal is a jump
    # of 100 dB in *both* bins, one common change that outweighed every
    # keying edge after it and kept a short over from locking at all.
    h = hist.astype(np.float64)
    h = np.maximum(h, h.max(axis=0) - MAX_RANGE_DB)
    a = h[:, :-shift_bins]
    b = h[:, shift_bins:]
    strength = np.mean(np.maximum(a, b), axis=0)
    swing = np.minimum(np.percentile(a, 90, axis=0)
                       - np.percentile(a, 10, axis=0),
                       np.percentile(b, 90, axis=0)
                       - np.percentile(b, 10, axis=0))
    # Changes over one whole frame (two hops). Frames overlap by half, so
    # a one-hop change catches half of an edge and the frames straddling it
    # blur the rest: measured, a clean signal correlated at -0.45 that way
    # and at -0.78 over two hops, with noise no better than -0.22 either way.
    da = a[CHANGE_LAG:] - a[:-CHANGE_LAG]
    db = b[CHANGE_LAG:] - b[:-CHANGE_LAG]
    da -= da.mean(axis=0)
    db -= db.mean(axis=0)
    den = np.sqrt((da * da).sum(axis=0) * (db * db).sum(axis=0))
    corr = np.where(den > 1e-9, (da * db).sum(axis=0)
                    / np.maximum(den, 1e-9), 0.0)
    corr = np.where(swing >= MIN_SWING_DB, corr, 0.0)
    return corr, strength


# ------------------------------------------------------------ generating
def encode(text: str, usos: bool = True) -> list[int]:
    """ITA2 codes for a string, with the shifts it needs.

    `usos` says what the *transmitter* assumes about the receiver: with it,
    a figure after a space gets a fresh FIGS, because the space will have
    unshifted the far end. Characters ITA2 has no code for are dropped, the
    way a teleprinter keyboard simply has no key for them.
    """
    out = [LTRS]
    figs = False
    for ch in text.upper():
        if ch == " ":
            out.append(SPACE)
            if usos:
                figs = False
            continue
        if ch == "\n":
            out.extend((CR, LF))
            continue
        if ch in ENC_LETTERS:
            if figs:
                out.append(LTRS)
                figs = False
            out.append(ENC_LETTERS[ch])
        elif ch in ENC_FIGURES:
            if not figs:
                out.append(FIGS)
                figs = True
            out.append(ENC_FIGURES[ch])
    return out


def rtty_audio(text: str = "", mark_hz: float = 2125.0 + SHIFT_HZ,
               shift_hz: float = SHIFT_HZ, baud: float = BAUD,
               reverse: bool = False, samplerate: int = 48000,
               amplitude: float = 0.5, lead_seconds: float = 0.5,
               idle_seconds: float = 0.4, tail_seconds: float = 0.4,
               usos: bool = True, codes: list[int] | None = None,
               drift_hz_per_s: float = 0.0,
               stop_bits: float = STOP_BITS) -> np.ndarray:
    """Synthesised RTTY, for the mock input and for the tests.

    Normal polarity puts mark on the higher audio tone, which is what a
    receiver in USB hears; `reverse` puts it on the lower, as in LSB. The
    phase is continuous across bit edges - an FSK transmitter's VFO does
    not jump - so there are no clicks for the decoder to find edges in.
    Silence, then `idle_seconds` of mark (a transmitter comes up on mark),
    then the characters, then mark again.
    """
    space_hz = mark_hz - shift_hz
    if reverse:
        mark_hz, space_hz = space_hz, mark_hz
    bit = samplerate / baud
    bits: list[tuple[bool, float]] = [(True, idle_seconds * baud)]
    for code in (codes if codes is not None else encode(text, usos=usos)):
        bits.append((False, 1.0))
        for n in range(DATA_BITS):
            bits.append((bool(code >> n & 1), 1.0))
        bits.append((True, stop_bits))
    bits.append((True, tail_seconds * baud))

    # Bit edges on the exact fractional sample grid, so a long message does
    # not drift against the receiver's idea of 45.45 baud.
    marks = []
    t = 0.0
    for is_mark, length in bits:
        n0, t = int(round(t)), t + length * bit
        marks.append(np.full(int(round(t)) - n0, is_mark))
    keyed = np.concatenate(marks)
    freq = np.where(keyed, mark_hz, space_hz).astype(np.float64)
    if drift_hz_per_s:
        freq += drift_hz_per_s * np.arange(freq.size) / samplerate
    phase = 2 * np.pi * np.cumsum(freq) / samplerate
    tone = (amplitude * np.sin(phase)).astype(np.float32)
    lead = np.zeros(int(samplerate * lead_seconds), dtype=np.float32)
    return np.concatenate((lead, tone, np.zeros(int(samplerate * 0.2),
                                                dtype=np.float32)))
