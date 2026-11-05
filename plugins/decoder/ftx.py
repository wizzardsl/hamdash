"""FT8 and FT4 decoding, as pure signal processing.

The same shape as `cw.py`, `rtty.py` and `psk.py`: samples in, decodes
out, no audio device and no Flask. `tests/test_ftx.py` drives it with
WAVs that WSJT-X's own `ft8sim` made and that WSJT-X's own `jt9` decoded,
and with recordings off the air - because a round trip through this
file's encoder only proves the encoder and the decoder share a mistake.

The protocol constants are from the published FT8/FT4 descriptions; the
LDPC tables are transcribed from kgoba/ft8_lib (MIT, notice in
`ftx_tables.py`). Nothing here is from WSJT-X's source, which is GPL.
Every table was checked against WSJT-X 3.0.2's `ft8code`/`ft4code`, not
against itself.

How a slot is decoded, and why each part is the way it is:

* **Coarse sync on a spectrogram.** Half-symbol hops and half-tone bins,
  a rectangular window one symbol long - which is orthogonal to the
  neighbouring tones when it is aligned, so a strong tone does not leak
  into the next one and look like a second signal. Each place is scored
  by how much of the power in the Costas symbols sits on the Costas
  tones, block by block, so a signal that started early or runs past the
  end of the buffer still scores on the blocks it has.

* **Each candidate is fitted coherently at a low rate.** One FFT of the
  whole slot, and for each candidate the 200 Hz around it is cut out and
  brought back as complex baseband at a few hundred samples a second.
  Time and frequency are then searched finely against the Costas symbols
  with a DFT one symbol long at each tone - a matched filter where the
  spectrogram was a sieve - and the symbols are read at the best place.

* **Soft bits, belief propagation, CRC.** Each bit's metric is the best
  tone with that bit set against the best without it, normalised; the
  LDPC(174,91) code is decoded by sum-product; a codeword that satisfies
  every parity check must then also carry the right CRC-14 and must not
  be all zeros (which satisfies both and is never sent).

* **Decoded signals are subtracted and the slot is searched again.** A
  strong station hides a weaker one a few hertz away; once it is known
  exactly, its waveform is rebuilt, its amplitude followed through the
  slot, and it is taken out of the audio. The second pass finds what was
  underneath.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

import ftx_tables

RATE = 12000                    # everything below works at 12 kHz

# ---------------------------------------------------------------- modes


@dataclass(frozen=True)
class Mode:
    name: str
    sps: int                    # samples per symbol at RATE
    tones: int
    nn: int                     # channel symbols
    slot: float                 # seconds
    bt: float                   # GFSK bandwidth-time product
    sync: tuple                 # ((symbol index, (tone, ...)), ...) per block
    bits: int                   # bits per data symbol
    gray: tuple                 # bits -> tone

    @property
    def baud(self) -> float:
        return RATE / self.sps

    @property
    def spacing(self) -> float:
        return RATE / self.sps

    @property
    def data_index(self) -> np.ndarray:
        used = set()
        for start, tones in self.sync:
            used.update(range(start, start + len(tones)))
        if self.name == "FT4":
            used.update((0, self.nn - 1))       # the ramp symbols
        return np.array([i for i in range(self.nn) if i not in used])

    @property
    def degray(self) -> np.ndarray:
        inv = np.zeros(self.tones, dtype=int)
        for bits, tone in enumerate(self.gray):
            inv[tone] = bits
        return inv


_COSTAS8 = (3, 1, 4, 0, 6, 5, 2)
FT8 = Mode("FT8", 1920, 8, 79, 15.0, 2.0,
           ((0, _COSTAS8), (36, _COSTAS8), (72, _COSTAS8)),
           3, (0, 1, 3, 2, 5, 6, 4, 7))
FT4 = Mode("FT4", 576, 4, 105, 7.5, 1.0,
           ((1, (0, 1, 3, 2)), (34, (1, 0, 2, 3)),
            (67, (2, 3, 1, 0)), (100, (3, 2, 0, 1))),
           2, (0, 1, 3, 2))
MODES = {"ft8": FT8, "ft4": FT4}

# Where a signal is meant to start in its slot. jt9's DT is measured from
# here, and so is ours.
NOMINAL_START = 0.5
EARLY = {"FT8": 2.5, "FT4": 1.0}   # how early a transmission may start, s

# FT4's 77 bits are XORed with this before the CRC, so that a run of zeros
# - a blank message - does not send a run of one tone.
FT4_XOR = bytes((0x4A, 0x5E, 0x89, 0xB4, 0xB0, 0x8A, 0x79, 0x55, 0xBE, 0x28))

# ---------------------------------------------------------------- bits


def _bits(n: int, width: int) -> list:
    return [(n >> (width - 1 - i)) & 1 for i in range(width)]


def _int(bits) -> int:
    n = 0
    for b in bits:
        n = (n << 1) | int(b)
    return n


def _ft4_xor_bits() -> np.ndarray:
    out = []
    for byte in FT4_XOR:
        out.extend(_bits(byte, 8))
    return np.array(out[:77], dtype=np.uint8)


FT4_XOR_BITS = _ft4_xor_bits()

CRC_POLY = 0x2757


def crc14(bits77) -> int:
    """CRC-14 of a 77-bit payload, zero-extended to 82 bits as specified."""
    reg = 0
    for b in list(bits77) + [0] * 5:
        reg ^= int(b) << 13
        if reg & 0x2000:
            reg = ((reg << 1) ^ CRC_POLY) & 0x3FFF
        else:
            reg = (reg << 1) & 0x3FFF
    return reg


_GEN = np.array([[int(c, 16) >> (3 - k) & 1 for c in row for k in range(4)][:91]
                 for row in ftx_tables.GENERATOR], dtype=np.uint8)
_NM = np.array(ftx_tables.NM, dtype=int) - 1           # -1 is padding
_NM_MASK = _NM >= 0
_NM0 = np.where(_NM_MASK, _NM, 0)


def _mn() -> tuple[np.ndarray, np.ndarray]:
    """For each codeword bit, the three checks it is in, and for each
    (check, slot) of NM, which of the bit's three entries it is."""
    mn = [[] for _ in range(174)]
    slot = np.zeros(_NM.shape, dtype=int)
    for m in range(_NM.shape[0]):
        for j in range(_NM.shape[1]):
            n = _NM[m, j]
            if n >= 0:
                slot[m, j] = len(mn[n])
                mn[n].append(m)
    assert all(len(x) == 3 for x in mn), "every bit is in three checks"
    return np.array(mn), slot


_MN, _SLOT = _mn()


def ldpc_encode(bits91) -> np.ndarray:
    msg = np.asarray(bits91, dtype=np.uint8)
    parity = (_GEN @ msg) & 1
    return np.concatenate((msg, parity)).astype(np.uint8)


def parity_errors(codeword) -> int:
    c = np.asarray(codeword, dtype=np.uint8)
    s = np.where(_NM_MASK, c[_NM0], 0).sum(axis=1) & 1
    return int(s.sum())


def bp_decode(llr: np.ndarray, iterations: int = 30
              ) -> tuple[np.ndarray, int]:
    """Sum-product decoding of one codeword. `llr` is positive for a 1.

    Returns the hard decisions and how many parity checks they fail; 0 is
    a codeword (which the CRC then has to confirm)."""
    bits, errs = bp_decode_many(np.asarray(llr, dtype=np.float64)[None, :],
                                iterations)
    return bits[0], int(errs[0])


def bp_decode_many(llr: np.ndarray, iterations: int = 30
                   ) -> tuple[np.ndarray, np.ndarray]:
    """Sum-product decoding of a batch of codewords at once, (B, 174).

    Batched because a slot has a hundred candidates and the cost of one
    is almost all numpy's per-call overhead: one at a time it was 7 ms
    each, which is most of a slot's decoding time."""
    llr = np.asarray(llr, dtype=np.float64)
    nb = llr.shape[0]
    tov = np.zeros((nb, 174, 3))
    best = (llr > 0).astype(np.uint8)
    best_err = np.full(nb, 83)
    live = np.arange(nb)
    cols, slots = _NM0[_NM_MASK], _SLOT[_NM_MASK]
    stall = np.zeros(nb, dtype=int)
    for _ in range(iterations):
        if live.size == 0:
            break
        zn = llr[live] + tov[live].sum(axis=2)
        hard = (zn > 0).astype(np.uint8)
        err = (np.where(_NM_MASK, hard[:, _NM0], 0).sum(axis=2) & 1).sum(axis=1)
        better = err < best_err[live]
        best[live[better]] = hard[better]
        stall[live] = np.where(better, 0, stall[live] + 1)
        best_err[live] = np.minimum(best_err[live], err)
        # Done, or going nowhere: a candidate that is noise does not
        # converge, and carrying it for every iteration is most of the
        # cost of a busy slot.
        keep = (err > 0) & (stall[live] < 8)
        live = live[keep]
        zn = zn[keep]
        if live.size == 0:
            break
        tl = tov[live]
        t = zn[:, _NM0] - tl[:, _NM0, _SLOT]
        toc = np.where(_NM_MASK, np.tanh(-t / 2.0), 1.0)
        # The product of every other entry in the row: prefix times suffix,
        # rather than the whole product divided by this one, which is
        # unstable exactly when an entry is near zero.
        one = np.ones((live.size, 83, 1))
        pre = np.cumprod(np.concatenate((one, toc[:, :, :-1]), axis=2), axis=2)
        suf = np.cumprod(np.concatenate((one, toc[:, :, :0:-1]), axis=2),
                         axis=2)[:, :, ::-1]
        prod = np.clip(pre * suf, -0.999999, 0.999999)
        vals = -2.0 * np.arctanh(prod)
        tl[:, cols, slots] = vals[:, _NM_MASK]
        tov[live] = tl
    return best, best_err


_GFULL = np.hstack((np.eye(91, dtype=np.uint8), _GEN.T)).astype(np.uint8)


def osd_decode(llr: np.ndarray, order: int = 2, depth: int = 40
               ) -> tuple[np.ndarray | None, float]:
    """Ordered-statistics decoding, for when belief propagation gave up.

    The 91 most reliable bits that are independent in the code are taken
    as right, the rest re-encoded from them; then every one of them is
    flipped in turn (order 1), and pairs of the `depth` least reliable of
    them (order 2). Only codewords whose 14 CRC bits are right count, and
    of those the one nearest the received bits wins. Returns it and its
    distance - the sum of |llr| over the bits it disagrees with, as a
    share of all |llr| - which the caller must bound: tried on noise,
    something always comes nearest."""
    llr = np.asarray(llr, dtype=np.float64)
    rel = np.abs(llr)
    perm = np.argsort(-rel, kind="stable")
    g = _GFULL[:, perm].copy()
    # Gaussian elimination, taking columns in order of reliability and
    # skipping any that depend on those already taken.
    pivots = []
    row = 0
    for col in range(174):
        if row == 91:
            break
        hits = np.nonzero(g[row:, col])[0]
        if hits.size == 0:
            continue
        r = row + hits[0]
        if r != row:
            g[[row, r]] = g[[r, row]]
        others = np.nonzero(g[:, col])[0]
        others = others[others != row]
        g[others] ^= g[row]
        pivots.append(col)
        row += 1
    if row < 91:
        return None, 1.0
    hard = (llr[perm] > 0).astype(np.uint8)
    info = hard[pivots]
    base = (info @ g) & 1
    tests = [base[None, :]]
    if order >= 1:
        tests.append(base[None, :] ^ g)
    if order >= 2:
        # The least reliable of the basis are the ones most likely wrong.
        weak = np.arange(91)[-depth:]
        i, j = np.triu_indices(weak.size, 1)
        tests.append(base[None, :] ^ g[weak[i]] ^ g[weak[j]])
    cands = np.vstack(tests)
    inv = np.argsort(perm)
    words = cands[:, inv]                       # back to codeword order
    # The CRC first, since it is the cheap test that throws out nearly
    # everything; then the nearest of what is left.
    msg = words[:, :77]
    good = np.array([crc14(m) == _int(w[77:91]) and w.any()
                     for m, w in zip(msg, words)]) if words.shape[0] < 64 \
        else _crc_ok(words)
    if not good.any():
        return None, 1.0
    words = words[good]
    hard0 = (llr > 0).astype(np.uint8)
    dist = (words != hard0[None, :]) @ rel
    k = int(dist.argmin())
    return words[k], float(dist[k] / rel.sum())


def _crc_ok(words: np.ndarray) -> np.ndarray:
    """CRC-14 of many codewords at once, as a matrix product: the CRC is
    linear in the message bits, so it is the XOR of each set bit's own
    CRC."""
    return ((((words[:, :77].astype(np.int64) @ _CRC_COLS) & 1)
             == words[:, 77:91]).all(axis=1)) & words.any(axis=1)


def _crc_cols() -> np.ndarray:
    cols = np.zeros((77, 14), dtype=np.int64)
    for i in range(77):
        e = [0] * 77
        e[i] = 1
        cols[i] = _bits(crc14(e), 14)
    return cols


_CRC_COLS = _crc_cols()


# ---------------------------------------------------------------- text

A37 = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
A36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
A10 = "0123456789"
A27 = " ABCDEFGHIJKLMNOPQRSTUVWXYZ"
A38 = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ/"
A42 = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ+-./?"
NTOKENS = 2063592
MAX22 = 4194304
MAXGRID4 = 32400


def call_hash(call: str, bits: int = 22) -> int:
    """The hash WSJT-X sends for a call it cannot spell in 28 bits."""
    n = 0
    c = call.strip().upper()[:11].ljust(11)
    for ch in c:
        j = A38.find(ch)
        if j < 0:
            return -1
        n = 38 * n + j
    h = ((47055833459 * n) % (1 << 64)) >> (64 - 22)
    return h >> (22 - bits)


class Hashes:
    """Callsigns heard in full, by their 22-, 12- and 10-bit hashes.

    A hashed call can only be shown if the station has been heard spelling
    it out; until then it is `<...>`, which is what WSJT-X shows too."""

    def __init__(self, limit: int = 2000) -> None:
        self.limit = limit
        self._by = {22: {}, 12: {}, 10: {}}

    def save(self, call: str) -> None:
        call = call.strip().strip("<>")
        if not call or call.startswith("CQ") or len(call) < 3:
            return
        h = call_hash(call)
        if h < 0:
            return
        for bits in (22, 12, 10):
            table = self._by[bits]
            table.pop(h >> (22 - bits), None)
            table[h >> (22 - bits)] = call
            while len(table) > self.limit:
                table.pop(next(iter(table)))

    def lookup(self, h: int, bits: int) -> str:
        call = self._by[bits].get(h)
        return f"<{call}>" if call else "<...>"


def _basecall_n(call: str) -> int:
    """A standard callsign's 28-bit number before the token offset, or -1."""
    c = call
    if c.startswith("3DA0") and 4 < len(c) <= 7:
        c = "3D0" + c[4:]
    elif c.startswith("3X") and len(c) > 2 and c[2].isalpha() and len(c) <= 7:
        c = "Q" + c[2:]
    if len(c) > 2 and c[2].isdigit() and len(c) <= 6:
        c6 = c.ljust(6)
    elif len(c) > 1 and c[1].isdigit() and len(c) <= 5:
        c6 = (" " + c).ljust(6)
    else:
        return -1
    idx = (A37.find(c6[0]), A36.find(c6[1]), A10.find(c6[2]),
           A27.find(c6[3]), A27.find(c6[4]), A27.find(c6[5]))
    if min(idx) < 0:
        return -1
    n = idx[0]
    for v, base in zip(idx[1:], (36, 10, 27, 27, 27)):
        n = n * base + v
    return n


def _pack28(word: str) -> tuple[int, int]:
    """(n28, ip) for a call or token; ip is the /R or /P bit."""
    if word == "DE":
        return 0, 0
    if word == "QRZ":
        return 1, 0
    if word == "CQ":
        return 2, 0
    if word.startswith("CQ_") or word.startswith("CQ "):
        arg = word[3:]
        if arg.isdigit() and len(arg) == 3:
            return 3 + int(arg), 0
        if arg.isalpha() and len(arg) <= 4:
            n = 0
            for ch in arg.rjust(4):
                n = n * 27 + A27.find(ch)
            return 1003 + n, 0
    ip = 0
    base = word
    if word.endswith("/R") or word.endswith("/P"):
        ip, base = 1, word[:-2]
    if base.startswith("<") and base.endswith(">"):
        return NTOKENS + call_hash(base[1:-1]), ip
    n = _basecall_n(base)
    if n >= 0:
        return NTOKENS + MAX22 + n, ip
    return NTOKENS + call_hash(base), ip


def _pack_grid_or_report(extra: str) -> tuple[int, int]:
    """(ir, igrid4) for the third field of a standard message."""
    if extra == "":
        return 0, MAXGRID4 + 1
    if extra == "RRR":
        return 0, MAXGRID4 + 2
    if extra == "RR73":
        # WSJT-X sends RR73 as the grid square it also is (in the Arctic
        # Ocean), not as the token reserved for it; ft8code says so.
        return 0, ((17 * 18 + 17) * 100) + 73
    if extra == "73":
        return 0, MAXGRID4 + 4
    ir = 0
    if extra.startswith("R") and len(extra) > 1 and extra[1] in "+-":
        ir, extra = 1, extra[1:]
    elif extra.startswith("R ") :
        ir, extra = 1, extra[2:]
    if extra and extra[0] in "+-":
        return ir, MAXGRID4 + 35 + int(extra)
    if (len(extra) == 4 and "A" <= extra[0] <= "R" and "A" <= extra[1] <= "R"
            and extra[2:].isdigit()):
        g = ((ord(extra[0]) - 65) * 18 + ord(extra[1]) - 65) * 100 \
            + int(extra[2:])
        return ir, g
    raise ValueError(f"not a grid or a report: {extra!r}")


def _is_grid(s: str) -> bool:
    return (len(s) == 4 and "A" <= s[0] <= "R" and "A" <= s[1] <= "R"
            and s[2:].isdigit() and s != "RR73")


def _is_extra(s: str) -> bool:
    if s in ("RRR", "RR73", "73") or _is_grid(s):
        return True
    if s.startswith("R ") and _is_grid(s[2:]):
        return True
    t = s[1:] if s.startswith("R") and len(s) > 1 and s[1] in "+-" else s
    return len(t) >= 2 and t[0] in "+-" and t[1:].isdigit() \
        and -30 <= int(t) <= 99


def _callish(w: str) -> bool:
    """Could be a callsign: letters and digits, with a digit, maybe a /."""
    w = w.strip("<>")
    return (3 <= len(w) <= 11 and any(c.isdigit() for c in w)
            and any(c.isalpha() for c in w)
            and all(c in A38[1:] for c in w))


def _type4_ok(a: str, b: str) -> bool:
    if a == "CQ":
        return _callish(b) and _basecall_n(b) < 0
    if not (_callish(a) and _callish(b)):
        return False
    # Exactly one of them is the one that will not fit in 28 bits.
    return (_basecall_n(a.strip("<>")) < 0) != (_basecall_n(b.strip("<>")) < 0)


def pack_free(text: str) -> list:
    t = text.upper()[:13].rjust(13)
    n = 0
    for ch in t:
        j = A42.find(ch)
        if j < 0:
            raise ValueError(f"cannot send {ch!r} in free text")
        n = n * 42 + j
    return _bits(n, 71) + [0, 0, 0] + [0, 0, 0]


def pack77(text: str) -> list:
    """Source-encode a message to 77 bits: standard, /R and /P,
    nonstandard calls (as WSJT-X does them) and free text."""
    words = text.upper().split()
    # CQ with a modifier is one field: "CQ DX K1ABC FN42", "CQ 145 ...".
    # A callsign always has a digit in it, so letters alone are a modifier.
    if len(words) >= 3 and words[0] == "CQ" and (
            (words[1].isdigit() and len(words[1]) == 3)
            or (words[1].isalpha() and len(words[1]) <= 4)):
        words = ["CQ_" + words[1]] + words[2:]
    if len(words) == 4 and words[2] == "R" and _is_grid(words[3]):
        words = [words[0], words[1], "R " + words[3]]
    std = None
    if len(words) in (2, 3):
        extra = words[2] if len(words) == 3 else ""
        a, b = words[0], words[1]
        if (extra == "" or _is_extra(extra)) and not (a.startswith("<") and b.startswith("<")):
            na, ipa = _pack28(a)
            nb, ipb = _pack28(b)
            bare = [w[:-2] if w.endswith(("/R", "/P")) else w for w in (a, b)]
            plain = all(w in ("DE", "QRZ", "CQ") or w.startswith("CQ_")
                        or w.startswith("<") or _basecall_n(w) >= 0
                        for w in bare)
            if plain:
                i3 = 2 if (a.endswith("/P") or b.endswith("/P")) else 1
                ir, g = _pack_grid_or_report(extra)
                std = (_bits(na, 28) + [ipa] + _bits(nb, 28) + [ipb]
                       + [ir] + _bits(g, 15) + _bits(i3, 3))
    if std is not None:
        return std
    # One call that will not fit 28 bits: type 4, the long one spelled out
    # in 58 bits and the other hashed in 12.
    if 2 <= len(words) <= 3:
        a, b = words[0], words[1]
        extra = words[2] if len(words) == 3 else ""
        nrpt = {"": 0, "RRR": 1, "RR73": 2, "73": 3}.get(extra)
        if nrpt is not None and _type4_ok(a, b):
            icq = 1 if a == "CQ" else 0
            if icq:
                long_, short, iflip = b, "", 0
            elif _basecall_n(a.strip("<>")) < 0 and not a.startswith("<"):
                long_, short, iflip = a, b, 1
            else:
                long_, short, iflip = b, a, 0
            n58 = 0
            for ch in long_.strip("<>").rjust(11)[:11]:
                n58 = n58 * 38 + A38.find(ch)
            # A CQ carries its own call's hash too, so a station that
            # hears it can show the call when it is later sent hashed.
            n12 = call_hash((short or long_).strip("<>"), 12)
            return (_bits(n12, 12) + _bits(n58, 58) + [iflip] + _bits(nrpt, 2)
                    + [icq] + _bits(4, 3))
    return pack_free(text)


def _unpack28(n28: int, ip: int, i3: int, hashes: Hashes | None) -> str:
    if n28 < NTOKENS:
        if n28 == 0:
            return "DE"
        if n28 == 1:
            return "QRZ"
        if n28 == 2:
            return "CQ"
        if n28 <= 1002:
            return f"CQ {n28 - 3:03d}"
        if n28 <= 532443:
            n = n28 - 1003
            s = ""
            for _ in range(4):
                s = A27[n % 27] + s
                n //= 27
            return "CQ " + s.strip()
        raise ValueError("unknown token")
    n28 -= NTOKENS
    if n28 < MAX22:
        return hashes.lookup(n28, 22) if hashes else "<...>"
    n = n28 - MAX22
    c = []
    for base, alpha in ((27, A27), (27, A27), (27, A27), (10, A10),
                        (36, A36), (37, A37)):
        c.append(alpha[n % base])
        n //= base
    if n:
        raise ValueError("callsign out of range")
    call = "".join(reversed(c))
    if call.startswith("3D0") and call[3] != " ":
        call = "3DA0" + call[3:].strip()
    elif call[0] == "Q" and call[1].isalpha():
        call = "3X" + call[1:].strip()
    else:
        call = call.strip()
    if len(call) < 3 or " " in call:
        raise ValueError("not a callsign")
    if hashes is not None:
        hashes.save(call)
    if ip:
        call += "/R" if i3 == 1 else "/P"
    return call


def _grid_or_report(ir: int, g: int) -> str:
    if g <= MAXGRID4:
        n = g
        d2 = n % 10
        n //= 10
        d1 = n % 10
        n //= 10
        b = n % 18
        a = n // 18
        if a >= 18:
            raise ValueError("grid out of range")
        grid = f"{chr(65 + a)}{chr(65 + b)}{d1}{d2}"
        return f"R {grid}" if ir else grid
    r = g - MAXGRID4
    if r == 1:
        return ""
    if r == 2:
        return "RRR"
    if r == 3:
        return "RR73"
    if r == 4:
        return "73"
    dd = r - 35
    return f"{'R' if ir else ''}{dd:+03d}"


def unpack77(bits77, hashes: Hashes | None = None) -> tuple[str, dict]:
    """(text, fields) for a 77-bit payload. `fields` carries what a
    program would want without parsing the text: `call` (the station
    transmitting), `to`, `grid`, `report`, `kind`."""
    b = [int(x) for x in bits77]
    i3 = _int(b[74:77])
    if i3 in (1, 2):
        na, ipa = _int(b[0:28]), b[28]
        nb, ipb = _int(b[29:57]), b[57]
        ir, g = b[58], _int(b[59:74])
        to = _unpack28(na, ipa, i3, hashes)
        de = _unpack28(nb, ipb, i3, hashes)
        extra = _grid_or_report(ir, g)
        text = " ".join(x for x in (to, de, extra) if x)
        f = {"kind": "standard", "to": to, "call": de}
        if extra in ("RRR", "RR73", "73"):
            f["ack"] = extra
        elif g <= MAXGRID4:
            f["grid"] = extra[-4:]
        elif extra:
            f["report"] = int(extra[1:] if extra[0] == "R" else extra)
        if ir:
            f["roger"] = True
        return text, f
    if i3 == 4:
        n12, n58 = _int(b[0:12]), _int(b[12:70])
        iflip, nrpt, icq = b[70], _int(b[71:73]), b[73]
        c = []
        for _ in range(11):
            c.append(A38[n58 % 38])
            n58 //= 38
        long_ = "".join(reversed(c)).strip()
        if hashes is not None:
            hashes.save(long_)
        short = hashes.lookup(n12, 12) if hashes else "<...>"
        if icq:
            return f"CQ {long_}", {"kind": "nonstandard", "to": "CQ",
                                   "call": long_}
        to, de = (long_, short) if iflip else (short, long_)
        extra = ("", "RRR", "RR73", "73")[nrpt]
        text = " ".join(x for x in (to, de, extra) if x)
        f = {"kind": "nonstandard", "to": to, "call": de}
        if extra:
            f["ack"] = extra
        return text, f
    if i3 == 0:
        n3 = _int(b[71:74])
        if n3 == 0:
            n = _int(b[0:71])
            s = ""
            for _ in range(13):
                s = A42[n % 42] + s
                n //= 42
            return s.strip(), {"kind": "free"}
        if n3 == 1:
            a = _unpack28(_int(b[0:28]), 0, 1, hashes)
            c = _unpack28(_int(b[28:56]), 0, 1, hashes)
            h10 = _int(b[56:66])
            rpt = 2 * _int(b[66:71]) - 30
            h = hashes.lookup(h10, 10) if hashes else "<...>"
            return (f"{a} RR73; {c} {h} {rpt:+03d}",
                    {"kind": "dxpedition", "call": h, "to": c,
                     "report": rpt})
        if n3 == 5:
            return f"{_int(b[0:71]):x}".upper(), {"kind": "telemetry"}
    # Field day, RTTY roundup and EU VHF contest messages: decoded
    # (the CRC passed), but not spelled out here.
    return f"[message type {i3}{'.' + str(_int(b[71:74])) if i3 == 0 else ''}]", \
        {"kind": "other"}


# ---------------------------------------------------------------- tones


def symbols_for(bits77, mode: Mode = FT8) -> np.ndarray:
    """Channel tones for a 77-bit payload."""
    payload = np.asarray(bits77, dtype=np.uint8)
    if mode is FT4:
        payload = payload ^ FT4_XOR_BITS
    crc = crc14(payload)
    cw = ldpc_encode(np.concatenate((payload, _bits(crc, 14))))
    out = np.zeros(mode.nn, dtype=int)
    for start, tones in mode.sync:
        out[start:start + len(tones)] = tones
    k = mode.bits
    vals = cw.reshape(-1, k) @ (1 << np.arange(k - 1, -1, -1))
    out[mode.data_index] = np.array(mode.gray)[vals]
    return out


def message_symbols(text: str, mode: Mode = FT8) -> np.ndarray:
    return symbols_for(pack77(text), mode)


def _gfsk_pulse(mode: Mode, sps: int) -> np.ndarray:
    """The frequency pulse of one symbol: a rectangle one symbol wide,
    smoothed by a Gaussian of the mode's BT, spanning three symbols."""
    t = (np.arange(3 * sps) - 1.5 * sps + 0.5) / sps
    k = math.pi * math.sqrt(2.0 / math.log(2.0))
    from math import erf
    return np.array([0.5 * (erf(k * mode.bt * (x + 0.5))
                            - erf(k * mode.bt * (x - 0.5))) for x in t])


def frequency_track(symbols, mode: Mode, sps: int) -> np.ndarray:
    """Instantaneous tone number, sample by sample, over the transmission."""
    pulse = _gfsk_pulse(mode, sps)
    n = len(symbols)
    track = np.zeros((n + 2) * sps)
    for i, s in enumerate(symbols):
        track[i * sps:i * sps + 3 * sps] += s * pulse
    # The first and last symbols are extended rather than ramped from zero,
    # so the transmission starts on its first tone.
    track[:2 * sps] += symbols[0] * pulse[sps:]
    track[n * sps:] += symbols[-1] * pulse[:2 * sps]
    return track[sps:sps + n * sps]


def synth(symbols, f0: float, mode: Mode = FT8, rate: int = RATE,
          amplitude: float = 1.0, complex_out: bool = False) -> np.ndarray:
    """A GFSK transmission of `symbols` with tone 0 at f0, starting at
    sample 0. The ends are ramped, as a real transmitter's are, so it
    does not click."""
    sps = int(round(mode.sps * rate / RATE))
    track = frequency_track(symbols, mode, sps)
    phase = 2 * np.pi * np.cumsum(f0 + track * mode.spacing) / rate
    env = np.ones(phase.size)
    # FT8 ramps over an eighth of a symbol; FT4 has a whole symbol at each
    # end for it, which is what its two extra symbols are for.
    ramp = sps if mode is FT4 else max(1, sps // 8)
    edge = 0.5 * (1 - np.cos(np.pi * np.arange(ramp) / ramp))
    env[:ramp] = edge
    env[-ramp:] = edge[::-1]
    if complex_out:
        return amplitude * env * np.exp(1j * phase)
    return (amplitude * env * np.sin(phase)).astype(np.float32)


def slot_audio(messages, mode: Mode = FT8, rate: int = RATE,
               noise: bool = True, seed: int = 0) -> np.ndarray:
    """One slot of audio from [(text, f0, dt, snr_db)]. Noise is white at
    unit variance and SNR is in 2500 Hz, as WSJT-X quotes it."""
    rng = np.random.default_rng(seed)
    n = int(mode.slot * rate)
    out = np.zeros(n)
    for text, f0, dt, snr in messages:
        s = synth(message_symbols(text, mode), f0, mode, rate)
        # A sine of amplitude a has power a^2/2; unit noise over rate/2 has
        # 2500/(rate/2) of its power in 2.5 kHz.
        a = math.sqrt(2 * 10 ** (snr / 10) * 2500 / (rate / 2))
        s = s.astype(np.float64) * a
        at = int(round((NOMINAL_START + dt) * rate))
        lo, hi = max(at, 0), min(at + s.size, n)
        if hi > lo:
            out[lo:hi] += s[lo - at:hi - at]
    if noise:
        out += rng.standard_normal(n)
    peak = np.abs(out).max() or 1.0
    return (out / peak * 0.5).astype(np.float32)


# ---------------------------------------------------------------- decoding


@dataclass
class Decode:
    mode: str
    text: str
    fields: dict
    snr: int
    dt: float               # seconds after the nominal start
    freq: float             # audio frequency of tone 0, Hz
    sync: float = 0.0
    passno: int = 1
    osd: float = 0.0          # >0 if found by OSD, its distance
    ap: str = ""              # "a1" (CQ) or "a2" (my call) if a-priori
    symbols: np.ndarray = field(default=None, repr=False)

    def as_dict(self) -> dict:
        out = {"mode": self.mode, "text": self.text, "snr": self.snr,
               "dt": round(self.dt, 2), "freq": round(self.freq, 1),
               **self.fields}
        if self.ap:
            out["ap"] = self.ap
        return out


def resample(audio: np.ndarray, rate: int) -> np.ndarray:
    """To 12 kHz, by FFT. A whole slot at once, so the edges do not matter
    and any ratio works - a 44.1 kHz interface hands this 14700."""
    audio = np.asarray(audio, dtype=np.float64)
    if rate == RATE:
        return audio
    n_out = int(round(audio.size * RATE / rate))
    spec = np.fft.rfft(audio)
    keep = n_out // 2 + 1
    if keep <= spec.size:
        spec = spec[:keep]
    else:
        spec = np.concatenate((spec, np.zeros(keep - spec.size)))
    return np.fft.irfft(spec, n_out) * (n_out / audio.size)


def _fft_length(n: int) -> int:
    """The smallest length >= n that is a multiple of 180 and has no prime
    factor above 5. 180 makes the baseband decimation exact for both modes
    (FT8 keeps 1 bin in 60, FT4 1 in 36), so a symbol is a whole number of
    baseband samples; 5-smooth keeps numpy's FFT fast."""
    m = max(180, -(-n // 180) * 180)
    while True:
        k = m
        for p in (2, 3, 5):
            while k % p == 0:
                k //= p
        if k == 1:
            return m
        m += 180


class Slot:
    """The working state for one slot: audio, its spectrum, the hashes."""

    def __init__(self, audio: np.ndarray, mode: Mode) -> None:
        self.mode = mode
        self.audio = np.asarray(audio, dtype=np.float64).copy()
        self.respectrum()

    def respectrum(self) -> None:
        # Long enough for the slot plus room to search past its end; a
        # length that factors well, because numpy's FFT likes it.
        self.nfft = _fft_length(self.audio.size)
        self.spec = np.fft.rfft(self.audio, self.nfft)


# Tunables, each with the reason it is what it is in the tests.
MAX_CANDIDATES = 120         # per pass; LDPC weeds out the false ones
SYNC_MIN = 1.6               # Costas tones over the other tones, per symbol
PASSES = 3
LLR_SCALE = 2.83
SNR_SPAN_HZ = 100            # either side of a signal, for its noise
# Bit metrics over this many symbols, tried in turn. FT4's tones are
# three times as wide and a third as long, so four of its symbols span
# less time than two of FT8's.
NSYM = {"FT8": (1, 3, 2), "FT4": (1, 4, 2)}
OSD_MAX = 40                # candidates per pass given to OSD
OSD_ORDER = 2
OSD_DEPTH = 40
# OSD tries hundreds of codewords a candidate, so its CRC passes by
# chance far more often than BP's does, and the distance is what tells a
# real find from a lucky one. Simulated bands are kinder than real ones:
# over 70 busy simulated slots every false find was at 0.073 or more,
# but eight slots of 40 m at night put five at 0.050-0.065 - all with a
# sync under 4.8, and mostly from passes 2 and 3, out of what subtraction
# left behind. The real finds there were 0.014-0.042 at any sync, or up
# to 0.063 with a sync of 6 or more. Hence two bounds.
OSD_SYNC_MIN = 3.0
OSD_SURE_DIST = 0.045       # of all |llr|: accepted at any sync
OSD_MAX_DIST = 0.065        # accepted only with a sync of OSD_FAR_SYNC
OSD_FAR_SYNC = 5.5
# FT4 has its own, stricter: its first real-air test (80 slots on 80 and
# 40 m, where jt9 found no FT4 at all) gave one OSD decode, at 0.038 and
# -14 dB, on somebody else's signal - "GM3HPN K29DAB R OL68". True FT4 OSD
# decodes in the simulator sit at 0.031-0.044, so distance cannot tell the
# two apart above 0.030; a few weak decodes are the price.
OSD_LIMITS = {"FT8": (OSD_SURE_DIST, OSD_MAX_DIST), "FT4": (0.030, 0.030)}
FIT_MIN = 1.2               # the fitted sync metric, below which not to try
# A-priori decoding: what is already known about a message, pinned before
# belief propagation is run again. See _ap_patterns.
AP_SYNC_MIN = 2.0           # below this a candidate is noise, AP or not
AP_MAX = 60                 # candidates per pass
AP_MAX_HARD = 36            # bits the unpinned metrics got wrong, at most
LDPC_ITERATIONS = 30


def _spectrogram(audio: np.ndarray, mode: Mode) -> np.ndarray:
    """Power, in half-symbol hops and half-tone bins."""
    sps = mode.sps
    hop = sps // 2
    nfft = 2 * sps
    n = (audio.size - sps) // hop + 1
    if n <= 0:
        return np.zeros((0, nfft // 2 + 1))
    idx = np.arange(sps)[None, :] + hop * np.arange(n)[:, None]
    frames = audio[idx]
    spec = np.fft.rfft(frames, nfft, axis=1)
    return (spec.real ** 2 + spec.imag ** 2)


def _candidates(power: np.ndarray, mode: Mode, f_lo: float, f_hi: float,
                limit: int) -> list:
    """(score, start sample, f0) of the best places, strongest first."""
    hop = mode.sps // 2
    binhz = mode.spacing / 2
    nt, nf = power.shape
    span = 2 * mode.tones            # bins covered by the tones
    b_lo = max(0, int(f_lo / binhz))
    b_hi = min(nf - span, int(f_hi / binhz))
    if b_hi <= b_lo:
        return []
    # Sum over all tones of each place, once: total[t, b] covers bins
    # b, b+2, ... b+2*(tones-1).
    total = np.zeros((nt, b_hi - b_lo))
    for k in range(mode.tones):
        total += power[:, b_lo + 2 * k:b_hi + 2 * k]
    # Start offsets: from EARLY before the nominal start to well after. A
    # station whose clock is fast starts before the slot does, and is
    # decoded from what is left - the first Costas block and a few symbols
    # are missing, and the code corrects for them as for any erasure. At a
    # second, as this was, real 40 m stations at DT -1.1 and -2.1 were
    # missed every slot at up to +1 dB, while jt9 read them.
    nominal = int(round(NOMINAL_START * RATE / hop))
    t_lo = nominal - int(round(EARLY[mode.name] * RATE / hop))
    t_hi = nominal + int(round(2.5 * RATE / hop))
    ts = np.arange(t_lo, t_hi + 1)
    sig = np.zeros((ts.size, b_hi - b_lo))
    tot = np.zeros_like(sig)
    for start, tones in mode.sync:
        for j, tone in enumerate(tones):
            rows = ts + 2 * (start + j)
            ok = (rows >= 0) & (rows < nt)
            if not ok.any():
                continue
            r = rows[ok]
            sig[ok] += power[r, b_lo + 2 * tone:b_hi + 2 * tone]
            tot[ok] += total[r]
    other = (tot - sig) / (mode.tones - 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        score = np.where(other > 0, sig / other, 0.0)
    # Local maxima only, so one signal is one candidate and not a smear of
    # its neighbouring bins and hops.
    pad = np.pad(score, 1, constant_values=-1)
    peak = np.ones_like(score, dtype=bool)
    for dt in (-1, 0, 1):
        for db in (-1, 0, 1):
            if dt == 0 and db == 0:
                continue
            peak &= score >= pad[1 + dt:1 + dt + score.shape[0],
                                 1 + db:1 + db + score.shape[1]]
    peak &= score >= SYNC_MIN
    ti, bi = np.nonzero(peak)
    order = np.argsort(-score[ti, bi])[:limit]
    return [(float(score[ti[i], bi[i]]), int(ts[ti[i]]) * hop,
             (b_lo + bi[i]) * binhz) for i in order]


class _Baseband:
    """The few hundred hertz around one candidate, as complex samples."""

    def __init__(self, slot: Slot, f0: float) -> None:
        mode = slot.mode
        # A symbol of 32 samples for FT8, 16 for FT4 - decimation is
        # chosen so a symbol is a whole number of samples.
        self.ns = 32 if mode is FT8 else 16
        self.rate = RATE * self.ns / mode.sps
        binhz = RATE / slot.nfft
        centre = f0 + mode.spacing * (mode.tones - 1) / 2
        nb = slot.nfft * self.ns // mode.sps
        c = int(round(centre / binhz))
        lo = c - nb // 2
        idx = np.arange(lo, lo + nb)
        ok = (idx >= 0) & (idx < slot.spec.size)
        band = np.zeros(nb, dtype=complex)
        band[ok] = slot.spec[idx[ok]]
        # Taper the band edges so a strong neighbour cut in half does not
        # ring through the whole slot.
        taper = int(nb * 0.1)
        w = np.ones(nb)
        w[:taper] = 0.5 * (1 - np.cos(np.pi * np.arange(taper) / taper))
        w[-taper:] = w[:taper][::-1]
        band *= w
        self.centre_hz = c * binhz
        z = np.fft.ifft(np.fft.ifftshift(band)) * nb / slot.nfft * 2
        self.z = z
        self.step = slot.nfft / nb                 # audio samples per sample
        # Tone k sits at this baseband frequency.
        self.tone_hz = (f0 + mode.spacing * np.arange(mode.tones)
                        - self.centre_hz)


def _tone_amps(bb: _Baseband, mode: Mode, start: float, df: float,
               symbols_idx: np.ndarray) -> np.ndarray:
    """Complex amplitude of every tone in each listed symbol, for a
    transmission starting at audio sample `start`, offset `df` Hz."""
    ns = bb.ns
    s0 = start / bb.step
    i0 = int(math.floor(s0 + 0.5))
    pos = i0 + symbols_idx[:, None] * ns + np.arange(ns)[None, :]
    ok = (pos >= 0) & (pos < bb.z.size)
    seg = np.where(ok, bb.z[np.clip(pos, 0, bb.z.size - 1)], 0)
    t = np.arange(ns) / bb.rate
    f = bb.tone_hz + df
    e = np.exp(-2j * np.pi * f[None, :] * t[:, None])        # (ns, tones)
    amps = seg @ e                                           # (n, tones)
    # Referred to the start of the transmission rather than of each
    # symbol, so the phase carries on from one symbol to the next the
    # way the transmitter's does, and neighbours can be added coherently.
    turn = np.exp(-2j * np.pi * f[None, :] * (symbols_idx[:, None] * ns
                                              / bb.rate))
    return amps * turn


def _sync_metric(amps: np.ndarray, mode: Mode, tones: np.ndarray) -> float:
    p = amps.real ** 2 + amps.imag ** 2
    sig = p[np.arange(tones.size), tones].sum()
    return sig / max(p.sum() - sig, 1e-30) * (mode.tones - 1)


def _sync_rows(mode: Mode):
    idx, tones = [], []
    for start, ts in mode.sync:
        for j, tone in enumerate(ts):
            tones.append(tone)
            idx.append(start + j)
    return np.array(idx), np.array(tones)


def _fit(slot: Slot, start: int, f0: float):
    """Search time and frequency finely around a candidate.

    Every time offset is tried at once, a baseband sample apart (1/32 of
    an FT8 symbol), for each of a handful of frequency offsets; then the
    frequency again, finer, and the time to a fraction of a sample by a
    parabola through the peak - which is what the subtraction needs."""
    mode = slot.mode
    bb = _Baseband(slot, f0)
    sidx, stones = _sync_rows(mode)
    ns = bb.ns
    i0 = int(round(start / bb.step))
    offs = np.arange(-ns // 2, ns // 2 + 1)
    pos = (i0 + offs[:, None, None] + sidx[None, :, None] * ns
           + np.arange(ns)[None, None, :])
    ok = (pos >= 0) & (pos < bb.z.size)
    seg = np.where(ok, bb.z[np.clip(pos, 0, bb.z.size - 1)], 0)
    t = np.arange(ns) / bb.rate
    rows = np.arange(sidx.size)

    blocks = np.cumsum([0] + [len(tn) for _, tn in mode.sync])

    def metric(df):
        f = bb.tone_hz + df
        e = np.exp(-2j * np.pi * f[None, :] * t[:, None])
        a = seg @ e                                     # (offs, S, tones)
        p = a.real ** 2 + a.imag ** 2
        sig = p[:, rows, stones].sum(axis=1)
        tot = p.sum(axis=(1, 2))
        plain = sig / np.maximum(tot - sig, 1e-30) * (mode.tones - 1)
        # Each Costas block added coherently: the tones are phase-
        # continuous, so the right time and frequency make the block's
        # four or seven symbols line up in phase and nothing else does.
        # That is what picks the place; summed as powers, sixteen FT4
        # sync symbols at -16 dB left it 70 samples and 4 Hz out.
        turn = np.exp(-2j * np.pi * f[stones][None, :]
                      * (sidx * ns / bb.rate)[None, :])
        c = a[:, rows, stones] * turn                   # (offs, S)
        coh = sum(np.abs(c[:, blocks[i]:blocks[i + 1]].sum(axis=1)) ** 2
                  for i in range(len(mode.sync)))
        return plain, coh / np.maximum((tot - sig) / (mode.tones - 1), 1e-30)

    best = (-1.0, 0, 0.0, None, None)
    # Coarse sync was a quarter symbol and a quarter tone out at worst.
    for df in np.arange(-0.375, 0.376, 0.0625) * mode.spacing:
        plain, coh = metric(df)
        k = int(coh.argmax())
        if coh[k] > best[0]:
            best = (float(coh[k]), k, df, coh, plain)
    df0 = best[2]
    for df in df0 + np.arange(-0.03125, 0.0313, 0.015625) * mode.spacing:
        plain, coh = metric(df)
        k = int(coh.argmax())
        if coh[k] > best[0]:
            best = (float(coh[k]), k, df, coh, plain)
    _, k, df, m, plain = best
    peak = float(plain[k])
    frac = 0.0
    if 0 < k < m.size - 1:
        den = m[k - 1] - 2 * m[k] + m[k + 1]
        if den < 0:
            frac = float(np.clip(0.5 * (m[k - 1] - m[k + 1]) / den, -0.5, 0.5))
    s_best = (i0 + offs[k] + frac) * bb.step
    return bb, (peak, s_best, df)


_GROUPS: dict = {}


def _groups(mode: Mode, nsym: int):
    """For bit metrics over `nsym` symbols: [(combos, masks, idx)] per
    group length, where idx is (groups, length) into the data symbols,
    combos every tone sequence of that length, and masks which sequences
    have each bit set. Coherent only within a run of data symbols, never
    across a sync block."""
    key = (mode.name, nsym)
    if key in _GROUPS:
        return _GROUPS[key]
    data = mode.data_index
    runs, start = [], 0
    for i in range(1, data.size + 1):
        if i == data.size or data[i] != data[i - 1] + 1:
            runs.append((start, i))
            start = i
    by_len: dict = {}
    for lo, hi in runs:
        for g0 in range(lo, hi, nsym):
            g = list(range(g0, min(g0 + nsym, hi)))
            by_len.setdefault(len(g), []).append(g)
    k = mode.bits
    out = []
    for n, gs in sorted(by_len.items()):
        combos = np.array(np.meshgrid(*[np.arange(mode.tones)] * n,
                                      indexing="ij")).reshape(n, -1).T
        bitsof = mode.degray[combos]                      # (C, n)
        masks = np.array([((bitsof[:, j] >> (k - 1 - b)) & 1).astype(bool)
                          for j in range(n) for b in range(k)])   # (n*k, C)
        out.append((combos, masks, np.array(gs)))
    _GROUPS[key] = out
    return out


def _llr(amps: np.ndarray, mode: Mode, nsym: int = 1) -> np.ndarray:
    """Bit metrics, positive for a 1, from the data symbols' complex tone
    amplitudes - each bit judged over `nsym` symbols taken together.

    The tones are phase-continuous, so over three symbols the signal is
    one of 512 known waveforms and the right one adds up coherently where
    noise does not: measured on ft8sim's own signals, that took -20 dB
    from 4 decodes in 30 to 10. A signal that fades or drifts within
    three symbols is better read one at a time, which is why both are
    tried."""
    k = mode.bits
    metric = np.zeros((amps.shape[0], k))
    for combos, masks, idx in _groups(mode, nsym):
        n = combos.shape[1]
        a = amps[idx]                                     # (G, n, tones)
        tot = sum(a[:, j, combos[:, j]] for j in range(n))   # (G, C)
        mag = np.abs(tot)[:, None, :]
        one = np.where(masks[None], mag, -np.inf).max(axis=2)
        zero = np.where(~masks[None], mag, -np.inf).max(axis=2)
        metric[idx.reshape(-1)] = (one - zero).reshape(-1, n, k).reshape(-1, k)
    llr = metric.reshape(-1)
    sd = llr.std()
    return llr / sd * LLR_SCALE if sd > 0 else llr


def _noise_floor(audio: np.ndarray, mode: Mode) -> np.ndarray:
    """Noise power per spectrogram bin, in the units of a rectangular DFT
    one symbol long, from the quietest quarter of the slot in each bin.

    Through a Hann window, not the rectangle the sync search uses: the
    rectangle's sidelobes fall off slowly, and a strong signal's lifted
    every bin near it far enough to read -5 dB as -9. A signal holds a
    bin for a fraction of its symbols, so the 25th percentile over time
    is noise even on a busy band, and per bin it follows the radio's
    passband, which is not flat."""
    sps = mode.sps
    hop = sps // 2
    n = (audio.size - sps) // hop + 1
    if n <= 0:
        return np.ones(sps + 1)
    idx = np.arange(sps)[None, :] + hop * np.arange(n)[:, None]
    win = np.hanning(sps)
    spec = np.fft.rfft(audio[idx] * win, 2 * sps, axis=1)
    power = spec.real ** 2 + spec.imag ** 2
    # Leave out frames that are all padding.
    live = power.sum(axis=1) > 0
    if live.sum() < 8:
        return np.ones(power.shape[1])
    # For exponentially distributed power the 25th percentile is ln(4/3)
    # of the mean; the window's own noise gain is sum(w^2) against sps.
    q = np.percentile(power[live], 25, axis=0) / math.log(4 / 3)
    return q * sps / float(np.sum(win ** 2))


def _snr(slot: Slot, sym: np.ndarray, start: float, freq: float) -> float:
    """SNR in 2500 Hz, the way WSJT-X quotes it.

    The signal is measured at full rate with a DFT a symbol long at each
    sent tone, which is the same measurement the spectrogram's bins make
    of the noise - so the two are in the same units and the ratio needs
    only the bandwidth. The noise was the median of the *other* tones at
    first, and a strong signal's own skirts raised it: -5 dB read -8."""
    mode = slot.mode
    sps = mode.sps
    s0 = int(round(start))
    idx = s0 + np.arange(sym.size)[:, None] * sps + np.arange(sps)[None, :]
    ok = (idx >= 0) & (idx < slot.audio.size)
    seg = np.where(ok, slot.audio[np.clip(idx, 0, slot.audio.size - 1)], 0)
    f = freq + sym * mode.spacing
    t = np.arange(sps) / RATE
    amp = np.einsum("ni,ni->n", seg, np.exp(-2j * np.pi * f[:, None] * t))
    sig = float(np.mean(amp.real ** 2 + amp.imag ** 2))
    nf = slot.noise
    binhz = mode.spacing / 2
    lo = int(freq / binhz)
    hi = lo + 2 * mode.tones
    # The median over 200 Hz around the signal: one bin's quarter is
    # noisy on its own, and a neighbour can lift a few.
    span = int(SNR_SPAN_HZ / binhz)
    noise = float(np.median(nf[max(lo - span, 0):min(hi + span, nf.size)]))
    s = max(sig - noise, noise * 1e-4)
    # A tone's whole power lands in one bin of a DFT one symbol long; the
    # noise in that bin is the noise in one baud.
    return 10 * math.log10(s / noise) + 10 * math.log10(mode.baud / 2500)


def _subtract(slot: Slot, d: Decode, start: float) -> None:
    """Take a decoded signal out of the audio, amplitude and all."""
    mode = slot.mode
    ref = synth(d.symbols, d.freq, mode, RATE, complex_out=True)
    at = int(round(start))
    lo, hi = max(at, 0), min(at + ref.size, slot.audio.size)
    if hi <= lo:
        return
    r = ref[lo - at:hi - at]
    x = slot.audio[lo:hi]
    z = x * np.conj(r)
    # The amplitude follows fading through the slot, but slowly: a box a
    # symbol wide, twice, which also removes the image at twice the tone.
    w = mode.sps
    for _ in range(2):
        c = np.cumsum(np.concatenate(([0.0 + 0j], z)))
        n = np.arange(z.size)
        a = np.clip(n - w // 2, 0, z.size)
        b = np.clip(n + w // 2 + 1, 0, z.size)
        z = (c[b] - c[a]) / (b - a)
    slot.audio[lo:hi] = x - 2 * np.real(z * r)


def _measure(slot: Slot, start: int, f0: float):
    """Fit one candidate and read its symbols: (llr, powers, start, freq,
    sync metric), or None if the fit says there is nothing there."""
    mode = slot.mode
    bb, (metric, s_best, df) = _fit(slot, start, f0)
    if metric < FIT_MIN:
        return None
    amps = _tone_amps(bb, mode, s_best, df, np.arange(mode.nn))
    p = amps.real ** 2 + amps.imag ** 2
    data = amps[mode.data_index]
    llrs = [_llr(data, mode, n) for n in NSYM[mode.name]]
    return llrs, p, s_best, f0 + df, metric


def _finish(slot: Slot, bits: np.ndarray, p, s_best, freq, metric,
            hashes: Hashes, seen: set, osd: bool = False, known=None):
    mode = slot.mode
    payload = bits[:77]
    if not bits.any():
        # All zeros satisfies every parity check and the CRC, and is
        # never sent: it is what noise decodes to most easily.
        return None
    if crc14(payload) != _int(bits[77:91]):
        return None
    if mode is FT4:
        payload = payload ^ FT4_XOR_BITS
    key = _int(payload)
    if key in seen:
        return None
    seen.add(key)
    try:
        # A scratch table first: a decode that is thrown out must not
        # leave its callsign behind to resolve somebody's hash with.
        text, fields = unpack77(payload, None)
    except ValueError:
        return None
    if not plausible(text, fields, osd, known):
        return None
    text, fields = unpack77(payload, hashes)
    sym = symbols_for(payload, mode)
    snr = _snr(slot, sym, s_best, freq)
    return Decode(mode.name, text, fields, int(round(snr)),
                  s_best / RATE - NOMINAL_START, freq, metric, symbols=sym)


_CALL = None
_CQ = re.compile(r"^CQ(?: (?:[A-Z]{1,4}|[0-9]{3}))?$")


def _ap_patterns(mode: Mode, mycall: str = "") -> list:
    """What can be known about a message before it is decoded, as
    (name, bit positions, bit values) in the codeword.

    WSJT-X's a-priori decoding, the two kinds that need no QSO in
    progress: **a1**, a CQ (the first call field is the CQ token and the
    message is the standard type), and **a2**, a message to the operator
    (that field is their own call). Pinning those 32 of the 77 bits to
    certainty is worth a decibel or two - the last of the gap to jt9 on
    real slots - because belief propagation then only has to find the
    other 45. The cost is that it *will* find 45 bits in noise more
    readily, which is what `AP_MAX_HARD` and the strict plausibility
    check are for.

    The CQ token has no /R bit to set, the type is i3=1, and FT4's
    scrambling applies to what is pinned exactly as to what is sent."""
    out = []
    i3 = [0, 0, 1]
    idx = np.array(list(range(29)) + [74, 75, 76])
    words = [("a1", "CQ")]
    if mycall:
        try:
            n, ip = _pack28(mycall.upper())
        except (ValueError, KeyError):
            n = -1
        # Only a call that packs as itself: a hashed one would pin a hash
        # of a call nobody may be using.
        if n >= NTOKENS + MAX22:
            words.append(("a2", mycall.upper()))
    for name, word in words:
        n, ip = _pack28(word)
        bits = np.array(_bits(n, 28) + [ip] + i3, dtype=np.uint8)
        if mode is FT4:
            bits = bits ^ FT4_XOR_BITS[idx]
        out.append((name, idx, bits))
    return out


def _ap_try(slot, got, todo, patterns, hashes, seen, passno, known=None):
    """A-priori decoding of the candidates belief propagation could not
    decode alone. Returns ([(Decode, s_best)], candidates still undecoded)."""
    cands = sorted((i for i in todo if got[i][4] >= AP_SYNC_MIN),
                   key=lambda i: -got[i][4])[:AP_MAX]
    if not cands or not patterns:
        return [], todo
    rows, meta = [], []
    for i in cands:
        for which, llr in enumerate(got[i][0]):
            mag = float(np.abs(llr).max()) * 1.01
            for name, idx, bits in patterns:
                pinned = llr.copy()
                pinned[idx] = np.where(bits == 1, mag, -mag)
                rows.append(pinned)
                meta.append((i, which, name, idx))
    words, errs = bp_decode_many(np.array(rows), LDPC_ITERATIONS)
    found, done = [], set()
    for (i, which, name, idx), word, e in zip(meta, words, errs):
        if e or i in done:
            continue
        llr = got[i][0][which]
        free = np.ones(174, dtype=bool)
        free[idx] = False
        # How far the decode is from what the signal itself said, on the
        # bits nobody told it: noise pinned into shape disagrees with its
        # own metrics far more than a weak real signal does.
        if int(((llr > 0).astype(np.uint8) != word)[free].sum()) > AP_MAX_HARD:
            continue
        g = got[i]
        d = _finish(slot, word, g[1], g[2], g[3], g[4], hashes, seen,
                    osd=True, known=known)
        if d is None:
            continue
        d.passno = passno
        d.ap = name
        found.append((d, g[2]))
        done.add(i)
    return found, [i for i in todo if i not in done]


def plausible(text: str, fields: dict, osd: bool, known=None) -> bool:
    """Whether a decode that passed the CRC is worth showing.

    Message types nobody defines are never real. From OSD - which tries
    hundreds of codewords per candidate, so the CRC alone lets through
    about one in sixteen thousand of them - only the ordinary kinds are
    taken, and only with callsigns shaped like callsigns: noise decoded
    that way reads "BQ7ABZ 9F0AFD R JQ89", which is a perfectly good
    message between two stations that do not exist."""
    global _CALL
    import re
    if _CALL is None:
        _CALL = re.compile(r"^(?:[A-Z0-9]{1,4}/)?(?:[A-Z]{1,2}|[0-9][A-Z]|"
                           r"[A-Z][0-9])[0-9][A-Z]{1,4}(?:/[A-Z0-9]{1,4})?$")
    kind = fields.get("kind")
    if kind == "other" and text.startswith("[message type"):
        if any(f"type {t}" in text for t in ("6", "7", "0.6", "0.7", "0.2")):
            return False
    if not osd:
        return True
    if kind not in ("standard", "nonstandard"):
        return False
    # /R and /P are one bit each, so half of noise's codewords carry one;
    # on the air they are rare. Every false find on 40 m had one or two.
    if "/R" in text or "/P" in text:
        return False
    for key in ("call", "to"):
        v = fields.get(key, "")
        # "CQ", "CQ DX", "CQ NA", "CQ 291" - and not "CQVXW6 6Z7K", which
        # is how OSD spelt noise as a non-standard message's addressee.
        if v.startswith("<") or v in ("DE", "QRZ") or _CQ.match(v):
            continue
        if not _CALL.match(v):
            return False
        # A callsign no country issues. Measured on two busy real bands:
        # OSD's false finds there read "A32TZM X70VKD", "O10EFG 5G1JZS",
        # "CQVXW6 6Z7K" - shaped like calls, from nowhere - at -4 to +2 dB,
        # where a strength rule would also lose the real fading stations
        # OSD exists to rescue. `known` is the caller's prefix table, so
        # this file still imports nothing.
        # The sender's grid has to be somewhere their country could be:
        # "DS4ESX OM1ZAX JC75" put a Slovak station near Antarctica.
        grid = fields.get("grid", "") if key == "call" else ""
        if known is not None and not known(v, grid):
            return False
    return True


def decode(audio: np.ndarray, mode: Mode | str = FT8, rate: int = RATE,
           f_lo: float = 200.0, f_hi: float = 3000.0,
           hashes: Hashes | None = None, passes: int = PASSES,
           deadline: float | None = None, ap: bool = False,
           mycall: str = "", known_call=None) -> list:
    """Every message in one slot of audio that starts at the slot's start.

    `ap` adds a-priori decoding of CQs, and of messages to `mycall` when
    it is given (see `_ap_patterns`). Off unless asked: a decode that was
    helped is marked `ap`, and whoever shows or reports it decides.

    `known_call(call, grid) -> bool`, when given, is asked about every
    callsign in a decode from the less certain stages (OSD and AP), with
    the grid the sender gave if any: a call no country issues, or a grid
    on the far side of the world from it, is noise in the shape of a
    message. The card passes the host's prefix table; plain
    belief-propagation decodes are not asked.

    `deadline` (time.monotonic) stops looking for more: a decode that is
    still running when the next slot needs decoding is a decode that will
    never catch up."""
    import time
    if isinstance(mode, str):
        mode = MODES[mode.lower()]
    hashes = hashes if hashes is not None else Hashes()
    x = resample(audio, rate)
    # Pad to the slot plus a little, so a late signal's tail is zeros
    # rather than off the end.
    want = int((mode.slot + 1.0) * RATE)
    if x.size < want:
        x = np.concatenate((x, np.zeros(want - x.size)))
    slot = Slot(x, mode)
    seen: set = set()
    out: list = []
    patterns = _ap_patterns(mode, mycall) if ap else []
    for passno in range(1, passes + 1):
        if deadline is not None and time.monotonic() > deadline:
            break
        power = _spectrogram(slot.audio, mode)
        if passno == 1:
            # Before anything is subtracted: what is left behind by a
            # subtraction is not noise.
            slot.noise = _noise_floor(slot.audio, mode)
        got = []
        for score, start, f0 in _candidates(power, mode, f_lo, f_hi,
                                            MAX_CANDIDATES):
            if deadline is not None and time.monotonic() > deadline:
                break
            m = _measure(slot, start, f0)
            if m is not None:
                got.append(m)
        if not got:
            break
        found = []
        todo = list(range(len(got)))
        # Each way of reading the bits in turn, for the candidates that
        # none of the earlier ones decoded.
        for which in range(len(NSYM[mode.name])):
            if not todo:
                break
            bits, errs = bp_decode_many(
                np.array([got[i][0][which] for i in todo]), LDPC_ITERATIONS)
            left = []
            for i, b, e in zip(todo, bits, errs):
                g = got[i]
                d = None if e else _finish(slot, b, g[1], g[2], g[3], g[4],
                                           hashes, seen)
                if d is None:
                    left.append(i)
                    continue
                d.passno = passno
                found.append((d, g[2]))
            todo = left
        if patterns and todo and not (
                deadline is not None and time.monotonic() > deadline):
            helped, todo = _ap_try(slot, got, todo, patterns, hashes, seen,
                                   passno, known_call)
            found.extend(helped)
        # What belief propagation could not, ordered statistics may - the
        # strongest-synced first, and only so many, because it is the
        # slow part and the weak end of a busy band is mostly noise.
        # Not beside anything this pass has decoded: a strong signal's
        # sidelobes give candidates a respectable sync, and OSD will make
        # a codeword of nearly anything. They are tried again next pass,
        # with the strong one subtracted and its sidelobes gone with it.
        width = mode.spacing * mode.tones
        near = [(d.freq, d.dt) for d, _ in found]
        todo = [i for i in todo if got[i][4] >= OSD_SYNC_MIN
                and not any(abs(got[i][3] - f) < width
                            for f, _ in near)]
        todo.sort(key=lambda i: -got[i][4])
        for i in todo[:OSD_MAX]:
            if deadline is not None and time.monotonic() > deadline:
                break
            g = got[i]
            for llr in g[0]:
                word, dist = osd_decode(llr, OSD_ORDER, OSD_DEPTH)
                sure, most = OSD_LIMITS.get(mode.name,
                                            (OSD_SURE_DIST, OSD_MAX_DIST))
                if word is None or dist > most or (
                        dist > sure and g[4] < OSD_FAR_SYNC):
                    continue
                d = _finish(slot, word, g[1], g[2], g[3], g[4], hashes, seen,
                            osd=True, known=known_call)
                if d is not None:
                    d.passno = passno
                    d.osd = round(dist, 3)
                    found.append((d, g[2]))
                    break
        if not found:
            break
        for d, s_best in found:
            _subtract(slot, d, s_best)
            out.append(d)
        slot.respectrum()
    return out


def format_line(slot_start_utc: float, d: Decode) -> str:
    """One line like WSJT-X's band activity: HHMMSS SNR DT FREQ ~ text."""
    import time
    hms = time.strftime("%H%M%S", time.gmtime(slot_start_utc))
    tail = f" {d.ap}" if d.ap else ""
    return (f"{hms} {d.snr:+3d} {d.dt:+4.1f} {int(round(d.freq)):4d} ~  "
            f"{d.text}{tail}")


# ---------------------------------------------------------------- streaming


class SlotDecoder:
    """FT8 or FT4 from a live stream: buffer each UTC slot, decode it once
    it is over, on a thread of its own.

    The same `feed(samples) -> text` as the CW, RTTY and PSK31 decoders, so
    the card treats it like them; the text is one line per decode, the way
    WSJT-X's band activity reads. The difference is time: a slot means
    something only against the clock, so every sample is placed in UTC.

    * **The clock is the earliest arrival, not the latest.** Audio comes
      in polled blocks, so a block reaches here some tens of milliseconds
      after it was heard, by an amount that varies. Each block gives "the
      stream's sample 0 was at time t or earlier"; the minimum over the
      last half minute is the best estimate, and a stall can only make an
      estimate later, never earlier. A jump of more than `GAP` means
      samples were lost, and the slot in progress is dropped rather than
      decoded with a hole in it.
    * **Decoding waits for the slot's tail.** A signal with DT +2 runs two
      seconds past the end of its slot, so the slot is decoded `WAIT`
      after it ends, and must finish before the next one is due - the
      deadline handed to `decode` is what makes a busy band degrade to
      fewer decodes rather than to a backlog.
    * **The median DT is reported** because a wrong system clock looks
      exactly like every station being early or late by the same amount.
    """

    GAP = 0.5
    KEEP_DECODES = 400

    def __init__(self, samplerate: int, mode: Mode | str = FT8,
                 clock=None, on_slot=None, on_slot_start=None,
                 threaded: bool = True, ap: bool = False,
                 mycall=None, known_call=None) -> None:
        import threading
        import time as _time
        self.mode = MODES[mode.lower()] if isinstance(mode, str) else mode
        self.rate = int(samplerate)
        self.clock = clock or _time.time
        self.on_slot = on_slot
        self.on_slot_start = on_slot_start
        self.threaded = threaded
        # A-priori decoding, and the operator's call for its a2 kind:
        # `mycall` may be a callable, asked each slot, so a callsign
        # entered in Settings takes effect without restarting the card.
        self.ap = ap
        self.mycall = mycall
        self.known_call = known_call
        self.wait = 1.0 if self.mode is FT8 else 0.5
        self.hashes = Hashes()
        self._lock = threading.Lock()
        self._buf = np.zeros(0, dtype=np.float32)
        self._buf_first = 0          # stream index of _buf[0]
        self._total = 0              # samples fed so far
        self._offsets: list = []     # (arrival, offset) over the window
        self._offset = None
        self._done_slot = None       # start of the last slot handed off
        self._seen_slot = None
        self._text: list = []
        self.decodes: list = []      # dicts, newest last, with "seq"
        self.seq = 0
        self.busy = False
        self.last_ms = None          # how long the last slot took
        self.last_count = 0
        self.last_slot = None
        self.median_dt = None
        self.dropped = 0
        # What the card's readout asks every decoder.
        self.tone_hz = 0.0
        self.snr_db = 0.0
        self.hearing = False

    # -- time ------------------------------------------------------------
    def _place(self, n: int) -> None:
        now = self.clock()
        self._total += n
        off = now - self._total / self.rate
        if self._offset is not None and off - self._offset > self.GAP:
            # Lost samples: everything held is in the wrong place now.
            self._buf = np.zeros(0, dtype=np.float32)
            self._buf_first = self._total
            self._offsets = []
            self._offset = None
            self._done_slot = None
            self.dropped += 1
        self._offsets.append((now, off))
        while self._offsets and now - self._offsets[0][0] > 30.0:
            self._offsets.pop(0)
        self._offset = min(o for _, o in self._offsets)

    def time_of(self, index: float) -> float:
        return index / self.rate + (self._offset or 0.0)

    def index_of(self, t: float) -> int:
        return int(round((t - (self._offset or 0.0)) * self.rate))

    # -- feeding ---------------------------------------------------------
    def feed(self, samples: np.ndarray) -> str:
        samples = np.asarray(samples, dtype=np.float32)
        if samples.size:
            self._buf = np.concatenate((self._buf, samples))
            self._place(samples.size)
            # Hold two slots and the tail: never more.
            keep = int((2 * self.mode.slot + 3) * self.rate)
            if self._buf.size > keep:
                cut = self._buf.size - keep
                self._buf = self._buf[cut:]
                self._buf_first += cut
            self._maybe_decode()
        with self._lock:
            out, self._text = "".join(self._text), []
        return out

    def _maybe_decode(self) -> None:
        slot = self.mode.slot
        t_last = self.time_of(self._total)
        current = math.floor(t_last / slot) * slot
        if current != self._seen_slot:
            self._seen_slot = current
            if self.on_slot_start is not None:
                try:
                    self.on_slot_start(current)
                except Exception:                       # noqa: BLE001
                    pass
        prev = current - slot
        if t_last < current + self.wait:
            prev -= slot                # the one just ended is not ready
        if self._done_slot is not None and prev <= self._done_slot:
            return
        first = self.index_of(prev)
        if first < self._buf_first:
            # Joined part-way through it: a partial slot decodes the
            # stations whose start was missed as nothing, or as garbage.
            self._done_slot = prev
            return
        lo = first - self._buf_first
        hi = self.index_of(prev + slot + self.wait) - self._buf_first
        audio = self._buf[lo:hi].copy()
        self._done_slot = prev
        if self.busy:
            # The last slot is still being decoded: this one is skipped,
            # and says so, rather than queued behind it for ever.
            self.dropped += 1
            return
        self.busy = True
        if self.threaded:
            import threading
            threading.Thread(target=self._decode, args=(prev, audio),
                             daemon=True, name=f"{self.mode.name}-slot").start()
        else:
            self._decode(prev, audio)

    def _decode(self, slot_start: float, audio: np.ndarray) -> None:
        import time as _time
        t0 = _time.monotonic()
        try:
            me = self.mycall() if callable(self.mycall) else self.mycall
            found = decode(audio, self.mode, self.rate, hashes=self.hashes,
                           deadline=t0 + self.mode.slot * 0.7,
                           ap=self.ap, mycall=me or "",
                           known_call=self.known_call)
        except Exception as exc:                        # noqa: BLE001
            found = []
            print(f"{self.mode.name} decode failed: {exc!r}")
        ms = int((_time.monotonic() - t0) * 1000)
        found.sort(key=lambda d: d.freq)
        rows = []
        lines = []
        for d in found:
            self.seq += 1
            row = {"seq": self.seq, "slot": slot_start, **d.as_dict()}
            if d.osd:
                row["osd"] = d.osd
            rows.append(row)
            lines.append(format_line(slot_start, d) + "\n")
        with self._lock:
            self.decodes.extend(rows)
            del self.decodes[:-self.KEEP_DECODES]
            self._text.extend(lines)
            self.last_ms = ms
            self.last_count = len(rows)
            self.last_slot = slot_start
            self.hearing = bool(rows)
            if rows:
                self.median_dt = round(float(np.median([r["dt"] for r in rows])),
                                       2)
                best = max(rows, key=lambda r: r["snr"])
                self.tone_hz, self.snr_db = best["freq"], best["snr"]
        self.busy = False
        if self.on_slot is not None:
            try:
                self.on_slot(slot_start, rows)
            except Exception as exc:                    # noqa: BLE001
                print(f"{self.mode.name} on_slot failed: {exc!r}")

    def since(self, seq: int) -> list:
        with self._lock:
            return [r for r in self.decodes if r["seq"] > seq]


def mock_slot_audio(slot_index: int, mode: Mode = FT8, rate: int = RATE
                    ) -> np.ndarray:
    """What the card's mock plays in a given slot: a QSO, a slot at a time,
    as two stations and a third calling CQ would send it."""
    ex = MOCK_FT8 if mode is FT8 else MOCK_FT4
    msgs = ex[slot_index % len(ex)]
    return slot_audio(msgs, mode, rate, seed=slot_index % 97)


# (text, Hz, DT, SNR) per slot, cycled. The even and odd slots are the two
# sides of a QSO, as they would be on the air; a CQ from somebody else and
# a weak station keep the window from being one conversation.
MOCK_FT8 = [
    [("CQ VA7HDX CN89", 1180, 0.1, -8), ("CQ DX JA1XYZ PM95", 2210, 0.3, -17)],
    [("VA7HDX VE7XYZ CN88", 1520, 0.2, -12), ("W9XYZ K1ABC RR73", 640, -0.1, -19)],
    [("VE7XYZ VA7HDX -12", 1180, 0.1, -9), ("CQ POTA N0XRG EL29", 2650, 0.0, -14)],
    [("VA7HDX VE7XYZ R-09", 1520, 0.2, -11)],
    [("VE7XYZ VA7HDX RR73", 1180, 0.1, -8), ("CQ DX JA1XYZ PM95", 2210, 0.3, -18)],
    [("VA7HDX VE7XYZ 73", 1520, 0.2, -12), ("TNX BOB 73 GL", 900, 0.4, -15)],
]
MOCK_FT4 = [
    [("CQ TEST VA7HDX CN89", 1200, 0.1, -6)],
    [("VA7HDX VE7XYZ CN88", 1600, 0.2, -9)],
    [("VE7XYZ VA7HDX -10", 1200, 0.1, -7), ("CQ DL1ABC JO62", 2300, 0.0, -13)],
    [("VA7HDX VE7XYZ R-08", 1600, 0.2, -10)],
    [("VE7XYZ VA7HDX RR73", 1200, 0.1, -6)],
    [("VA7HDX VE7XYZ 73", 1600, 0.2, -9)],
]
