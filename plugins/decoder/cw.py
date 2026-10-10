"""Morse decoding, as pure signal processing.

No audio device and no Flask here on purpose: the decoder is the part that
can be wrong in ways you cannot see, so it is a module that takes samples and
returns text and `tests/test_cw.py` drives it with synthesised CW at several
speeds, tones and signal-to-noise ratios.

How it works, and why each part is the way it is:

* **The tone is found, not configured.** Nobody knows what their sidetone
  pitch is in hertz, and it changes with the clarifier. The strongest bin in
  the CW passband wins, and it only moves when a different bin is clearly
  better for a while - a tracker that follows the loudest thing frame by
  frame hops onto a passing SSB station and goes deaf.

* **The threshold follows the band, not a number.** Sound card gain varies by
  tens of dB between a USB codec and whatever the operator left the mixer at,
  so a fixed level is wrong on nearly every machine. Mark and space are split
  between a recent noise floor and a recent peak, with hysteresis so a signal
  sitting on the threshold does not produce a burst of dots.

* **Speed is measured continuously.** An operator is not sending at the speed
  they were a minute ago, and no two are the same. The dot length is an
  exponential average fed by every element decoded, which is also what makes
  the WPM readout honest.

* **An unknown pattern is shown, not swallowed.** `<-.-.-.>` on screen tells
  you the decoder is hearing something it cannot name - usually the speed
  estimate being wrong, which you can see and act on. A silent drop looks
  exactly like the band being quiet.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

# Where CW lives in an SSB passband. Below 250 Hz is rumble and mains hum.
# The top was 1500 Hz on the assumption that a CW note sits near the usual
# 600-800 Hz sidetone, and the first signal this met off the air was at 2207
# Hz - a receiver in SSB passes the whole 300-2700 and the operator tunes
# wherever they like inside it, so that assumption cost the one signal in
# the band that was actually readable.
TONE_LOW_HZ = 250.0
TONE_HIGH_HZ = 2700.0

# A frame of about 8 ms, which resolves a 30 ms dot (40 WPM) into several
# samples. Hop is half a frame.
FRAME_SECONDS = 0.008

# The floor and the peak are tracked as quantiles, by stepping towards every
# frame: up by one rate when the frame is above the estimate, down by another
# when it is below. The ratio of the two rates *is* the quantile - step up at
# r and down at 3r and the estimate settles where a quarter of frames are
# below it - and the size of the steps is how fast it follows a changing
# band. No window and no assumption about how much of the time the key is
# down, which is what a percentile over a fixed history cannot avoid making.
#
# Chasing the minimum instead looks obvious and is wrong: the quiet frames of
# a single FFT bin of noise go very deep, so an instant-down floor sits far
# below the noise, and the threshold derived from it reads the band's own
# hiss as one endless dash.
FLOOR_UP_DB = 0.03                  # 25th percentile: down is 3x up
FLOOR_DOWN_DB = 0.09
PEAK_UP_DB = 0.64                   # 97th percentile: up is 32x down
PEAK_DOWN_DB = 0.02                 # slow, so a word gap does not lose it

# History the tone tracker looks at - about three seconds - and how much
# better another bin has to look before the tracker will move to it. The
# window has to hold the key both down and up to see any swing at all, and at
# 6 WPM a dash alone is 600 ms and a word gap 1.4 s, so a second and a half
# of history could be entirely inside one or the other.
TRACK_FRAMES = 560
TRACK_MARGIN_DB = 4.0
TRACK_SETTLE = 60

# How much a bin has to swing between loud and quiet before it is accepted
# as a keyed signal. Noise alone swings about 8 dB in a single FFT bin, and
# locking onto that replays a second and a half of hiss through the decoder
# and prints the letters it finds in it.
LOCK_SPREAD_DB = 18.0

# Frames of level tracking before anything is decoded, counted again from
# scratch whenever the tone moves to a different bin.
SETTLE_FRAMES = 40

# Below this there is no signal, only noise, and inventing elements from it
# would fill the screen with rubbish. Measured rather than guessed: the top
# and bottom of a noise-only distribution are about 8 dB apart in a single
# FFT bin, so a 6 dB floor reports a dead band as a signal.
MIN_SNR_DB = 12.0

# No dot is this short - it is 80 WPM - so a mark this brief is a noise
# spike, not an element. Without this, the first letter of an over is
# routinely preceded by two or three dots that nobody sent, because the
# thresholds are still being computed from a history that is all silence.
MIN_ELEMENT_MS = 15.0

# Element timing, in dots. 1 dot, 3 dot dash; 1 dot element gap, 3 dot letter
# gap, 7 dot word gap. The boundaries sit between them.
DASH_AT = 2.0
LETTER_GAP_AT = 2.0
WORD_GAP_AT = 5.0

# Bounds on the speed estimate, so one glitch cannot run it away. 5 WPM is
# slower than anyone sends; 60 is faster than this decoder is honest about.
MIN_DOT_MS = 1200.0 / 60.0
MAX_DOT_MS = 1200.0 / 5.0
DEFAULT_DOT_MS = 60.0                       # 20 WPM, until something arrives

# Marks needed before the speed estimate is trusted enough to print what it
# has decoded. Two letters, which is the cost of getting the first ones right.
CONFIDENT_AFTER = 8

# How many recent marks the speed is estimated from. Short on purpose: a
# longer window is steadier but takes most of a sentence to follow an
# operator who has slowed down, and the estimate has to hold both a dot and
# a dash to work at all, which two letters already do.
MARK_WINDOW = 10

# A mark or a gap longer than this is not Morse, it is a tuning carrier or
# the end of a transmission.
MAX_MARK_MS = 2000.0

# The widest span the mark/space decision is made over. Real audio never has
# a floor more than about this far below the signal, and a synthetic test
# tone in digital silence has one 160 dB down.
MAX_RANGE_DB = 40.0

MORSE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F",
    "--.": "G", "....": "H", "..": "I", ".---": "J", "-.-": "K", ".-..": "L",
    "--": "M", "-.": "N", "---": "O", ".--.": "P", "--.-": "Q", ".-.": "R",
    "...": "S", "-": "T", "..-": "U", "...-": "V", ".--": "W", "-..-": "X",
    "-.--": "Y", "--..": "Z",
    "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
    ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
    ".-.-.-": ".", "--..--": ",", "..--..": "?", ".----.": "'",
    "-.-.--": "!", "-..-.": "/", "-.--.-": ")",
    ".-...": "&", "---...": ":", "-.-.-.": ";", "-...-": "=",
    ".-.-.": "+", "-....-": "-", "..--.-": "_", ".-..-.": '"',
    "...-..-": "$", ".--.-.": "@",
    # Prosigns, written the way an operator would write them down. A few
    # share a pattern with a punctuation mark - "-.--." is both "(" and KN -
    # and the prosign is what is nearly always being sent on the air, so it
    # wins and the duplicate is not in the table at all. Two entries with one
    # key would silently drop whichever came first.
    "...-.-": "<SK>", "...-.": "<SN>", "-.--.": "<KN>", "........": "<HH>",
    ".-.-": "<AA>", "-.-.-": "<CT>",
}

# The other way round, for generating audio. Built once, and from MORSE, so
# the two can never disagree.
PATTERNS = {text: pattern for pattern, text in reversed(list(MORSE.items()))}


def _frame_sizes(samplerate: int) -> tuple[int, int]:
    """Frame and hop, as powers of two near `FRAME_SECONDS`."""
    want = max(16.0, samplerate * FRAME_SECONDS)
    n = 1 << max(4, int(round(math.log2(want))))
    return n, max(1, n // 2)


class CwDecoder:
    """Audio in, text out.

    Call `feed()` with however many samples have arrived; whatever it could
    decode comes back as a string, which is empty most of the time. State
    carries across calls, so the caller never has to think about block
    boundaries.
    """

    def __init__(self, samplerate: int = 48000) -> None:
        self.samplerate = int(samplerate)
        self.frame, self.hop = _frame_sizes(self.samplerate)
        self.frame_ms = 1000.0 * self.hop / self.samplerate
        self._window = np.hanning(self.frame).astype(np.float32)
        self._buf = np.zeros(0, dtype=np.float32)

        # Where in the spectrum to look, as bin numbers.
        hz_per_bin = self.samplerate / self.frame
        self._lo = max(1, int(TONE_LOW_HZ / hz_per_bin))
        self._hi = max(self._lo + 1,
                       min(self.frame // 2, int(TONE_HIGH_HZ / hz_per_bin)))
        self._hz_per_bin = hz_per_bin

        self._floor = None              # dB, the quiet level being tracked
        self._peak = None               # dB, the loud one
        self._bin = 0                   # the bin the tone is in; 0 = unknown
        self._locked = False            # ...and whether anything confirms it
        self._keyed = False             # is anything being sent right now
        # Recent level of every bin in the CW passband, as a ring. The tone
        # is chosen from this rather than from whichever bin is loudest.
        self._band = np.zeros((TRACK_FRAMES, self._hi - self._lo),
                              dtype=np.float32)
        self._band_n = 0

        self._mark = False              # whether the tone is on right now
        self._run = 0                   # frames in the current mark or gap
        # Durations and silences, not dots, dashes and letter breaks. Every
        # one of those is a judgement about the speed, and at the start of an
        # over the speed is not known: at 8 WPM against a cold 20 WPM
        # estimate even the gaps *inside* the first letter look like letter
        # breaks, and C arrives as T R. So nothing is decided until there is
        # enough to decide it with, and the list is segmented then.
        self._pending: list[tuple[float, bool]] = []    # (ms, is_mark)
        self._marks: deque[float] = deque(maxlen=MARK_WINDOW)
        self._seen = 0                  # marks since this decoder started
        self._pending_space = False     # a word gap waiting for a word

        self.dot_ms = DEFAULT_DOT_MS
        self.tone_hz = 0.0
        self.snr_db = 0.0
        self.level_db = -120.0

    # -- readouts ---------------------------------------------------------
    @property
    def wpm(self) -> float:
        """PARIS speed, which is what every other program means by WPM."""
        return round(1200.0 / max(self.dot_ms, 1.0), 1)

    @property
    def hearing(self) -> bool:
        return self.snr_db >= MIN_SNR_DB

    # -- the pipeline ------------------------------------------------------
    def feed(self, samples) -> str:
        """Decode whatever these samples complete. Usually ""."""
        block = np.asarray(samples, dtype=np.float32).ravel()
        if block.size:
            self._buf = np.concatenate((self._buf, block))
        out: list[str] = []
        while self._buf.size >= self.frame:
            frame = self._buf[:self.frame] * self._window
            self._buf = self._buf[self.hop:]
            out.append(self._frame(frame))
        return "".join(out)

    def flush(self) -> str:
        """Whatever is held back waiting for a gap that has not come.

        A letter is only finished by the silence after it, and the opening
        letters of an over are held until the speed is known, so without
        this the end of a short transmission never appears at all.
        """
        return self._drain(force=True)

    def _track(self, band: np.ndarray) -> str:
        """Choose the bin that is being keyed, and read back what it missed.

        Not the loudest: a steady carrier anywhere in the passband is louder
        than a CW signal for the half of the time the CW signal is off, and a
        loudest-bin tracker locks onto it and then reads the keying of
        nothing. What makes CW CW is that it goes on and off, so the bin with
        the greatest spread between its loud moments and its quiet ones wins
        - which a carrier, by definition, does not have.

        It also takes a clear margin to move. A tracker that re-decides every
        frame wanders between adjacent bins of one signal and chops its
        elements in half.

        Settling on a bin is also the moment the first element of the over
        has just gone past, so the ring is replayed through the detector at
        that point: the evidence that the tone is here at all *is* the first
        letter, and without the replay that letter is the price of finding
        it.
        """
        db = 20.0 * np.log10(np.maximum(band / self.frame, 1e-9))
        self._band[self._band_n % TRACK_FRAMES] = db
        self._band_n += 1
        filled = min(self._band_n, TRACK_FRAMES)
        if not self._bin:
            self._bin = int(np.argmax(band)) + self._lo
            self.tone_hz = round(self._bin * self._hz_per_bin, 1)

        # Never while the key is down. Moving the tone mid-element re-seeds
        # the thresholds underneath a mark that is already being timed and
        # cuts the element in two.
        if filled < TRACK_SETTLE or self._band_n % 16 or self._mark:
            return ""
        hist = self._ordered(filled)
        spread = (np.percentile(hist, 95, axis=0)
                  - np.percentile(hist, 20, axis=0))
        best = int(np.argmax(spread))
        here = min(max(self._bin - self._lo, 0), spread.size - 1)
        # Whether anything in the passband is going on and off at all. The
        # lock survives a gap between overs - it is the same station, and
        # finding it again costs the first letter of the next over - but
        # decoding does not. Noise crossing a threshold left over from a
        # signal that has stopped is where a decoder's screen fills up with
        # E and T between transmissions.
        self._keyed = bool(spread[here] >= MIN_SNR_DB
                           or spread[best] >= LOCK_SPREAD_DB)
        if spread[best] < LOCK_SPREAD_DB:
            # Nothing in the passband is going on and off. There is no tone
            # to lock onto, and reading one would be reading the noise. An
            # existing lock is left alone: a gap between overs is quiet by
            # definition, and dropping the lock there only means finding the
            # same signal again a moment later.
            return ""
        if self._bin == best + self._lo:
            if self._locked:
                return ""               # already here, and already read
        elif self._locked and spread[best] <= spread[here] + TRACK_MARGIN_DB:
            return ""                   # not clearly better than where we are
        self._bin = best + self._lo
        self._locked = True
        self.tone_hz = round(self._bin * self._hz_per_bin, 1)
        return self._replay(hist[:, best])

    def _ordered(self, filled: int) -> np.ndarray:
        """The ring, oldest frame first."""
        if self._band_n <= TRACK_FRAMES:
            return self._band[:filled]
        return np.roll(self._band, -(self._band_n % TRACK_FRAMES), axis=0)

    def _replay(self, column: np.ndarray) -> str:
        """Run the remembered levels of the new tone through the detector.

        Everything timed before the lock was timed at the wrong bin, so the
        detector starts again - and then hears the last second and a half as
        if it had been listening in the right place all along.
        """
        self._floor = float(np.percentile(column, 25))
        self._peak = float(np.percentile(column, 97))
        self._mark, self._run = False, 0
        self._pending.clear()
        self._marks.clear()
        self._seen = 0
        self.dot_ms = DEFAULT_DOT_MS
        return "".join(self._level(float(v)) for v in column)

    def _frame(self, windowed: np.ndarray) -> str:
        spec = np.abs(np.fft.rfft(windowed))
        band = spec[self._lo:self._hi]
        if not band.size:
            return ""

        replayed = self._track(band)

        # Energy at the tone, taken over three bins: the signal drifts, and a
        # keyed carrier is wider than one bin anyway.
        lo = max(0, self._bin - 1)
        power = float(np.max(spec[lo:self._bin + 2]))
        db = 20.0 * math.log10(max(power / self.frame, 1e-9))
        if replayed or not self._locked:
            # Either this frame has just been read as part of the replay, or
            # there is no confirmed tone to read it at.
            self.level_db = db
            return replayed
        return self._level(db)

    def _level(self, db: float) -> str:
        """One frame's level: thresholds, mark or space, and any edge."""
        self.level_db = db
        if not self._keyed:
            self._mark = False
            self._run = 0
            return self._drain(force=True)
        if self._floor is None:
            self._floor = self._peak = db
        self._floor += (FLOOR_UP_DB if db > self._floor else -FLOOR_DOWN_DB)
        self._peak += (PEAK_UP_DB if db > self._peak else -PEAK_DOWN_DB)
        # Clamped, because digital silence is -180 dB and nothing in a shack
        # is: an unclamped floor puts the decision threshold eighty dB below
        # the signal, where the shaped edge of a keyed note crosses it long
        # before the note is actually on. The peak cannot go under the floor.
        self._peak = max(self._peak, self._floor)
        noise = max(self._floor, self._peak - MAX_RANGE_DB)
        peak = self._peak
        self.snr_db = round(peak - noise, 1)

        if self.snr_db < MIN_SNR_DB:
            # Nothing is being sent. Let go of anything half-decoded rather
            # than holding it until the band next opens - it will not get
            # any better, and the operator is owed the decoder's best guess.
            self._mark = False
            self._run = 0
            return self._drain(force=True)

        # Hysteresis: a signal sitting on the threshold would otherwise key
        # itself into a stream of dots.
        on_at = noise + 0.60 * (peak - noise)
        off_at = noise + 0.40 * (peak - noise)
        mark = db >= on_at if not self._mark else db > off_at
        if mark == self._mark:
            self._run += 1
            if mark:
                return ""
            held = self._run * self.frame_ms
            if not self._pending:
                if held >= WORD_GAP_AT * self.dot_ms:
                    self._pending_space = True
                return ""
            # The letter is printed as soon as the silence after it is long
            # enough, not when the operator starts the next one - otherwise
            # the last character of an over waits for the next over.
            if held >= LETTER_GAP_AT * self.dot_ms:
                out = self._drain()
                if out or self._seen >= CONFIDENT_AFTER:
                    return out
            # And a silence this long means the over has ended, so print the
            # best guess rather than keeping it for an estimate that is not
            # going to improve.
            if held >= MAX_MARK_MS:
                return self._drain(force=True)
            return ""

        ms = self._run * self.frame_ms
        self._mark, self._run = mark, 1
        return self._on_edge(was_mark=not mark, ms=ms)

    # -- timing ------------------------------------------------------------
    def _on_edge(self, was_mark: bool, ms: float) -> str:
        if ms <= 0:
            return ""
        if was_mark:
            self._element(ms)
            return ""
        return self._gap(ms)

    def _element(self, ms: float) -> None:
        """A mark just ended: record how long it was and re-estimate."""
        if ms > MAX_MARK_MS:
            # A tuning carrier, not an element. It says nothing about speed,
            # and whatever was half-collected around it is not a letter.
            self._pending.clear()
            return
        if ms < MIN_ELEMENT_MS:
            # A spike. The time belongs to the silence it interrupted - drop
            # it without doing that and one letter gap becomes two short
            # ones, which is a letter break the decoder then fails to see.
            self._extend_gap(ms)
            return
        self._marks.append(ms)
        self._seen += 1
        self._learn()
        self._pending.append((ms, True))

    def _extend_gap(self, ms: float) -> None:
        """Give some time back to the silence in progress."""
        if self._pending and not self._pending[-1][1]:
            self._pending[-1] = (self._pending[-1][0] + ms, False)

    def _gap(self, ms: float) -> str:
        """A silence just ended: within a letter, between them, or a word."""
        if self._pending and not self._pending[-1][1]:
            # The mark between these two silences was a spike and was
            # dropped, so this is all one silence.
            self._extend_gap(ms)
            return ""
        if not self._pending:
            if ms >= WORD_GAP_AT * self.dot_ms:
                self._pending_space = True
            return ""
        if self._seen < CONFIDENT_AFTER or ms < LETTER_GAP_AT * self.dot_ms:
            # Either it is inside a letter, or the speed is not known well
            # enough to say - in which case it is recorded and judged when
            # the pending run is segmented.
            self._pending.append((ms, False))
            return ""
        if ms >= WORD_GAP_AT * self.dot_ms:
            self._pending_space = True
        return self._drain()

    def _learn(self) -> None:
        """The dot length, from the marks recently heard.

        A single running average cannot tell a dot from a dash on its own -
        it has to be told, by the very classification it is there to make.
        So the estimate comes from the spread instead: with both lengths
        present the short cluster is the dots, and the split between them is
        their geometric mean, which sits at the same ratio from each. Only
        when everything heard is one length does it fall back on believing
        the previous estimate.
        """
        marks = np.asarray(self._marks)
        if marks.size < 2:
            return
        if marks.size <= 8:
            # Percentiles of three samples are nearly the median, which
            # hides exactly the spread this is looking for: the first dot
            # and dash of an over are the whole of the evidence there is.
            lo, hi = float(np.min(marks)), float(np.max(marks))
        else:
            lo = float(np.percentile(marks, 10))
            hi = float(np.percentile(marks, 90))
        if hi >= 2.0 * lo:
            split = math.sqrt(lo * hi)
            dots = marks[marks <= split]
            want = float(np.median(dots)) if dots.size else lo
        else:
            mid = float(np.median(marks))
            want = mid if mid <= DASH_AT * self.dot_ms else mid / 3.0
        if not MIN_DOT_MS <= want <= MAX_DOT_MS:
            return
        # Quick to settle while the first few elements arrive, steady after:
        # nothing is printed until this is trustworthy, so the faster it
        # converges the sooner the opening letters appear.
        gain = 0.6 if self._seen < CONFIDENT_AFTER else 0.25
        self.dot_ms = (1 - gain) * self.dot_ms + gain * want

    def _drain(self, force: bool = False) -> str:
        """Segment what is pending into letters, and name them.

        This is the only place a duration becomes a symbol, so every one of
        them is decided with the best speed estimate there has ever been
        rather than the one that happened to be current when it arrived.
        """
        if not self._pending:
            return ""
        if not force and self._seen < CONFIDENT_AFTER:
            return ""
        letters: list[list[float] | None] = []      # None is a word gap
        current: list[float] = []
        for ms, is_mark in self._pending:
            if is_mark:
                current.append(ms)
                continue
            if ms < LETTER_GAP_AT * self.dot_ms:
                continue                            # inside a letter
            if current:
                letters.append(current)
                current = []
            if ms >= WORD_GAP_AT * self.dot_ms:
                letters.append(None)
        if current:
            letters.append(current)
        self._pending.clear()

        out: list[str] = []
        for letter in letters:
            if letter is None:
                self._pending_space = True
                continue
            pattern = "".join("-" if ms > DASH_AT * self.dot_ms else "."
                              for ms in letter)
            text = MORSE.get(pattern)
            if text is None:
                # Shown, not swallowed: it is nearly always the speed
                # estimate being off, and seeing it is what tells you that.
                text = f"<{pattern}>"
            if self._pending_space:
                self._pending_space = False
                text = " " + text
            out.append(text)
        return "".join(out)


def morse_for(text: str) -> list[str]:
    """The patterns for a string, for generating test audio and the mock."""
    out = []
    for ch in text.upper():
        if ch == " ":
            out.append(" ")
        elif ch in PATTERNS:
            out.append(PATTERNS[ch])
    return out


def keyed_audio(text: str, wpm: float = 20.0, tone_hz: float = 700.0,
                samplerate: int = 48000, amplitude: float = 0.5,
                rise_ms: float = 5.0, lead_seconds: float = 1.0) -> np.ndarray:
    """Synthesised CW, for the mock input and for the tests.

    The envelope is raised-cosine rather than square: real keying is shaped
    to stop it clicking across the band, and a square one would give the
    decoder an easier job than it will ever have in the shack.
    """
    dot = 1.2 / max(wpm, 1.0)
    rise = max(1, int(samplerate * rise_ms / 1000.0))
    shape = 0.5 * (1 - np.cos(np.linspace(0, np.pi, rise)))

    envelope: list[np.ndarray] = []

    def silence(dots: float) -> None:
        envelope.append(np.zeros(int(samplerate * dot * dots), dtype=np.float32))

    def key(dots: float) -> None:
        n = int(samplerate * dot * dots)
        e = np.ones(n, dtype=np.float32)
        if n > 2 * rise:
            e[:rise] = shape
            e[-rise:] = shape[::-1]
        envelope.append(e)

    # A run-up before the keying, because there always is one: the decoder
    # spends the first third of a second deciding which bin the signal is
    # in, and audio that starts mid-letter is not a case worth optimising.
    envelope.append(np.zeros(int(samplerate * lead_seconds), dtype=np.float32))
    for i, pattern in enumerate(morse_for(text)):
        if pattern == " ":
            silence(4)                  # 3 from the letter gap makes 7
            continue
        if i:
            silence(3)                  # between letters, minus the 1 below
        for j, el in enumerate(pattern):
            if j:
                silence(1)
            key(3 if el == "-" else 1)
    silence(7)

    env = np.concatenate(envelope) if envelope else np.zeros(1, dtype=np.float32)
    t = np.arange(env.size, dtype=np.float32) / samplerate
    return (amplitude * env * np.sin(2 * np.pi * tone_hz * t)).astype(np.float32)
