"""WSPR: message packing, the convolutional code, the waveform, and a
decoder for a two-minute slot of audio.

Imports nothing from the card, like `ftx.py`, `cw.py` and `dsp.py`: audio
in, decodes out, so `tests/test_wspr.py` can drive it with synthesised
transmissions and WSJT-X's own `wsprcode` and `wsprd` as the reference.
Nothing here is taken from WSJT-X's source, which is GPL: the packing was
read off the protocol description and pinned bit for bit against
`wsprcode`'s output, and the sync vector below is the published one,
copied out of that output.

The protocol, as far as this file needs it:

* **50 bits.** A callsign in 28, a grid square and a power in 22. The
  power's last digit is always 0, 3 or 7, which leaves room for two more
  message types in the same bits: type 2 carries a prefix or suffix in
  place of the grid (``PJ4/K1ABC 33``), type 3 a six-character grid in
  place of the callsign and a 15-bit hash of the callsign in place of the
  grid (``<PJ4/K1ABC> FK52UD 33``). A station with a compound call sends
  the two alternately, so the hash can only be read once the type 2 has
  been heard.
* **A K=32, rate 1/2 convolutional code**, 31 zero bits of tail, so 162
  coded bits, interleaved by bit-reversing an 8-bit index.
* **162 symbols of 4-FSK**, each carrying one coded bit and one bit of
  the fixed sync vector, 12000/8192 = 1.46 Hz apart and 8192/12000 =
  0.683 s long. A transmission takes 110.6 s, starting one second into
  an even minute, somewhere in a 200 Hz window centred on 1500 Hz audio.
* **K=32 cannot be Viterbi-decoded** (2^31 states), so the decoder is
  sequential: a stack decoder, which follows the best-scoring path and
  backs up when it stops paying. It costs almost nothing on a good
  signal and a bounded amount on noise.
"""
from __future__ import annotations

import heapq
import math
import re
from dataclasses import dataclass

import numpy as np

RATE = 12000
SPS = 8192                      # samples per symbol at RATE
NSYM = 162
SPACING = RATE / SPS            # 1.4648 Hz between tones
SLOT = 120.0
START = 1.0                     # a transmission starts 1 s into the slot
CENTRE = 1500.0                 # the middle of the WSPR window, audio Hz
HALF_WIDTH = 110.0              # how far either side of CENTRE to look

# The sync vector, one bit per symbol: the low bit of every channel
# symbol. Published with the protocol; `wsprcode` prints it.
SYNC = np.array([int(c) for c in (
    "110000001000111000100101111000000010010100000010110011010001"
    "101000011010101010010010110001101010001000001001001110110011"
    "010001110000010100110000000110101100011000")], dtype=np.int8)

# The usual WSPR dial frequencies, USB, by band in metres: where WSJT-X
# and every beacon sit, so the signals land 1400-1600 Hz into the audio.
# 60 m is left out on purpose - its WSPR frequency differs by country,
# and a button that tunes somewhere a licence does not allow is worse
# than none. The Decoder card's band buttons are checked against this.
DIALS = {"2200": 136000, "630": 474200, "160": 1836600, "80": 3568600,
         "40": 7038600, "30": 10138700, "20": 14095600, "17": 18104600,
         "15": 21094600, "12": 24924600, "10": 28124600, "6": 50293000}

POLY = (0xF2D05351, 0xE4613C47)
POWERS = (0, 3, 7, 10, 13, 17, 20, 23, 27, 30, 33, 37, 40, 43, 47, 50,
          53, 57, 60)


# ---------------------------------------------------------------- packing

_ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ "


def _code(c: str) -> int:
    """0-9, then A-Z as 10-35, then space as 36."""
    return _ALNUM.index(c)


def _pack_call(call: str) -> int:
    """28 bits from a callsign of up to six characters whose third is a
    digit - or, for a one-letter prefix (G4ABC), whose second is, which
    a leading space turns into the same shape."""
    call = call.upper()
    if len(call) >= 2 and call[1].isdigit() and not call[2:3].isdigit():
        call = " " + call
    call = call.ljust(6)
    if (len(call) != 6 or not call[2].isdigit()
            or any(c not in _ALNUM for c in call)
            or not all(c.isalpha() or c == " " for c in call[3:])
            or call[1] == " " or call[3] == " "):
        raise ValueError(f"{call.strip()!r} is not a callsign WSPR can send")
    n = _code(call[0])
    n = n * 36 + _code(call[1])
    n = n * 10 + int(call[2])
    for c in call[3:]:
        n = n * 27 + (26 if c == " " else _code(c) - 10)
    return n


def _unpack_call(n: int) -> str:
    out = []
    for _ in range(3):
        v = n % 27
        out.append(" " if v == 26 else _ALNUM[v + 10])
        n //= 27
    out.append(str(n % 10))
    n //= 10
    out.append(_ALNUM[n % 36])
    n //= 36
    if n > 36:
        raise ValueError("not a callsign")
    out.append(_ALNUM[n])
    return "".join(reversed(out)).strip()


def _grid4(n: int) -> str:
    """The four-character locator from the top 15 bits of the second
    field: (179 - 10*L1 - D1) * 180 + 10*L2 + D2."""
    a, b = divmod(n, 180)
    a = 179 - a
    l1, d1 = divmod(a, 10)
    l2, d2 = divmod(b, 10)
    if not (0 <= l1 < 18 and 0 <= l2 < 18):
        raise ValueError("not a grid")
    return f"{chr(65 + l1)}{chr(65 + l2)}{d1}{d2}"


def _pack_grid4(grid: str) -> int:
    g = grid.upper()
    if not re.fullmatch(r"[A-R]{2}[0-9]{2}", g):
        raise ValueError(f"{grid!r} is not a four-character grid")
    return ((179 - 10 * (ord(g[0]) - 65) - int(g[2])) * 180
            + 10 * (ord(g[1]) - 65) + int(g[3]))


def _pack_affix(prefix: str = "", suffix: str = "") -> int:
    """A prefix of up to three characters, right-aligned in base 37; or a
    suffix of one character or two digits, from 60000 up."""
    if prefix:
        p = prefix.upper().rjust(3)
        if len(p) != 3 or any(c not in _ALNUM for c in p):
            raise ValueError(f"{prefix!r} is not a prefix WSPR can send")
        return (_code(p[0]) * 37 + _code(p[1])) * 37 + _code(p[2])
    s = suffix.upper()
    if len(s) == 1 and s in _ALNUM[:36]:
        return 60000 + _code(s)
    if len(s) == 2 and s.isdigit():
        return 60000 + 26 + int(s)
    raise ValueError(f"{suffix!r} is not a suffix WSPR can send")


def _unpack_affix(n3: int, call: str) -> str:
    if n3 < 60000:
        p = []
        for _ in range(3):
            n3, v = divmod(n3, 37)
            p.append(_ALNUM[v])
        prefix = "".join(reversed(p)).strip()
        return f"{prefix}/{call}"
    s = n3 - 60000
    if s < 36:
        return f"{call}/{_ALNUM[s]}"
    if 36 <= s <= 125:
        return f"{call}/{s - 26:02d}"
    raise ValueError("not a suffix")


def _rot(x: int, k: int) -> int:
    return ((x << k) | (x >> (32 - k))) & 0xFFFFFFFF


def lookup3(data: bytes, seed: int) -> int:
    """Bob Jenkins' public-domain hashlittle(), which WSPR uses (seed 146,
    15 bits) to stand a callsign in for itself in a type 3 message."""
    a = b = c = (0xDEADBEEF + len(data) + seed) & 0xFFFFFFFF
    k = data
    m = 0xFFFFFFFF
    while len(k) > 12:
        a = (a + int.from_bytes(k[0:4], "little")) & m
        b = (b + int.from_bytes(k[4:8], "little")) & m
        c = (c + int.from_bytes(k[8:12], "little")) & m
        a = (a - c) & m; a ^= _rot(c, 4); c = (c + b) & m
        b = (b - a) & m; b ^= _rot(a, 6); a = (a + c) & m
        c = (c - b) & m; c ^= _rot(b, 8); b = (b + a) & m
        a = (a - c) & m; a ^= _rot(c, 16); c = (c + b) & m
        b = (b - a) & m; b ^= _rot(a, 19); a = (a + c) & m
        c = (c - b) & m; c ^= _rot(b, 4); b = (b + a) & m
        k = k[12:]
    if not k:
        return c
    t = k.ljust(12, b"\0")
    a = (a + int.from_bytes(t[0:4], "little")) & m
    b = (b + int.from_bytes(t[4:8], "little")) & m
    c = (c + int.from_bytes(t[8:12], "little")) & m
    c ^= b; c = (c - _rot(b, 14)) & m
    a ^= c; a = (a - _rot(c, 11)) & m
    b ^= a; b = (b - _rot(a, 25)) & m
    c ^= b; c = (c - _rot(b, 16)) & m
    a ^= c; a = (a - _rot(c, 4)) & m
    b ^= a; b = (b - _rot(a, 14)) & m
    c ^= b; c = (c - _rot(b, 24)) & m
    return c


def call_hash(call: str) -> int:
    return lookup3(call.upper().encode("ascii"), 146) & 0x7FFF


class Hashes:
    """Compound callsigns heard in full, by their 15-bit hash, so a type 3
    message from the same station can say who it is."""

    def __init__(self) -> None:
        self._by: dict[int, str] = {}

    def add(self, call: str) -> None:
        self._by[call_hash(call)] = call

    def get(self, h: int) -> str:
        return self._by.get(h, "")


def _power(dbm: int) -> int:
    dbm = int(dbm)
    if dbm not in POWERS:
        raise ValueError(f"{dbm} dBm is not a WSPR power level")
    return dbm


def pack(text: str) -> int:
    """A WSPR message as its 50-bit number."""
    words = text.upper().split()
    if len(words) == 3 and words[0].startswith("<"):
        call = words[0].strip("<>")
        grid = words[1]
        if not re.fullmatch(r"[A-R]{2}[0-9]{2}[A-X]{2}", grid):
            raise ValueError(f"{grid!r} is not a six-character grid")
        n = _pack_call(grid[1:] + grid[0])
        ntype = -(_power(int(words[2])) + 1)
        m = call_hash(call) * 128 + ntype + 64
        return n << 22 | m
    if len(words) == 3:
        n = _pack_call(words[0])
        m = _pack_grid4(words[1]) * 128 + _power(int(words[2])) + 64
        return n << 22 | m
    if len(words) == 2 and "/" in words[0]:
        a, _, b = words[0].partition("/")
        if len(a) <= 3 and len(b) > 2:
            base, n3 = b, _pack_affix(prefix=a)
        else:
            base, n3 = a, _pack_affix(suffix=b)
        n = _pack_call(base)
        nadd, ng = (2, n3 - 32768) if n3 >= 32768 else (1, n3)
        m = ng * 128 + _power(int(words[1])) + nadd + 64
        return n << 22 | m
    raise ValueError(f"{text!r} is not a WSPR message")


def unpack(value: int, hashes: Hashes | None = None) -> tuple[str, dict]:
    """The message text, and its fields: call, grid, dbm and kind."""
    n, m = value >> 22, value & 0x3FFFFF
    ntype = (m & 127) - 64
    ng = m >> 7
    if ntype >= 0 and ntype % 10 in (0, 3, 7):
        call, grid = _unpack_call(n), _grid4(ng)
        return f"{call} {grid} {ntype}", {"call": call, "grid": grid,
                                          "dbm": ntype, "kind": 1}
    if ntype > 0:
        nu = ntype % 10
        nadd = nu - 7 if nu > 7 else (nu - 3 if nu > 3 else nu)
        dbm = ntype - nadd
        if dbm not in POWERS or nadd not in (1, 2):
            raise ValueError("not a WSPR message")
        full = _unpack_affix(ng + 32768 * (nadd - 1), _unpack_call(n))
        if hashes is not None:
            hashes.add(full)
        return f"{full} {dbm}", {"call": full, "grid": "", "dbm": dbm,
                                 "kind": 2}
    dbm = -(ntype + 1)
    if dbm not in POWERS:
        raise ValueError("not a WSPR message")
    rot = _unpack_call(n).ljust(6)
    grid = rot[5] + rot[:5]
    if not re.fullmatch(r"[A-R]{2}[0-9]{2}[A-X]{2}", grid):
        raise ValueError("not a WSPR message")
    call = hashes.get(ng) if hashes is not None else ""
    shown = f"<{call}>" if call else "<...>"
    return f"{shown} {grid} {dbm}", {"call": call, "grid": grid,
                                     "dbm": dbm, "kind": 3, "hash": ng}


# ---------------------------------------------------------------- coding

def _bits50(value: int) -> list:
    return [(value >> (49 - i)) & 1 for i in range(50)]


def encode_bits(value: int) -> np.ndarray:
    """The 162 coded bits, before interleaving."""
    reg = 0
    out = []
    for b in _bits50(value) + [0] * 31:
        reg = ((reg << 1) | b) & 0xFFFFFFFF
        out.append((reg & POLY[0]).bit_count() & 1)
        out.append((reg & POLY[1]).bit_count() & 1)
    return np.array(out, dtype=np.int8)


def _interleave_order() -> np.ndarray:
    """Where coded bit p goes: the p-th 8-bit index, bit-reversed, that is
    under 162."""
    order = []
    for i in range(256):
        j = int(f"{i:08b}"[::-1], 2)
        if j < NSYM:
            order.append(j)
    return np.array(order)


_ORDER = _interleave_order()


def interleave(bits: np.ndarray) -> np.ndarray:
    out = np.zeros(NSYM, dtype=bits.dtype)
    out[_ORDER] = bits
    return out


def deinterleave(values: np.ndarray) -> np.ndarray:
    return values[_ORDER]


def channel_symbols(text_or_value) -> np.ndarray:
    """The 162 tones, 0-3, that carry a message."""
    v = pack(text_or_value) if isinstance(text_or_value, str) else text_or_value
    return (SYNC + 2 * interleave(encode_bits(v))).astype(np.int8)


# ---------------------------------------------------------------- the air

def synth(symbols, f0: float, rate: int = RATE, drift: float = 0.0,
          amplitude: float = 1.0) -> np.ndarray:
    """A continuous-phase transmission with tone 0 at f0 Hz, drifting by
    `drift` Hz over its length, from sample 0. Real, float64."""
    sps = int(round(SPS * rate / RATE))
    tones = np.repeat(np.asarray(symbols, dtype=float), sps)
    t = np.arange(tones.size) / tones.size
    freq = f0 + tones * SPACING + drift * (t - 0.5)
    phase = 2 * np.pi * np.cumsum(freq) / rate
    return amplitude * np.sin(phase)


def slot_audio(messages, rate: int = RATE, seed: int = 0,
               noise: bool = True, length: float = SLOT) -> np.ndarray:
    """A slot of audio from [(text, f0, dt, snr_db[, drift])]. Noise is
    white at unit variance and SNR is in 2500 Hz, as WSPR reports it."""
    rng = np.random.default_rng(seed)
    n = int(length * rate)
    out = np.zeros(n)
    for msg in messages:
        text, f0, dt, snr = msg[:4]
        drift = msg[4] if len(msg) > 4 else 0.0
        a = math.sqrt(2 * 10 ** (snr / 10) * 2500 / (rate / 2))
        s = synth(channel_symbols(text), f0, rate, drift, a)
        at = int(round((START + dt) * rate))
        lo, hi = max(at, 0), min(at + s.size, n)
        if hi > lo:
            out[lo:hi] += s[lo - at:hi - at]
    if noise:
        out += rng.standard_normal(n)
    peak = np.abs(out).max() or 1.0
    return (out / peak * 0.5).astype(np.float32)


# ---------------------------------------------------------------- decoding
#
# The audio is mixed down to complex baseband around CENTRE and decimated
# to 375 Hz, where a symbol is exactly 256 samples and the tones are
# exactly one 256-point FFT bin apart. Everything after that works there.

BB_RATE = 375
BB_SPS = 256                    # samples per symbol at BB_RATE
HOP = 16                        # spectrogram hop, samples: 1/16 symbol
LAG_LO, LAG_HI = -2.0, 3.0      # s from START a transmission may begin
DRIFTS = np.arange(-4.0, 4.01, 0.5)     # Hz over the whole transmission
MAX_CANDIDATES = 60
CAND_SNR = -33.0                # dB in 2500 Hz, from the averaged spectrum
SYNC_MIN = 0.12                 # the normalised sync metric to try one
BLOCKS = (3, 2, 1)              # tried in turn; see _soft
PASSES = 3
STACK_LIMIT = 40000             # node expansions before giving up
# What makes a decode believable. On real signals the path metric is
# not it: 80 m at night smears a station's energy into its neighbouring
# tones, and three stations wsprd also heard came out at -4, -8 and -47
# (KG7RDR, a -5 dB signal, was thrown away at a floor of 5). Noise does
# not get there at all - 750 attempts on pure noise, none reached the end
# of the tree within STACK_LIMIT - so the floor is only below anything
# real. HARD_MAX is the guard that matters: the decode re-encoded may
# disagree with the received bits in at most this many places; the real
# decodes measured were 3 to 29.
METRIC_MIN = -60.0
HARD_MAX = 45


@dataclass
class Decode:
    text: str
    fields: dict
    snr: int
    dt: float                   # s after the nominal start
    freq: float                 # audio Hz of the signal's centre, at its start
    drift: float                # Hz over the transmission
    sync: float = 0.0
    metric: float = 0.0
    cycles: int = 0
    block: int = 0              # symbols judged together; 0 if not decoded
    passno: int = 1
    mode: str = "WSPR"

    def as_dict(self) -> dict:
        return {"mode": self.mode, "text": self.text, "snr": self.snr,
                "dt": round(self.dt, 2), "freq": round(self.freq, 1),
                "drift": round(self.drift, 1), **self.fields}


def _baseband(audio: np.ndarray, rate: int) -> np.ndarray:
    """Complex 375 Hz samples of the 375 Hz around CENTRE, by FFT: the
    whole slot at once, so there is no filter to design and no edge."""
    n = int(round(SLOT * rate))
    x = np.zeros(n)
    m = min(n, audio.size)
    x[:m] = audio[:m]
    spec = np.fft.rfft(x)
    df = rate / n
    nb = int(round(SLOT * BB_RATE))             # 45000 samples out
    c = int(round(CENTRE / df))
    idx = c + np.arange(-(nb // 2), nb - nb // 2)
    sub = np.zeros(nb, dtype=complex)
    ok = (idx >= 0) & (idx < spec.size)
    sub[ok] = spec[idx[ok]]
    sub = np.fft.ifftshift(sub)
    return np.fft.ifft(sub) * (nb / n) * 2


def _spectrogram(bb: np.ndarray) -> np.ndarray:
    """Power, frames x 512 bins (0.73 Hz each, fftshifted so bin 256 is
    CENTRE), one symbol's worth of samples per frame, a frame every HOP."""
    nf = (bb.size - BB_SPS) // HOP
    idx = np.arange(BB_SPS)[None, :] + HOP * np.arange(nf)[:, None]
    frames = bb[idx]
    spec = np.fft.fftshift(np.fft.fft(frames, 512, axis=1), axes=1)
    return (spec.real ** 2 + spec.imag ** 2).astype(np.float32)


def _bin_hz(b: float) -> float:
    return (b - 256) * BB_RATE / 512


def _candidates(power: np.ndarray) -> tuple[list, float]:
    """(centre bin, spectrum SNR) for every hump that stands out of the
    averaged spectrum, strongest first; and the noise floor per bin.

    The peaks are taken from a light smoothing and the SNR from a heavy
    one. Smoothing over the signal's whole width (7 half-bins) is the
    right way to measure it and the wrong way to find it: two stations
    a few hertz apart came out as one hump, and only one of them was
    tried."""
    avg = power.mean(axis=0)
    light = np.convolve(avg, np.ones(3) / 3, mode="same")
    # Four tones span 3 tone spacings = 6 half-bins: a window of 7.
    heavy = np.convolve(avg, np.ones(7) / 7, mode="same")
    lo = int(256 + (-HALF_WIDTH) / (BB_RATE / 512))
    hi = int(256 + HALF_WIDTH / (BB_RATE / 512))
    floor = float(np.percentile(heavy[lo:hi], 30))
    out = []
    for b in range(lo + 1, hi - 1):
        if light[b] >= light[b - 1] and light[b] > light[b + 1]:
            # A signal's power is spread over its four tones; noise in
            # 2500 Hz is floor per 0.73 Hz bin times 2500/0.73.
            excess = (heavy[b] - floor) * 7
            if excess <= 0:
                continue
            snr = 10 * math.log10(excess / (floor * 2500 / (BB_RATE / 512)))
            if snr >= CAND_SNR:
                out.append((b, snr))
    out.sort(key=lambda c: -c[1])
    return out[:MAX_CANDIDATES], floor


_SGN = (2 * SYNC.astype(np.float32) - 1)


def _coarse(power: np.ndarray, centre_bin: int) -> tuple:
    """The (lag frame, tone-0 bin, drift) that best fit the sync vector,
    and how well. Tones are two half-bins apart; tone 0 sits three below
    the centre of the four."""
    nf = power.shape[0]
    frames_per_sym = BB_SPS // HOP
    lag0 = int(round((START + LAG_LO) * BB_RATE / HOP))
    lag1 = int(round((START + LAG_HI) * BB_RATE / HOP))
    lags = np.arange(max(lag0, 0), min(lag1, nf - frames_per_sym * NSYM) + 1)
    sym = np.arange(NSYM)
    best = (-1e9, 0, 0, 0.0)
    for drift in DRIFTS:
        # Drift in half-bins at each symbol, about the middle.
        shift = np.round(drift * (sym / (NSYM - 1) - 0.5)
                         / (BB_RATE / 512)).astype(int)
        for b0 in range(centre_bin - 3 - 2, centre_bin - 3 + 3):
            bins = b0 + shift[:, None] + 2 * np.arange(4)[None, :]
            if bins.min() < 0 or bins.max() >= power.shape[1]:
                continue
            rows = lags[:, None] + frames_per_sym * sym[None, :]
            p = power[rows[:, :, None], bins[None, :, :]]   # lag x sym x 4
            tot = p.sum(axis=2) + 1e-12
            odd = (p[:, :, 1] + p[:, :, 3] - p[:, :, 0] - p[:, :, 2]) / tot
            metric = (odd * _SGN[None, :]).mean(axis=1)
            i = int(np.argmax(metric))
            if metric[i] > best[0]:
                best = (float(metric[i]), int(lags[i]), b0, float(drift))
    return best


def _amps(bb: np.ndarray, start: float, f0: float, drift: float
          ) -> np.ndarray | None:
    """162 x 4 complex tone amplitudes by direct correlation, the
    transmission taken to begin at sample `start` with tone 0 at f0 Hz
    (baseband), drifting linearly by `drift` Hz over its length.

    **Coherent across symbols, and that is worth about two decibels.**
    WSPR's tones are exactly 1/T apart and its phase is continuous, so the
    extra phase a tone adds over a symbol is a whole number of turns: if
    the carrier is followed by the integral of its frequency (not by
    frequency times time, which jumps at every symbol under drift) and
    each tone is referenced to its own symbol's start, a signal's
    amplitude has the same phase in every symbol. That is what lets
    `_soft` add symbols together."""
    n0 = int(round(start))
    total = NSYM * BB_SPS
    if n0 + total <= 0 or n0 >= bb.size:
        return None
    # Audio before the slot or after it is silence, not a reason to give
    # up: a station that starts right at the top of the minute is early
    # by a second, which is ordinary.
    seg = np.zeros(total, dtype=complex)
    lo, hi = max(n0, 0), min(n0 + total, bb.size)
    seg[lo - n0:hi - n0] = bb[lo:hi]
    t = np.arange(total) / total
    phase = 2 * np.pi * np.cumsum(f0 + drift * (t - 0.5)) / BB_RATE
    base = (seg * np.exp(-1j * phase)).reshape(NSYM, BB_SPS)
    return base @ _TONE_REF


_TONE_REF = np.exp(-2j * np.pi * np.outer(np.arange(BB_SPS), np.arange(4))
                   * SPACING / BB_RATE)


def _powers(c: np.ndarray) -> np.ndarray:
    return c.real ** 2 + c.imag ** 2


# For the fine search: each pair of symbols, every data pattern of the
# pair as one coherent sum (rows of _PAIR_TONES), the best kept.
_PAIR_J = np.arange(0, NSYM, 2)
_PAIR_TONES = np.stack([
    np.stack([SYNC[_PAIR_J] + 2 * h0, SYNC[_PAIR_J + 1] + 2 * h1])
    for h0, h1 in ((0, 0), (0, 1), (1, 0), (1, 1))]).astype(int)  # 4x2x81


def _sync_of(c: np.ndarray | None) -> float:
    """How well a trial position fits: the energy the sync vector puts in
    the right places, as a share of all of it.

    **Judged two symbols at a time, coherently.** The obvious measure
    takes each symbol's tone powers separately, and on a weak signal its
    peak is wherever the noise happened to lean: at -29.5 dB it placed
    the start 20 samples out on average (8% of a symbol) and caught one
    signal in five within 8. Adding a pair as one coherent sum - every
    data pattern tried, the best kept, as in `_soft` - put all of them
    within 8 and averaged 4. That alone was most of the gap to wsprd."""
    if c is None:
        return -1.0
    a = c[_PAIR_J[None, None, :] + np.arange(2)[None, :, None], _PAIR_TONES]
    best = _powers(a.sum(axis=1)).max(axis=0)
    return float(best.sum() / (_powers(c).sum() + 1e-12))


def _fine(bb: np.ndarray, lag: int, b0: int, drift: float) -> tuple:
    """Refine start (samples), f0 (Hz) and drift around the coarse fit, by
    climbing the sync metric in shrinking steps."""
    best = (_sync_of(_amps(bb, lag * HOP, _bin_hz(b0), drift)),
            float(lag * HOP), _bin_hz(b0), drift)
    for st, sf, sd in ((8, 0.18, 0.5), (4, 0.09, 0.25), (2, 0.045, 0.12),
                       (1, 0.022, 0.06)):
        improved = True
        while improved:
            improved = False
            _, s0, g0, d0 = best
            for ds, dg, dd in ((-st, 0, 0), (st, 0, 0), (0, -sf, 0),
                               (0, sf, 0), (0, 0, -sd), (0, 0, sd)):
                m = _sync_of(_amps(bb, s0 + ds, g0 + dg, d0 + dd))
                if m > best[0] + 1e-5:
                    best = (m, s0 + ds, g0 + dg, d0 + dd)
                    improved = True
    return best


def _lni0(x: np.ndarray) -> np.ndarray:
    return np.log(np.i0(np.minimum(x, 700.0)))


_HYPS = {b: np.array([[(h >> (b - 1 - j)) & 1 for j in range(b)]
                      for h in range(2 ** b)]) for b in (1, 2, 3)}


def _soft(c: np.ndarray, block: int = 3) -> np.ndarray:
    """One LLR per channel symbol for its data bit.

    The sync bit says which pair of tones a symbol is in, and the data
    bit which of the pair; the two tones of the *other* pair never carry
    the signal, which makes them an exact measure of the noise. With
    `block` > 1 the symbol is judged with its neighbours: every data
    pattern of the block is tried as one coherent sum (`_amps` is why that
    is allowed), and the bit's LLR is the best pattern with it set against
    the best with it clear. Three symbols cost 8 sums and gain about 2 dB
    over judging each alone - the difference between this and wsprd, and
    then some. A phase that wanders - fading, an unsteady oscillator -
    spoils the sum, which is why `decode` falls back to smaller blocks."""
    idx = np.arange(NSYM)
    s = SYNC.astype(int)
    p = _powers(c)
    noise = float((p[idx, 1 - s] + p[idx, 3 - s]).mean()) / 2 or 1e-12
    hi, lo = p[idx, s + 2], p[idx, s]
    a2 = max(float((hi + lo).mean()) - 2 * noise, 1e-9 * noise)
    k = 2 * math.sqrt(a2) / noise
    if block == 1:
        return _lni0(k * np.sqrt(hi)) - _lni0(k * np.sqrt(lo))
    hyps = _HYPS[block]
    out = np.zeros(NSYM)
    for i in range(NSYM):
        j0 = min(max(i - block // 2, 0), NSYM - block)
        js = np.arange(j0, j0 + block)
        tones = s[js][None, :] + 2 * hyps
        lv = _lni0(k * np.abs(c[js[None, :], tones].sum(axis=1)))
        pos = hyps[:, i - j0]
        out[i] = lv[pos == 1].max() - lv[pos == 0].max()
    return out


def _metric_table(llr: np.ndarray) -> list:
    """Fano metric for each coded bit being 0 or 1: 1 + log2 P(bit|r) - R
    with R = 1/2."""
    out = []
    for L in llr:
        L = float(np.clip(L, -30, 30))
        p1 = 1 / (1 + math.exp(-L))
        out.append((0.5 + math.log2(max(1 - p1, 1e-12)),
                    0.5 + math.log2(max(p1, 1e-12))))
    return out


def stack_decode(llr_coded: np.ndarray, limit: int = STACK_LIMIT
                 ) -> tuple[int | None, float, int]:
    """The 50-bit message most likely to have been sent, by a stack
    (Zigangirov-Jelinek) sequential decoder; its path metric; and how
    many nodes it expanded. None if it gave up."""
    mt = _metric_table(llr_coded)
    p0, p1 = POLY
    mask = 0xFFFFFFFF
    heap = [(-0.0, 0, 0, 0)]        # (-metric, -depth, reg, bits)
    expanded = 0
    while heap and expanded < limit:
        neg, negd, reg, bits = heapq.heappop(heap)
        depth = -negd
        if depth == 81:
            return bits >> 31, -neg, expanded
        expanded += 1
        m0, m1 = mt[2 * depth], mt[2 * depth + 1]
        for b in ((0,) if depth >= 50 else (0, 1)):
            r = ((reg << 1) | b) & mask
            o0 = (r & p0).bit_count() & 1
            o1 = (r & p1).bit_count() & 1
            heapq.heappush(heap, (neg - m0[o0] - m1[o1], negd - 1, r,
                                  (bits << 1) | b))
        if len(heap) > 4 * limit:
            heap = heapq.nsmallest(2 * limit, heap)
            heapq.heapify(heap)
    return None, 0.0, expanded


def _snr(c: np.ndarray, symbols: np.ndarray, floor: float) -> float:
    """Signal against noise in 2500 Hz, from the station's power in its
    four tones and the band's own noise floor per tone bin (1.46 Hz).

    **Not from the other three tones.** They looked like the obvious noise
    measure and are exactly that on a synthesised signal - and on a real
    one they are not: 80 m at night smeared WW0WWV's energy into its
    neighbouring tones (77% in the right one, 61% by the end), the other
    three read 9 dB over the true floor, and a -9 dB station was reported
    at -21. The floor is measured away from every signal. The likelihoods
    in `_soft` keep the other pair as their noise on purpose: there it
    decodes better, because leakage is part of what a bit must survive."""
    noise = floor or 1e-12
    # All four tones, not just the one sent: what the smearing moved into
    # the neighbours is still the station's power, and counting only the
    # sent tone read 2-5 dB under wsprd on every real 80 m station.
    sig = max(float(_powers(c).sum(axis=1).mean()) - 4 * noise, 1e-12)
    return 10 * math.log10(sig / (noise * 2500 / SPACING))


def _subtract(bb: np.ndarray, start: float, f0: float, drift: float,
              symbols: np.ndarray, c: np.ndarray) -> None:
    """Take a decoded transmission out of the baseband, so the next pass
    can hear what it was sitting on. Its amplitude is measured symbol by
    symbol and smoothed over five, which follows fading without
    subtracting much of the noise along with it."""
    n0 = int(round(start))
    total = NSYM * BB_SPS
    amp = c[np.arange(NSYM), symbols] / BB_SPS
    amp = np.convolve(amp, np.ones(5) / 5, mode="same")
    t = np.arange(total) / total
    phase = 2 * np.pi * np.cumsum(f0 + drift * (t - 0.5)) / BB_RATE
    m = np.arange(BB_SPS)
    tone = np.exp(2j * np.pi * np.outer(symbols, m) * SPACING / BB_RATE)
    sig = (amp[:, None] * tone).reshape(-1) * np.exp(1j * phase)
    lo, hi = max(n0, 0), min(n0 + total, bb.size)
    if hi > lo:
        bb[lo:hi] -= sig[lo - n0:hi - n0]


def decode(audio: np.ndarray, rate: int = RATE, hashes: Hashes | None = None,
           deadline: float | None = None, known_call=None,
           passes: int = PASSES) -> list:
    """Every WSPR transmission in a slot of audio that starts at the top of
    an even minute.

    In passes: each decode is subtracted from the audio, and the next
    pass searches what is left. Two stations a few hertz apart, or a weak
    one on a strong one's skirt, are a pair the first pass can only hear
    half of."""
    if rate != RATE:
        raise ValueError(f"wspr.decode wants {RATE} Hz audio")
    return decode_baseband(_baseband(np.asarray(audio, dtype=float), rate),
                           hashes, deadline, known_call, passes)


def decode_baseband(bb: np.ndarray, hashes: Hashes | None = None,
                    deadline: float | None = None, known_call=None,
                    passes: int = PASSES) -> list:
    """`decode`, from the 375 Hz complex baseband `_baseband` makes - which
    is a sixteenth the size of the audio, and so what a recording off the
    air is kept as in the test fixtures."""
    import time as _time
    bb = np.array(bb, dtype=complex)
    found: list = []
    seen: set = set()
    for passno in range(1, passes + 1):
        power = _spectrogram(bb)
        cands, floor = _candidates(power)
        tried: set = set()
        new = 0
        for cbin, _snr_spec in cands:
            if deadline is not None and _time.monotonic() > deadline:
                return found
            sync, lag, b0, drift = _coarse(power, cbin)
            if sync < SYNC_MIN or (lag // 4, b0, drift) in tried:
                continue
            tried.add((lag // 4, b0, drift))
            got = _try(bb, lag, b0, drift, hashes, known_call, floor)
            if got is None:
                continue
            d, c, sent, start, f0 = got
            _subtract(bb, start, f0, d.drift, sent, c)
            if d.text in seen:
                continue
            seen.add(d.text)
            d.passno = passno
            found.append(d)
            new += 1
        if not new:
            break
    return found


def _try(bb, lag, b0, drift, hashes, known_call, floor):
    """Fit, demodulate and decode one candidate: the Decode and what is
    needed to subtract it, or None."""
    sync, start, f0, drift = _fine(bb, lag, b0, drift)
    c = _amps(bb, start, f0, drift)
    if c is None:
        return None
    for block in BLOCKS:
        llr = _soft(c, block)
        value, metric, cycles = stack_decode(deinterleave(llr))
        if value is not None and metric >= METRIC_MIN:
            break
    else:
        return None
    sent = channel_symbols(value)
    hard = int(((llr > 0).astype(int) != (sent >> 1)).sum())
    if hard > HARD_MAX:
        return None
    try:
        text, fields = unpack(value, hashes)
    except ValueError:
        return None
    if known_call is not None and fields.get("call") and fields["kind"] == 1 \
            and not known_call(fields["call"], fields.get("grid", "")):
        return None
    # Where the signal was when it began, as wsprd reports it and so as
    # WSPRnet has every other spot: f0 is the middle of a drifting
    # transmission, and half its drift away from the start.
    freq = CENTRE + f0 + 1.5 * SPACING - drift / 2
    d = Decode(text, fields, int(round(_snr(c, sent, floor))),
               start / BB_RATE - START, freq, drift, sync, metric, cycles,
               block)
    return d, c, sent, start, f0


# ---------------------------------------------------------------- streaming

@dataclass(frozen=True)
class _Mode:
    """What the slot machinery asks of a mode: its name and its slot."""
    name: str = "WSPR"
    slot: float = SLOT


WSPR = _Mode()


def format_line(slot_start_utc: float, d: Decode) -> str:
    """One line the way wsprd prints a spot: HHMM SNR DT FREQ DRIFT text."""
    import time
    hhmm = time.strftime("%H%M", time.gmtime(slot_start_utc))
    return (f"{hhmm} {d.snr:+3d} {d.dt:+4.1f} {d.freq:7.1f} "
            f"{d.drift:+4.1f}  {d.text}")


def _slot_decoder_class():
    # ftx imports nothing from here, and here only needs its streaming
    # class - the clock, the buffer, the thread - not its codec.
    import ftx

    class SlotDecoder(ftx.SlotDecoder):
        """WSPR from a live stream: ftx's slot machinery, a two-minute
        slot, and this module's decoder. A transmission ends 111.6 s into
        its slot, so a short wait past the end catches a late one."""

        def __init__(self, samplerate: int, clock=None, on_slot=None,
                     on_slot_start=None, threaded: bool = True,
                     known_call=None) -> None:
            super().__init__(samplerate, WSPR, clock=clock, on_slot=on_slot,
                             on_slot_start=on_slot_start, threaded=threaded,
                             known_call=known_call)
            self.wait = 0.5
            self.hashes = Hashes()

        def _run(self, audio: np.ndarray, deadline: float) -> list:
            if self.rate != RATE:
                audio = ftx.resample(audio, self.rate)
            return decode(audio, RATE, hashes=self.hashes, deadline=deadline,
                          known_call=self.known_call)

        def _line(self, slot_start: float, d) -> str:
            return format_line(slot_start, d)

    return SlotDecoder


def SlotDecoder(*args, **kwargs):
    """See `_slot_decoder_class`: built on first use, so that importing
    this module for its codec does not import ftx."""
    global SlotDecoder
    cls = _slot_decoder_class()
    SlotDecoder = cls
    return cls(*args, **kwargs)


# Who the card's mock hears, cycled by slot: (text, Hz, DT, SNR, drift).
# Invented calls in real places, so the map and the distances have
# something true to draw, and a weak one to show where the floor is.
MOCK = [
    [("K1ABC FN42 37", 1442, 0.3, -18, 0.0), ("VE7XYZ CN89 30", 1497, -0.2, -24, 1.0),
     ("JA1XYZ PM95 37", 1561, 0.8, -27, -0.5)],
    [("DL1ABC JO62 23", 1420, 0.1, -21, 0.0), ("VK3XYZ QF22 27", 1530, 1.1, -26, -1.0),
     ("W9XYZ EN52 33", 1475, 0.0, -12, 0.5)],
]


def mock_slot_audio(slot_index: int, rate: int = RATE) -> np.ndarray:
    """What the card's mock plays in a given slot."""
    return slot_audio(MOCK[slot_index % len(MOCK)], rate,
                      seed=slot_index % 97)
