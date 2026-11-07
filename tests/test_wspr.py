"""The WSPR codec and decoder, held to WSJT-X's own tools.

`tests/fixtures/wspr/codes.json` is what `wsprcode` makes of 21 messages
of all three types, and `sim_band.json` is what `wsprd` decoded from a
slot this tree synthesises - `tools/wspr_fixtures.py` made both. Nothing
here asserts against a number worked out by the code under test: that is
the `bandsBetween` trap (CLAUDE.md), a mistake made identically by the
builder and the parser.

Run with:  python -m unittest discover -s tests
"""
import json
import sys
import time
import unittest
from pathlib import Path

try:
    import numpy as np
except ImportError:                                     # pragma: no cover
    # CI's host-only job runs without the audio cards' packages, on
    # purpose; this whole file is about a card's DSP.
    raise unittest.SkipTest("numpy is not installed")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "decoder"))

import wspr                                             # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "wspr"
CODES = json.loads((FIX / "codes.json").read_text())
BAND = json.loads((FIX / "sim_band.json").read_text())


def band_audio():
    return wspr.slot_audio([tuple(m) for m in BAND["sent"]], seed=BAND["seed"])


class TestTheCodeAgreesWithWsprcode(unittest.TestCase):
    def test_every_message_packs_bit_for_bit(self):
        for text, ref in CODES.items():
            with self.subTest(text=text):
                self.assertEqual(f"{wspr.pack(text):013x}", ref["value"])

    def test_every_message_gives_the_same_162_symbols(self):
        for text, ref in CODES.items():
            with self.subTest(text=text):
                got = "".join(str(int(x)) for x in wspr.channel_symbols(text))
                self.assertEqual(got, ref["symbols"])

    def test_the_sync_vector_is_the_symbols_low_bit(self):
        """Every channel symbol is sync + 2 * data, so its low bit is the
        published sync vector whatever the message."""
        for ref in CODES.values():
            low = [int(c) & 1 for c in ref["symbols"]]
            self.assertEqual(low, [int(x) for x in wspr.SYNC])

    def test_the_hash_is_the_one_wsprcode_sends(self):
        """A type 3 message carries the callsign as a 15-bit hash; its
        value is in the bits wsprcode wrote for <PJ4/K1ABC>."""
        ref = int(CODES["<PJ4/K1ABC> FK52UD 33"]["value"], 16)
        self.assertEqual(wspr.call_hash("PJ4/K1ABC"), (ref & 0x3FFFFF) >> 7)


class TestUnpacking(unittest.TestCase):
    def test_types_1_and_2_read_back_as_sent(self):
        for text in CODES:
            if text.startswith("<"):
                continue
            with self.subTest(text=text):
                got, fields = wspr.unpack(wspr.pack(text))
                self.assertEqual(got.split(), text.split())
                self.assertIn(fields["kind"], (1, 2))

    def test_a_hash_names_nobody_until_the_full_call_is_heard(self):
        h = wspr.Hashes()
        text, fields = wspr.unpack(wspr.pack("<PJ4/K1ABC> FK52UD 33"), h)
        self.assertEqual(text, "<...> FK52UD 33")
        self.assertEqual(fields["call"], "")
        wspr.unpack(wspr.pack("PJ4/K1ABC 33"), h)       # the type 2
        text, fields = wspr.unpack(wspr.pack("<PJ4/K1ABC> FK52UD 33"), h)
        self.assertEqual(text, "<PJ4/K1ABC> FK52UD 33")
        self.assertEqual((fields["call"], fields["grid"], fields["dbm"]),
                         ("PJ4/K1ABC", "FK52UD", 33))

    def test_what_cannot_be_sent_is_refused(self):
        for bad in ("K1ABC FN42 36", "K1ABC ZZ42 37", "K1 FN42 37",
                    "TOOLONGCALL FN42 37", "hello"):
            with self.subTest(text=bad):
                with self.assertRaises(ValueError):
                    wspr.pack(bad)


class TestDecodingAgainstWsprd(unittest.TestCase):
    """The slot wsprd decoded, rebuilt sample for sample and decoded here."""

    @classmethod
    def setUpClass(cls):
        cls.got = {d.text: d for d in wspr.decode(band_audio())}
        cls.ref = {" ".join(r["text"].split()): r for r in BAND["wsprd"]}

    def test_everything_wsprd_decoded(self):
        self.assertEqual(set(self.ref) - set(self.got), set())

    def test_nothing_that_was_not_sent(self):
        sent = {m[0] for m in BAND["sent"]}
        self.assertEqual(set(self.got) - sent, set())

    def test_the_same_snr_dt_frequency_and_drift(self):
        """WSJT-X's conventions, not guesses: SNR in 2500 Hz, DT from one
        second into the slot, the frequency where the signal began, the
        drift in hertz over the whole transmission."""
        for text, r in self.ref.items():
            d = self.got[text]
            with self.subTest(text=text):
                self.assertLessEqual(abs(d.snr - r["snr"]), 2)
                self.assertLessEqual(abs(d.dt - r["dt"]), 0.2)
                self.assertLessEqual(abs(d.freq - r["freq"]), 1.0)
                self.assertLessEqual(abs(d.drift - r["drift"]), 1.0)

    def test_two_stations_four_hertz_apart_both_decode(self):
        self.assertIn("DL1ABC JO62 13", self.got)       # 1505 Hz, -26 dB
        self.assertIn("ZL2AB RF70 53", self.got)        # 1509 Hz, -18 dB


class TestWhatMadeItWork(unittest.TestCase):
    """Each of these was measured against wsprd before it was kept; the
    tests show the version without it failing, so they cannot quietly stop
    testing anything."""

    MSGS = [("K1ABC FN42 37", 1445.0, 0.2, -29.5, 0.0),
            ("VE7PPE CN89 30", 1520.0, -0.4, -29.5, 0.8)]

    def test_coherent_blocks_hear_what_single_symbols_cannot(self):
        a = wspr.slot_audio(self.MSGS, seed=30)
        saved = wspr.BLOCKS
        try:
            wspr.BLOCKS = (1,)
            alone = {d.text for d in wspr.decode(a)}
            wspr.BLOCKS = saved
            blocks = {d.text for d in wspr.decode(a)}
        finally:
            wspr.BLOCKS = saved
        self.assertGreater(len(blocks), len(alone))
        self.assertEqual(blocks, {m[0] for m in self.MSGS})

    def test_the_fine_search_lands_within_eight_samples(self):
        """Judging pairs of symbols coherently is what put the start
        estimate within a few samples at -29.5 dB."""
        a = wspr.slot_audio(self.MSGS, seed=31)
        bb = wspr._baseband(a.astype(float), wspr.RATE)
        power = wspr._spectrogram(bb)
        for text, f0, dt, _snr, drift in self.MSGS:
            cbin = int(round(256 + (f0 - wspr.CENTRE + 1.5 * wspr.SPACING)
                             / (wspr.BB_RATE / 512)))
            _s, lag, b0, d0 = wspr._coarse(power, cbin)
            _s, start, g0, d1 = wspr._fine(bb, lag, b0, d0)
            truth = (wspr.START + dt) * wspr.BB_RATE
            with self.subTest(text=text):
                self.assertLessEqual(abs(start - truth), 8)
                self.assertLessEqual(abs(g0 - (f0 - wspr.CENTRE)), 0.12)

    def test_subtraction_separates_a_close_pair(self):
        msgs = [("K1ABC FN42 37", 1500.0, 0.1, -12, 0.0),
                ("JA1XYZ PM95 10", 1503.5, 0.4, -22, 0.0)]
        a = wspr.slot_audio(msgs, seed=8)
        once = {d.text for d in wspr.decode(a, passes=1)}
        twice = {d.text for d in wspr.decode(a)}
        self.assertEqual(twice, {m[0] for m in msgs})
        self.assertLess(len(once), 2)


def air(name):
    bb = np.load(FIX / f"{name}.npz")["bb"]
    return bb, json.loads((FIX / f"{name}.json").read_text())


class TestOffTheAir(unittest.TestCase):
    """Real slots from the station's FT-900, kept as baseband. These are the
    only fixtures that can disagree with the code about what a signal is
    like: the ionosphere smears a real one, and simulation does not."""

    @classmethod
    def setUpClass(cls):
        cls.bb80, cls.doc80 = air("air_80m_0632")
        cls.got80 = {d.text: d for d in wspr.decode_baseband(cls.bb80)}

    def test_80m_gives_what_wsprd_gave(self):
        got, doc = self.got80, self.doc80
        ref = {" ".join(r["text"].split()): r for r in doc["wsprd"]}
        self.assertEqual(set(got), set(ref))
        for text, r in ref.items():
            d = got[text]
            with self.subTest(text=text):
                # Real SNRs read 1-3 dB under wsprd's; simulated ones match.
                self.assertLessEqual(abs(d.snr - r["snr"]), 4)
                self.assertLessEqual(abs(d.dt - r["dt"]), 0.3)
                self.assertLessEqual(abs(d.freq - r["freq"]), 1.5)

    def test_a_smeared_station_is_not_thrown_away(self):
        """KG7RDR at -5 dB decodes with a path metric of about -4: the
        old floor of 5, tuned on clean synthetic signals, discarded it."""
        saved = wspr.METRIC_MIN
        try:
            wspr.METRIC_MIN = 5.0
            old = {d.text for d in wspr.decode_baseband(self.bb80)}
        finally:
            wspr.METRIC_MIN = saved
        self.assertNotIn("KG7RDR DN31 30", old)
        self.assertIn("KG7RDR DN31 30", self.got80)

    def test_snr_is_against_the_band_floor(self):
        """Measured from the other three tones, a -9 dB station read -21:
        on a real signal they hold its smeared energy, not just noise."""
        self.assertGreaterEqual(self.got80["WW0WWV DN70 30"].snr, -13)

    def test_fst4w_in_the_window_decodes_to_nothing(self):
        """30 m that night held only FST4W-120 beacons - same tone spacing,
        same frequencies - which jt9 read and wsprd did not."""
        bb, doc = air("air_30m_0624")
        self.assertTrue(doc["fst4w_jt9"])
        self.assertEqual(wspr.decode_baseband(bb), [])


class TestNoiseDecodesToNothing(unittest.TestCase):
    def test_twelve_slots_of_noise(self):
        for seed in range(12):
            with self.subTest(seed=seed):
                self.assertEqual(wspr.decode(wspr.slot_audio([], seed=900 + seed)),
                                 [])


class TestTheStream(unittest.TestCase):
    def test_a_mock_slot_fed_live_comes_out_as_rows(self):
        rows = []
        t = [1200.0]
        d = wspr.SlotDecoder(wspr.RATE, clock=lambda: t[0], threaded=False,
                             on_slot=lambda s, r: rows.extend(r))
        a = wspr.mock_slot_audio(0)
        for i in range(0, a.size, 6000):
            t[0] = 1200 + (i + 6000) / wspr.RATE
            d.feed(a[i:i + 6000])
        t[0] = 1320.8
        text = d.feed(np.zeros(9600, dtype=np.float32))
        sent = {m[0] for m in wspr.MOCK[0]}
        self.assertEqual({r["text"] for r in rows}, sent)
        for r in rows:
            self.assertEqual(r["slot"], 1200)
            self.assertEqual(r["mode"], "WSPR")
            self.assertIn("dbm", r)
            self.assertIn("drift", r)
        self.assertEqual(text.count("\n"), len(sent))

    def test_a_slot_is_decoded_well_inside_the_next(self):
        a = band_audio()
        t0 = time.monotonic()
        wspr.decode(a)
        self.assertLess(time.monotonic() - t0, 60)


if __name__ == "__main__":
    unittest.main()
