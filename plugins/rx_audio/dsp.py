"""Decimation for the receive-audio card: samples in, samples out.

Imports nothing from the card, like `decoder/cw.py`, so it can be driven
with synthesised signals at known frequencies - which is the only way to
tell a working anti-alias filter from one that is quietly folding the
hiss above the cut back down into the passband. That failure is not
audible as a fault: it just sounds like a noisier radio.

A receiver's audio is about 3 kHz wide, so sending 48 kHz is four times
the bandwidth for nothing. The spike used a box average, which is a very
poor low-pass - its first null is at the output rate and it is only about
13 dB down in the first sidelobe.
"""
from __future__ import annotations

import numpy as np

# Where the passband ends, as a fraction of the OUTPUT Nyquist, and how
# many taps there are to get from there to nothing.
#
# These two are one decision. A Hamming-windowed sinc needs about 3.3/N of
# the sample rate to go from passband to stopband, so with N taps the
# stopband starts at cutoff + 3.3*in_rate/N - and it has to start at or
# below the output Nyquist, because everything above that folds. The first
# try was 0.90 and 63 taps, which put the stopband edge at 6.7 kHz when
# the Nyquist was 6.0: measured only 14 dB down at 6 kHz, so anything in
# the hiss just above it came back into the speech band barely attenuated.
#
# 0.72 and 95 taps puts it at 4.3 kHz and 6.0 kHz respectively. A
# receiver's audio is about 3 kHz wide, so a 4.3 kHz passband costs
# nothing real and buys a stopband that lands exactly where it must.
CUTOFF = 0.72
TAPS = 95               # odd, so the delay is a whole number of samples


def design(in_rate: int, factor: int, taps: int = TAPS) -> np.ndarray:
    """A windowed-sinc low-pass for decimating by `factor`.

    Cutoff is just under the *output* Nyquist, which is what decides
    whether anything folds: a component above in_rate/(2*factor) comes
    back as a mirror image inside the passband, and nothing downstream can
    tell it from a real signal.
    """
    fc = CUTOFF / (2.0 * factor)             # cycles/sample of the input
    n = np.arange(taps, dtype=np.float64) - (taps - 1) / 2.0
    h = 2.0 * fc * np.sinc(2.0 * fc * n)
    h *= np.hamming(taps)
    return (h / h.sum()).astype(np.float32)


def factor_for(in_rate: int, wanted: int) -> int:
    """Whole-number decimation only, so no resampling is needed.

    The card reports the rate it actually produces rather than the one it
    was asked for: 48000 asked for 12000 gives exactly 12000, and 44100
    gives 14700. A browser's AudioBuffer takes any rate, so an odd one
    costs nothing, where resampling to hit a round number would cost
    quality and code.
    """
    if wanted <= 0 or in_rate <= wanted:
        return 1
    return max(1, int(in_rate // wanted))


class Decimator:
    """Stateful: it carries the filter's tail between blocks.

    Filtering each block independently is the obvious thing and it clicks
    every block boundary, because the filter starts from zeros each time -
    at 50 blocks a second that is a 50 Hz buzz laid over the audio.
    """

    def __init__(self, in_rate: int, wanted: int, taps: int = TAPS) -> None:
        self.in_rate = int(in_rate)
        self.factor = factor_for(in_rate, wanted)
        self.out_rate = int(self.in_rate // self.factor)
        self.taps = design(self.in_rate, self.factor, taps) \
            if self.factor > 1 else None
        self._tail = np.zeros(0, dtype=np.float32)
        self._phase = 0          # which input sample the next output is on

    def __call__(self, block: np.ndarray) -> np.ndarray:
        block = np.asarray(block, dtype=np.float32)
        if self.factor == 1:
            return block
        x = np.concatenate([self._tail, block])
        n = len(self.taps)
        if len(x) < n:
            self._tail = x
            return np.zeros(0, dtype=np.float32)
        # 'valid' consumes n-1 samples of history; keep them for next time.
        y = np.convolve(x, self.taps, mode="valid").astype(np.float32)
        self._tail = x[len(x) - (n - 1):]
        # Keep the decimation phase continuous across blocks, or the
        # output rate wobbles by a sample here and there.
        idx = np.arange(self._phase, len(y), self.factor)
        self._phase = (self._phase - len(y)) % self.factor
        return y[idx]
