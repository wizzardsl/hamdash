"""FT8 and FT4, held to WSJT-X rather than to themselves.

`plugins/decoder/ftx.py` imports nothing from the card, for the reason
`cw.py` and `psk.py` do not. What it is checked against:

* `fixtures/ftx/codes.json` - what WSJT-X 3.0.2's `ft8code` and `ft4code`
  print for each message: the 77 source bits, the CRC, the 83 parity bits
  and the channel symbols, and the text WSJT-X reads back. The encoder,
  the LDPC tables and both modes' symbol maps must reproduce them, and
  the unpacker must read WSJT-X's bits as WSJT-X does. A round trip
  through this file's own encoder would only prove that the encoder and
  the decoder share a mistake ("tests that assert against a guess test
  the guess").
* `fixtures/ftx/*_sim_multi.wav` - several signals in one slot, and
  `fixtures/ftx/*_air_*.wav`, recorded off the air; the answer for each is
  what WSJT-X's `jt9` decoded from the same file (`tools/ftx_fixtures.py`
  made them and says how). FT8's simulated signals are WSJT-X's own
  waveforms from `ft8sim`; FT4's are this tree's, because `ft4sim_mult`
  writes nothing - jt9 decoding every one at the SNR it was made at is
  what says that waveform is right.

Tolerances against jt9: SNR within 2 dB (3 for FT4; 4 off the air, where
the two measure the noise in different places - over 119 decodes on 40 m
the mean difference was 0.0 dB and the mean size 1.7), DT within 0.1 s,
frequency within 2 Hz (3 for FT4, whose tones are 21 Hz apart).

Measured thresholds, on 30 of `ft8sim`'s own files at each SNR (jt9 run
with "my call" and "his call" set to stations not in the files, since its
defaults K1ABC and W9XYZ make it decode those a priori):

    FT8   -19 dB   -20   -21   -22        FT4  -15   -16   -17   -18
    jt9   30/30    28    16     3         jt9   30    30    27    15
    this  28/30    22    11     2         this  30    28    20     -

so about half a decibel behind jt9 without its a-priori decoding, in both
modes. This file checks the -19/-15 end of that, which is cheap.

Run with:  python -m unittest discover -s tests
"""
import json
import sys
import unittest
import wave
from pathlib import Path

try:
    import numpy as np
except ImportError:                                     # pragma: no cover
    # CI's host-only job runs without the audio cards' packages, on
    # purpose; this whole file is about a card's DSP.
    raise unittest.SkipTest("numpy is not installed")

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "ftx"
sys.path.insert(0, str(ROOT / "plugins" / "decoder"))

import ftx                                              # noqa: E402

CODES = json.loads((FIX / "codes.json").read_text())["messages"]
BITS = lambda s: [int(c) for c in s]                    # noqa: E731


def read(path):
    with wave.open(str(path)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        return a.astype(np.float32) / 32768.0, w.getframerate()


class TestTheCodeIsWsjtxs(unittest.TestCase):
    """Every table, against what ft8code and ft4code printed."""

    def test_the_source_encoding(self):
        for row in CODES:
            if row["ft8"]["altered"]:
                continue        # WSJT-X sends something else; see below
            with self.subTest(row["text"]):
                self.assertEqual("".join(map(str, ftx.pack77(row["text"]))),
                                 row["bits77"])

    def test_the_crc(self):
        for row in CODES:
            with self.subTest(row["text"]):
                self.assertEqual(format(ftx.crc14(BITS(row["bits77"])),
                                        "014b"), row["crc14"])

    def test_the_parity_bits_and_the_checks(self):
        for row in CODES:
            with self.subTest(row["text"]):
                cw = ftx.ldpc_encode(BITS(row["bits77"] + row["crc14"]))
                self.assertEqual("".join(map(str, cw[91:])), row["parity83"])
                # And the parity-check rows the decoder uses must agree
                # with the generator the encoder uses.
                self.assertEqual(ftx.parity_errors(cw), 0)

    def test_the_channel_symbols_of_both_modes(self):
        for row in CODES:
            with self.subTest(row["text"]):
                b = BITS(row["bits77"])
                self.assertEqual("".join(map(str, ftx.symbols_for(b, ftx.FT8))),
                                 row["ft8"]["symbols"])
                self.assertEqual("".join(map(str, ftx.symbols_for(b, ftx.FT4))),
                                 row["ft4"]["symbols"])

    def test_wsjtx_bits_read_as_wsjtx_reads_them(self):
        for row in CODES:
            with self.subTest(row["text"]):
                h = ftx.Hashes()
                want = row["ft8"]["decoded"]
                if "<" in want and "<...>" not in want:
                    # ft8code knows the call it has just hashed; a receiver
                    # knows it only once it has heard it in full.
                    h.save(want[want.index("<") + 1:want.index(">")])
                text, _ = ftx.unpack77(BITS(row["bits77"]), h)
                self.assertEqual(text, want)

    def test_an_unknown_hash_is_shown_as_wsjtx_shows_it(self):
        row = next(r for r in CODES if r["text"].startswith("<PJ4"))
        text, f = ftx.unpack77(BITS(row["bits77"]), ftx.Hashes())
        self.assertEqual(text, "<...> W9XYZ -10")
        self.assertEqual(f["report"], -10)

    def test_the_fields(self):
        _, f = ftx.unpack77(ftx.pack77("VA7HDX VE7XYZ R-05"))
        self.assertEqual((f["to"], f["call"], f["report"], f.get("roger")),
                         ("VA7HDX", "VE7XYZ", -5, True))
        _, f = ftx.unpack77(ftx.pack77("CQ DX JA1XYZ PM95"))
        self.assertEqual((f["to"], f["call"], f["grid"]),
                         ("CQ DX", "JA1XYZ", "PM95"))
        _, f = ftx.unpack77(ftx.pack77("VE7XYZ VA7HDX RR73"))
        self.assertEqual(f["ack"], "RR73")
        self.assertNotIn("grid", f)     # RR73 is a grid square too

    def test_the_mock_plays_messages_wsjtx_would_send(self):
        by_text = {r["text"]: r for r in CODES}
        for mode, ex in ((ftx.FT8, ftx.MOCK_FT8), (ftx.FT4, ftx.MOCK_FT4)):
            for slot in ex:
                for text, *_ in slot:
                    with self.subTest(text):
                        got = ftx.message_symbols(text, mode)
                        self.assertEqual("".join(map(str, got)),
                                         by_text[text][mode.name.lower()]
                                         ["symbols"])


class TestTheDecoder(unittest.TestCase):

    def test_belief_propagation_corrects_errors(self):
        b = BITS(CODES[0]["bits77"] + CODES[0]["crc14"])
        cw = ftx.ldpc_encode(b)
        rng = np.random.default_rng(3)
        llr = (cw * 2.0 - 1.0) + rng.normal(0, 0.6, 174)
        self.assertGreater(((llr > 0) != cw).sum(), 3)
        got, errs = ftx.bp_decode(llr * 2 / 0.36)
        self.assertEqual(errs, 0)
        self.assertTrue((got == cw).all())

    def test_osd_finds_what_bp_cannot(self):
        b = BITS(CODES[0]["bits77"] + CODES[0]["crc14"])
        cw = ftx.ldpc_encode(b)
        bp = osd = 0
        for seed in range(20):
            rng = np.random.default_rng(seed)
            llr = ((cw * 2.0 - 1.0) + rng.normal(0, 0.9, 174)) * 2 / 0.81
            got, errs = ftx.bp_decode(llr)
            bp += bool(errs == 0 and (got == cw).all())
            word, _ = ftx.osd_decode(llr)
            osd += word is not None and bool((word == cw).all())
        self.assertGreater(osd, bp)

    def _check_against_jt9(self, name, snr_tol, freq_tol, must_find):
        audio, rate = read(FIX / f"{name}.wav")
        meta = json.loads((FIX / f"{name}.json").read_text())
        mode = ftx.MODES[meta["mode"]]
        got = {d.text: d for d in ftx.decode(audio, mode, rate)}
        expect = [r for r in meta["jt9"] if "ap" not in r]
        sent = {r["text"] for r in meta.get("made", [])}
        missed = []
        for r in expect:
            d = got.get(r["text"])
            if d is None:
                missed.append(r)
                continue
            with self.subTest(r["text"]):
                self.assertLessEqual(abs(d.snr - r["snr"]), snr_tol)
                self.assertLessEqual(abs(d.dt - r["dt"]), 0.1 + 0.05)
                self.assertLessEqual(abs(d.freq - r["freq"]), freq_tol)
        # Only the weakest may be missed: below must_find is jt9's half
        # a decibel.
        self.assertEqual([m for m in missed if m["snr"] >= must_find], [],
                         f"missed: {missed}")
        # Anything decoded that jt9 did not must be something that was
        # really sent - never a false decode.
        for text in got:
            if text not in {r["text"] for r in expect}:
                self.assertIn(text, sent, "decoded something not sent")
        return got, expect

    def test_ft8_matches_jt9_on_wsjtxs_own_signals(self):
        got, expect = self._check_against_jt9("ft8_sim_multi", 2, 2, -19)
        self.assertGreaterEqual(len(got), len(expect) - 2)

    def test_ft4_matches_jt9(self):
        got, expect = self._check_against_jt9("ft4_sim_multi", 3, 3, -15)
        self.assertGreaterEqual(len(got), len(expect))

    def test_overlapping_signals_are_both_found(self):
        audio, rate = read(FIX / "ft8_sim_multi.wav")
        got = {d.text: d for d in ftx.decode(audio, ftx.FT8, rate)}
        # 15 Hz apart, 9 dB apart, inside each other's 50 Hz.
        self.assertIn("CQ DX JA1XYZ PM95", got)
        self.assertIn("JA1XYZ G4ABC IO91", got)
        # The weaker is found once the stronger is subtracted.
        self.assertGreater(got["JA1XYZ G4ABC IO91"].passno, 1)

    def test_a_quiet_band_prints_nothing(self):
        for mode in (ftx.FT8, ftx.FT4):
            for seed in range(3):
                with self.subTest(f"{mode.name} {seed}"):
                    a = np.random.default_rng(seed).standard_normal(
                        int(mode.slot * 12000)).astype(np.float32) * 0.1
                    self.assertEqual(ftx.decode(a, mode), [])

    def test_the_threshold(self):
        # Six slots each at the bottom of where both decoders agree:
        # -19 dB FT8, -15 dB FT4 (see the table at the top).
        for mode, snr in ((ftx.FT8, -19), (ftx.FT4, -15)):
            ok = 0
            for seed in range(6):
                a = ftx.slot_audio([("VE7XYZ VA7HDX CN89", 1500, 0.2, snr)],
                                   mode, seed=seed)
                ok += any(d.text == "VE7XYZ VA7HDX CN89" for d in
                          ftx.decode(a, mode, f_lo=1400, f_hi=1600, passes=1))
            with self.subTest(mode.name):
                self.assertGreaterEqual(ok, 5)

    def test_any_sample_rate(self):
        # A 44.1 kHz interface hands the decoder 14700 Hz.
        a = ftx.slot_audio([("CQ VA7HDX CN89", 1200, 0.0, -10)], ftx.FT8,
                           rate=14700, seed=1)
        d = ftx.decode(a, ftx.FT8, 14700)
        self.assertEqual([x.text for x in d], ["CQ VA7HDX CN89"])
        self.assertAlmostEqual(d[0].freq, 1200, delta=1)

    def test_snr_dt_and_frequency(self):
        for snr in (-2, -10, -16):
            a = ftx.slot_audio([("CQ VA7HDX CN89", 777.7, 0.4, snr)],
                               ftx.FT8, seed=5)
            d = ftx.decode(a)[0]
            with self.subTest(snr):
                self.assertLessEqual(abs(d.snr - snr), 1)
                self.assertAlmostEqual(d.dt, 0.4, delta=0.03)
                self.assertAlmostEqual(d.freq, 777.7, delta=0.5)


class TestOffTheAir(unittest.TestCase):
    """Recordings from the operator's FT-900, decoded by jt9 and by this."""

    def test_every_recording(self):
        files = sorted(FIX.glob("*_air_*.wav"))
        if not files:
            self.skipTest("no off-air recordings")
        for wav in files:
            meta = json.loads(wav.with_suffix(".json").read_text())
            audio, rate = read(wav)
            got = {d.text: d for d in ftx.decode(audio, meta["mode"], rate)}
            expect = {r["text"]: r for r in meta["jt9"] if "ap" not in r}
            with self.subTest(wav.name):
                found = [t for t in expect if t in got]
                # jt9 is half a decibel better; what is missed must be
                # from the weak end.
                for t, r in expect.items():
                    if t not in got:
                        self.assertLessEqual(r["snr"], -17, t)
                self.assertGreaterEqual(len(found), meta["expect_found"])
                extra = [t for t in got if t not in expect]
                self.assertEqual(sorted(extra), sorted(meta.get("extra", [])))
                for t in found:
                    # On a real band the two estimate the noise from
                    # different places: over 119 decodes on 40 m the mean
                    # difference was 0.0 dB, the mean size 1.7, the worst 4.
                    self.assertLessEqual(abs(got[t].snr - expect[t]["snr"]),
                                         4, t)
                    self.assertLessEqual(abs(got[t].dt - expect[t]["dt"]),
                                         0.15, t)
                    self.assertLessEqual(abs(got[t].freq - expect[t]["freq"]),
                                         3, t)


class TestSlots(unittest.TestCase):
    """The live path: a stream placed on the UTC clock, a slot at a time."""

    def feed(self, mode, slots, start, gap_at=None, block=1200):
        t = [start]
        dec = ftx.SlotDecoder(12000, mode, clock=lambda: t[0],
                              threaded=False)
        text = ""
        first = int(round(start / mode.slot))
        for k in range(slots):
            a = ftx.mock_slot_audio(first + k, mode)
            for i in range(0, a.size, block):
                if gap_at is not None and k == gap_at and i == 24000:
                    t[0] += 2.0                 # two seconds lost
                    continue
                t[0] += block / 12000
                text += dec.feed(a[i:i + block])
        return dec, text

    def test_each_slot_is_decoded_once_it_is_over(self):
        start = 1_700_000_000 - 1_700_000_000 % 15
        dec, text = self.feed(ftx.FT8, 4, start)
        lines = text.splitlines()
        # Slots 0, 1 and 2 are over and their tails heard; 3 is not done.
        slots = sorted({r["slot"] for r in dec.decodes})
        self.assertEqual(slots, [start, start + 15, start + 30])
        self.assertTrue(any("CQ VA7HDX CN89" in x or "VE7XYZ" in x
                            for x in lines))
        # HHMMSS of the slot it was heard in, as WSJT-X writes it.
        import time
        hms = time.strftime("%H%M%S", time.gmtime(start))
        self.assertTrue(lines[0].startswith(hms))
        self.assertAlmostEqual(dec.median_dt, 0.15, delta=0.2)

    def test_ft4_slots_are_seven_and_a_half_seconds(self):
        start = 1_700_000_000 - 1_700_000_000 % 15 + 7.5
        dec, _ = self.feed(ftx.FT4, 3, start)
        self.assertEqual(sorted({r["slot"] for r in dec.decodes}),
                         [start, start + 7.5])

    def test_a_slot_joined_part_way_is_not_decoded(self):
        start = 1_700_000_000 - 1_700_000_000 % 15
        t = [start + 6.0]
        dec = ftx.SlotDecoder(12000, ftx.FT8, clock=lambda: t[0],
                              threaded=False)
        a = np.concatenate([ftx.mock_slot_audio(k, ftx.FT8)
                            for k in range(3)])[int(6 * 12000):]
        for i in range(0, a.size, 1200):
            t[0] += 0.1
            dec.feed(a[i:i + 1200])
        self.assertNotIn(start, {r["slot"] for r in dec.decodes})
        self.assertIn(start + 15, {r["slot"] for r in dec.decodes})

    def test_lost_audio_drops_the_slot_rather_than_decode_a_hole(self):
        start = 1_700_000_000 - 1_700_000_000 % 15
        dec, _ = self.feed(ftx.FT8, 4, start, gap_at=1)
        self.assertGreaterEqual(dec.dropped, 1)
        self.assertNotIn(start + 15, {r["slot"] for r in dec.decodes})

    def test_a_late_arrival_does_not_move_the_clock(self):
        # Blocks that arrive late (a stall) must not shift where samples
        # are placed: the clock is the earliest arrival.
        t = [1000.0]
        dec = ftx.SlotDecoder(12000, ftx.FT8, clock=lambda: t[0],
                              threaded=False)
        for k in range(50):
            t[0] += 0.1 + (0.3 if k == 20 else 0.0) - (0.3 if k == 21 else 0)
            dec.feed(np.zeros(1200, dtype=np.float32))
        self.assertAlmostEqual(dec.time_of(0), 1000.0, delta=0.01)
        self.assertEqual(dec.dropped, 0)


if __name__ == "__main__":
    unittest.main()
