"""PSK31 decoding, as pure signal processing.

The same shape as `cw.py` and `rtty.py`, for the same reason: a decoder
prints letters whether it is right or wrong, so this takes samples and
returns text, and `tests/test_psk.py` drives it with synthesised BPSK31
whose content is known exactly. No audio device and no Flask here.

BPSK31: 31.25 baud, one carrier whose phase is reversed for a 0 and left
alone for a 1, with the amplitude shaped to a cosine through each reversal
so the signal is only ~60 Hz wide. Text is Varicode (G3PLX): each
character a run of bits that starts and ends with 1 and never holds two 0s
in a row, so "00" is what separates characters. Idle is a run of 0s -
continuous reversals - and the end of an over is an unmodulated carrier.

How it works, and why each part is the way it is:

* **The carrier is found by squaring.** BPSK has no carrier to find: its
  spectrum is two sidebands with nothing in the middle. Squared, the
  keying disappears (+1 and -1 square to the same thing) and what is left
  is a pure line at twice the carrier. A plain carrier squares to a line
  too - but it also *has* a line before squaring, which BPSK does not, so
  a candidate with a strong unsquared line at its centre is a carrier and
  not a PSK signal. Noise has neither. The squared line sits on 0.5 Hz
  bins over two seconds, so the frequency is found to a quarter of a hertz.

* **Differential detection, not a phase lock.** Each symbol is compared
  with the one before: the same phase is a 1, the opposite a 0. That needs
  the frequency right to within a few hertz, not the phase - a frequency
  error turns the phase a little each symbol, and a little is fine. What
  rotation there is is measured from the decisions themselves and fed back
  (AFC), so a drifting signal is followed without losing a character.

* **The bit clock comes from the envelope.** Every reversal takes the
  amplitude through zero halfway between symbols, so the envelope sampled
  sixteen times a symbol is largest at the symbol centres. That phase is
  averaged over many symbols and only moved with a margin, so one noisy
  symbol does not make the clock jump and print a character twice.

* **The raw audio is replayed at lock**, as the RTTY decoder does: the
  carrier can only be found once the signal has been going a moment, so a
  few seconds are kept and demodulated again from the start of the ring.

* **A character has to look like a signal to be printed.** Locked onto
  noise, the detector still makes decisions, and Varicode will make
  letters of anything. A real symbol's phase change is decisively 0 or
  180 degrees; noise's is anywhere. So each character is printed only if
  its symbols, taken together, were decisive.
"""
from __future__ import annotations

import math

import numpy as np

BAUD = 31.25
TONE_LOW_HZ = 250.0
TONE_HIGH_HZ = 2700.0
SPS = 16                    # baseband samples per symbol
BB_RATE = BAUD * SPS        # 500 Hz

SEARCH_SECONDS = 3.0        # of audio squared for the carrier search
SEARCH_EVERY = 0.5          # seconds between looks
LOCK_DB = 9.0               # squared line over its neighbourhood, to try it
TRIAL_SECONDS = 3.0         # of audio demodulated to judge a candidate
# How decisive a candidate's symbols must be. Noise averages 2/pi (0.64)
# with a spread of about 0.3 a symbol, so over ~90 symbols 0.80 is five
# standard deviations - and it needs to be: a search tries a few
# candidates, three offsets and sixteen clock phases twice a second, and
# at 0.78 over two seconds noise cleared the bar often enough to print
# "e e nef" from a quiet band.
TRIAL_QUALITY = 0.80
TRIAL_DIP = 1.08            # envelope at the best clock phase over the worst
CANDIDATES = 4              # squared lines tried, strongest first
CARRIER_DB = 6.0            # unsquared centre over the sidebands: a carrier
MOVE_QUALITY = 0.08         # a different signal's symbols, this much surer
DRIFT_HZ = 8.0              # nearer than this is the same signal drifting
RING_SECONDS = 8.0          # replayed at lock
AFC_GAIN = 0.06

DECISIVE = 0.80             # mean |cos| of a character's phase changes
TIMING_ALPHA = 0.02         # how fast the bit clock follows
TIMING_MARGIN = 1.08        # a new clock phase must be this much stronger

# The PSK31 Varicode, as G3PLX published it, for ASCII 0-127. Checked
# entry by entry against fldigi's table when this was written.
VARICODE = [
    "1010101011", "1011011011", "1011101101", "1101110111",     # 0
    "1011101011", "1101011111", "1011101111", "1011111101",
    "1011111111", "11101111", "11101", "1101101111",
    "1011011101", "11111", "1101110101", "1110101011",
    "1011110111", "1011110101", "1110101101", "1110101111",     # 16
    "1101011011", "1101101011", "1101101101", "1101010111",
    "1101111011", "1101111101", "1110110111", "1101010101",
    "1101011101", "1110111011", "1011111011", "1101111111",
    "1", "111111111", "101011111", "111110101",                 # 32 ' '
    "111011011", "1011010101", "1010111011", "101111111",
    "11111011", "11110111", "101101111", "111011111",
    "1110101", "110101", "1010111", "110101111",
    "10110111", "10111101", "11101101", "11111111",             # 48 '0'
    "101110111", "101011011", "101101011", "110101101",
    "110101011", "110110111", "11110101", "110111101",
    "111101101", "1010101", "111010111", "1010101111",
    "1010111101", "1111101", "11101011", "10101101",            # 64 '@'
    "10110101", "1110111", "11011011", "11111101",
    "101010101", "1111111", "111111101", "101111101",
    "11010111", "10111011", "11011101", "10101011",
    "11010101", "111011101", "10101111", "1101111",             # 80 'P'
    "1101101", "101010111", "110110101", "101011101",
    "101110101", "101111011", "1010101101", "111110111",
    "111101111", "111111011", "1010111111", "101101101",
    "1011011111", "1011", "1011111", "101111",                  # 96 '`'
    "101101", "11", "111101", "1011011",
    "101011", "1101", "111101011", "10111111",
    "11011", "111011", "1111", "111",
    "111111", "110111111", "10101", "10111",                    # 112 'p'
    "101", "110111", "1111011", "1101011",
    "11011111", "1011101", "111010101", "1010110111",
    "110111011", "1010110101", "1011010111", "1110110101",
]
DECODE = {code: chr(i) for i, code in enumerate(VARICODE)}
# What a decoded control character becomes on screen. Most are never sent;
# printing them as themselves would draw boxes, or move the cursor.
SHOWN = {"\r": "", "\n": "\n", "\t": " ", "\b": ""}


WORK_RATE = 12000           # everything below runs at about this
DECIM_TAPS = 63


class _Decimator:
    """Down to about WORK_RATE by a whole number, through a windowed-sinc
    low-pass that carries its state across blocks. Everything was tuned at
    12 kHz - the rate the shared capture hands this decoder - and run at
    48 kHz (the mock's rate) the same thresholds let noise print a few
    characters. One rate, tested, and a quarter of the arithmetic."""

    def __init__(self, rate: int) -> None:
        self.factor = max(1, int(rate) // WORK_RATE)
        self.rate = int(rate) // self.factor
        self._phase = 0
        if self.factor == 1:
            self._taps = None
            return
        cut = 0.45 / self.factor                    # of the input rate
        n = np.arange(DECIM_TAPS) - (DECIM_TAPS - 1) / 2
        taps = 2 * cut * np.sinc(2 * cut * n) * np.hamming(DECIM_TAPS)
        self._taps = taps / taps.sum()
        self._hist = np.zeros(DECIM_TAPS - 1)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        if self._taps is None:
            return x
        buf = np.concatenate((self._hist, x))
        y = np.convolve(buf, self._taps, mode="valid")
        self._hist = buf[-(DECIM_TAPS - 1):]
        out = y[self._phase::self.factor]
        self._phase = (self._phase - y.size) % self.factor
        return out


class PskDecoder:
    """Samples in, text out. One signal at a time: the strongest PSK31 in
    the passband, followed as it drifts."""

    def __init__(self, samplerate: int = 48000) -> None:
        self._decimate = _Decimator(samplerate)
        self.input_rate = int(samplerate)
        self.samplerate = self._decimate.rate
        self._ring = np.zeros(0, dtype=np.float64)
        self._ring_start = 0            # absolute index of _ring[0]
        self._total = 0                 # samples fed, ever
        self._next_search = int(SEARCH_SECONDS * self.samplerate)
        self.carrier_hz = 0.0
        self.snr_db = 0.0
        # How far the audio has already been read out loud. A replay after
        # losing a weak signal and finding it again re-read text already
        # printed, so the same words came out twice.
        self._printed_to = 0
        self._demod_reset()

    # -- what the card shows ---------------------------------------------------
    @property
    def tone_hz(self) -> float:
        return round(float(self.carrier_hz), 1)

    # Plain Python values, not numpy's: these go straight into a JSON
    # reply, and np.bool_ made the card's /text answer a 500.
    @property
    def hearing(self) -> bool:
        return bool(self.carrier_hz and self._quality > DECISIVE)

    @property
    def quality(self) -> float:
        return round(float(self._quality), 2)

    # -- input -----------------------------------------------------------------
    def feed(self, samples) -> str:
        x = self._decimate(np.asarray(samples, dtype=np.float64).ravel())
        if not x.size:
            return ""
        self._ring = np.concatenate((self._ring, x))
        self._total += x.size
        keep = int(RING_SECONDS * self.samplerate)
        if self._ring.size > keep:
            drop = self._ring.size - keep
            self._ring = self._ring[drop:]
            self._ring_start += drop
        out = []
        if self.carrier_hz:
            out.append(self._demodulate(x))
        if self._total >= self._next_search:
            self._next_search = self._total + int(SEARCH_EVERY * self.samplerate)
            out.append(self._search())
        return "".join(out)

    # -- finding the carrier -----------------------------------------------------
    def _search(self) -> str:
        n = int(SEARCH_SECONDS * self.samplerate)
        if self._ring.size < n:
            return ""
        best = self._best(carriers(self._ring[-n:], self.samplerate))
        if best is None:
            if self.carrier_hz and self._quality < 0.5:
                # Gone: nothing that keys, and the decisions are noise.
                self.carrier_hz = 0.0
                self.snr_db = 0.0
            return ""
        hz, snr, q = best
        if not self.carrier_hz:
            return self._lock(hz, snr)
        if abs(hz - self.carrier_hz) <= DRIFT_HZ:
            # The same signal; the AFC follows it more finely than this.
            self.snr_db = snr
            return ""
        # **Moving is judged on the symbols, not the squared line.** A loud
        # carrier beside the signal makes a mixing product that squares
        # 15 dB stronger than the PSK itself - which is what a squared line
        # measures - and the decoder left a signal it was reading perfectly
        # for one with nothing on it. Only a candidate whose symbols are
        # clearly more decisive than the current signal's wins it over.
        n = int(TRIAL_SECONDS * self.samplerate)
        here = trial_quality(self._ring[-n:], self.samplerate, self.carrier_hz)
        if q > here + MOVE_QUALITY or self._quality < 0.5:
            return self._lock(hz, snr)
        return ""

    def _best(self, found: list[tuple[float, float]]):
        """**The demodulator is the judge.** A squared line is only a
        candidate: squared PSK also has weaker lines half a symbol rate
        either side of the real one, which won during text and locked the
        decoder 15.6 Hz off, printing rubbish; and a PSK signal squared
        against a nearby carrier makes a line halfway between them, with
        nothing there at all. Each candidate, and the frequencies half a
        symbol rate either side of it, is demodulated over the last couple
        of seconds, and the one whose symbols are decisive wins. Noise and
        mixing products never are."""
        n = int(TRIAL_SECONDS * self.samplerate)
        if self._ring.size < n or not found:
            return None
        x = self._ring[-n:]
        best, best_q = None, TRIAL_QUALITY
        for hz, snr in found:
            for f in (hz, hz - BAUD / 2, hz + BAUD / 2):
                if not TONE_LOW_HZ <= f <= TONE_HIGH_HZ:
                    continue
                q = trial_quality(x, self.samplerate, f)
                if q > best_q + 0.02 or (best is None and q > best_q):
                    best, best_q = (f, snr, q), q
        return best

    def _lock(self, hz: float, snr: float) -> str:
        self.carrier_hz, self.snr_db = hz, snr
        # Twice through the ring: once to set the bit clock, once to read.
        # Read straight away, the first symbols were taken at whatever
        # phase the clock started on and came out as a stray character
        # before the text - a "-" ahead of every over in the tests.
        self._demod_reset()
        self._training = True
        self._demodulate(self._ring, start=self._ring_start)
        env, clock, hz_now = self._env.copy(), self._clock, self.carrier_hz
        self._demod_reset()
        self._env, self._clock, self.carrier_hz = env, clock, hz_now
        # Read the ring again through the new carrier: the opening of the
        # over is in there, and finding the carrier took this long. Only
        # what has not been printed already is printed.
        return self._demodulate(self._ring, start=self._ring_start)

    # -- demodulating -------------------------------------------------------------
    def _demod_reset(self) -> None:
        self._phase = 0.0
        self._pos = None                # absolute sample index of next output
        self._tail = np.zeros(0, dtype=np.complex128)
        self._tail_start = 0
        self._bb_count = 0
        self._env = np.zeros(SPS)
        self._clock = 0
        self._prev = None
        self._bits = ""
        self._char_q: list[float] = []
        self._quality = 0.0
        self._training = False

    def _demodulate(self, x: np.ndarray, start: int | None = None) -> str:
        if start is None:
            start = self._total - x.size
        step = 2 * math.pi * self.carrier_hz / self.samplerate
        ph = self._phase + step * np.arange(x.size)
        self._phase = float((ph[-1] + step) % (2 * math.pi)) if x.size else self._phase
        mixed = x * np.exp(-1j * ph)
        # One symbol's worth of averaging - the matched filter, near enough,
        # for a pulse a symbol long - read at 16 points a symbol.
        span = self.samplerate / BAUD
        if self._tail.size and self._tail_start + self._tail.size == start:
            buf = np.concatenate((self._tail, mixed))
            base = self._tail_start
        else:
            buf, base = mixed, start
        csum = np.concatenate(([0j], np.cumsum(buf)))
        hop = self.samplerate / BB_RATE
        if self._pos is None:
            self._pos = base + span
        out = []
        end = base + buf.size
        while self._pos < end:
            hi = int(round(self._pos)) - base
            lo = int(round(self._pos - span)) - base
            if lo >= 0:
                z = (csum[hi] - csum[lo]) / (hi - lo)
                got = self._baseband(z)
                if got and self._pos >= self._printed_to:
                    out.append(got)
            self._pos += hop
        if not self._training:
            self._printed_to = max(self._printed_to, end)
        keep = int(span) + 2
        self._tail = buf[-keep:] if buf.size > keep else buf
        self._tail_start = end - self._tail.size
        return "".join(out)

    def _baseband(self, z: complex) -> str:
        i = self._bb_count % SPS
        self._bb_count += 1
        self._env[i] += TIMING_ALPHA * (abs(z) - self._env[i])
        best = int(np.argmax(self._env))
        if best != self._clock and self._env[best] > TIMING_MARGIN * self._env[self._clock]:
            self._clock = best
        if i != self._clock:
            return ""
        out = self._symbol(z)
        return "" if self._training else out

    def _symbol(self, z: complex) -> str:
        prev, self._prev = self._prev, z
        if prev is None:
            return ""
        d = z * np.conj(prev)
        mag = abs(d)
        if mag == 0:
            return ""
        q = float(abs(d.real) / mag)
        self._quality += 0.1 * (q - self._quality)
        # The rotation left over after taking the keying out (doubling the
        # angle folds 180 degrees onto 0): that is the frequency error.
        err = float(np.angle(d * d)) / 2
        self.carrier_hz += AFC_GAIN * err * BAUD / (2 * math.pi)
        return self._bit(1 if d.real > 0 else 0, q)

    def _bit(self, bit: int, q: float) -> str:
        if not self._bits and bit == 0:
            return ""                           # idle reversals
        self._bits += str(bit)
        self._char_q.append(q)
        if not self._bits.endswith("00"):
            if len(self._bits) > 14:            # longer than any code: noise
                self._bits, self._char_q = "", []
            return ""
        code = self._bits[:-2]
        quals, self._bits, self._char_q = self._char_q, "", []
        ch = DECODE.get(code)
        # The character's own symbols and the run before it: a short code
        # ("e" is 11) is four symbols, and four symbols of noise come out
        # decisive often enough to print one now and then.
        if (ch is None or sum(quals) / len(quals) < DECISIVE
                or self._quality < DECISIVE):
            return ""
        return SHOWN.get(ch, ch if ch.isprintable() else "")

    def flush(self) -> str:
        return ""


def carriers(x: np.ndarray, rate: int) -> list[tuple[float, float]]:
    """Squared lines that could be a BPSK31 carrier, strongest first, with
    how far each stands above the floor around it in dB.

    Squaring takes the keying off: a PSK signal at f leaves a line at 2f,
    and so does a plain carrier - which also has a line at f before it is
    squared, where BPSK has a hole. That second test keeps a carrier out of
    the list; `PskDecoder._best` decides among what is left.
    """
    n = x.size
    win = np.hanning(n)
    sq = np.abs(np.fft.rfft((x * x - np.mean(x * x)) * win)) ** 2
    plain = np.abs(np.fft.rfft(x * win)) ** 2
    bin_hz = rate / n
    lo = int(2 * TONE_LOW_HZ / bin_hz)
    hi = min(int(2 * TONE_HIGH_HZ / bin_hz), sq.size - 1)
    if hi <= lo + 8:
        return []
    band = sq[lo:hi]
    floor = float(np.median(band)) + 1e-30
    side = int(round(BAUD / 2 / bin_hz))        # the idle sidebands, f +- 15.6
    out: list[tuple[float, float]] = []
    taken: list[float] = []
    # Peaks, not bins: the strongest bins were all the shoulders of one
    # loud carrier's line, and the PSK line beside it was never looked at.
    peaks = np.flatnonzero((band[1:-1] > band[:-2]) & (band[1:-1] >= band[2:])) + 1
    for k in peaks[np.argsort(band[peaks])[::-1]][:60]:
        k2 = lo + int(k)
        line = float(sq[max(k2 - 1, 0):k2 + 2].sum())
        snr = 10 * math.log10(line / (3 * floor))
        if snr < LOCK_DB:
            break
        f = _refine(sq, k2) * bin_hz / 2
        if any(abs(f - t) < BAUD for t in taken):
            continue                            # a neighbour of one listed
        taken.append(f)
        kc = int(round(f / bin_hz))
        if kc - side - 2 < 0 or kc + side + 3 > plain.size:
            continue
        centre = float(plain[kc - 1:kc + 2].max())
        sides = max(float(plain[kc - side - 2:kc - side + 3].max()),
                    float(plain[kc + side - 2:kc + side + 3].max()))
        if centre > sides * 10 ** (CARRIER_DB / 10):
            continue                            # a carrier, not PSK
        out.append((f, snr))
        if len(out) >= CANDIDATES:
            break
    return out


def trial_quality(x: np.ndarray, rate: int, hz: float) -> float:
    """How decisive BPSK31 symbols at `hz` are over `x`: the mean |cos| of
    the phase change between symbols, at the best of sixteen clock phases.
    About 1 for a signal, 2/pi (0.64) for noise."""
    t = np.arange(x.size)
    mixed = x * np.exp(-2j * np.pi * hz * t / rate)
    span = rate / BAUD
    csum = np.concatenate(([0j], np.cumsum(mixed)))
    pos = np.arange(span, x.size, rate / BB_RATE)
    hi = np.round(pos).astype(int)
    lo = np.round(pos - span).astype(int)
    z = (csum[hi] - csum[lo]) / np.maximum(hi - lo, 1)
    # **PSK dips; a tone does not.** Every phase reversal takes the
    # amplitude through zero between symbols, so the envelope is largest
    # at one clock phase and smallest half a symbol later. A loud carrier
    # a few hundred hertz away leaks through as a steady tone that turns a
    # fixed amount each symbol - perfectly "decisive" - and locked the
    # decoder onto nothing while the PSK beside it went unread. Its
    # envelope is flat at every phase.
    env = np.abs(z)
    if env.size < 4 * SPS:
        return 0.0
    by_phase = np.array([env[ph::SPS].mean() for ph in range(SPS)])
    if by_phase.max() < TRIAL_DIP * max(by_phase.min(), 1e-30):
        return 0.0
    best = 0.0
    for ph in range(SPS):
        s = z[ph::SPS]
        if s.size < 8:
            continue
        d = s[1:] * np.conj(s[:-1])
        mag = np.abs(d)
        ok = mag > 0
        if not ok.any():
            continue
        q = float(np.mean(np.abs(d.real[ok]) / mag[ok]))
        best = max(best, q)
    return best


def find_carrier(x: np.ndarray, rate: int) -> tuple[float, float] | None:
    """The strongest candidate, for callers that want one answer."""
    got = carriers(x, rate)
    return got[0] if got else None


def _refine(spec: np.ndarray, k: int) -> float:
    """The peak between bins, from a parabola through three."""
    if k <= 0 or k >= spec.size - 1:
        return float(k)
    a, b, c = (math.log(spec[k - 1] + 1e-30), math.log(spec[k] + 1e-30),
               math.log(spec[k + 1] + 1e-30))
    den = a - 2 * b + c
    return k + (0.5 * (a - c) / den if den else 0.0)


# -- making a signal, for the tests and the card's mock ------------------------
def encode(text: str) -> str:
    """Text as the bit stream a PSK31 transmitter sends: each character's
    code followed by 00. Characters outside ASCII are left out."""
    return "".join(VARICODE[ord(ch)] + "00" for ch in text if ord(ch) < 128)


def psk_audio(text: str = "", carrier_hz: float = 1000.0, samplerate: int = 48000,
              amplitude: float = 0.5, lead_seconds: float = 1.0,
              tail_seconds: float = 0.5, drift_hz_per_s: float = 0.0,
              bits: str | None = None) -> np.ndarray:
    """BPSK31 as a transmitter makes it: a preamble of reversals (idle 0s),
    the text, then unmodulated carrier. Each reversal is shaped to a cosine
    over its symbol, as on the air."""
    lead = "0" * int(lead_seconds * BAUD)
    tail = "1" * int(tail_seconds * BAUD)
    stream = bits if bits is not None else lead + encode(text) + "00" + tail
    n_sym = samplerate / BAUD
    total = int(len(stream) * n_sym)
    t = np.arange(total) / samplerate
    env = np.empty(total)
    sign = 1.0
    for i, b in enumerate(stream):
        a, z = int(i * n_sym), int((i + 1) * n_sym)
        tt = (np.arange(z - a)) / (z - a)
        if b == "0":
            env[a:z] = sign * np.cos(np.pi * tt)
            sign = -sign
        else:
            env[a:z] = sign
    freq = carrier_hz + drift_hz_per_s * t
    phase = 2 * np.pi * np.cumsum(freq) / samplerate
    return (amplitude * env * np.cos(phase)).astype(np.float32)
