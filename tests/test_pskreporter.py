"""PSK Reporter uploads, held to PSK Reporter's own description.

https://pskreporter.info/pskdev.html gives the record format descriptors
and a complete example datagram as bytes; the encoder must reproduce
them exactly, because the server is somebody else's and the first sign
of a mistake would be reports silently thrown away. Everything else here
is the policy that page sets - batching, one datagram per five minutes,
templates in the first three and hourly, a callsign at most once per five
minutes and once an hour unless it changes band - driven by a fake clock.

**Nothing in this file reaches PSK Reporter.** Datagrams go to a list,
or, once, to a UDP socket on 127.0.0.1.

Run with:  python -m unittest discover -s tests
"""
import socket
import struct
import unittest

from hamdash.bus import Bus
from hamdash.services import pskreporter as p

HEX = lambda s: bytes.fromhex(" ".join(s.split()))      # noqa: E731

# The page's complete datagram: header, both descriptors, the receiver
# record for N1DQ, two sender records - sent at 1200960114, sequence 1.
EXAMPLE = HEX("""
00 0A 00 AC 47 95 32 72 00 00 00 01 00 00 00 00
00 03 00 24  99 92 00 03 00 01  80 02 FF FF 00 00 76 8F  80 04 FF FF 00 00 76 8F
             80 08 FF FF 00 00 76 8F  00 00
00 02 00 2C  99 93 00 05  80 01 FF FF 00 00 76 8F  80 05 00 04 00 00 76 8F
             80 0A FF FF 00 00 76 8F  80 0B 00 01 00 00 76 8F  00 96 00 04
99 92 00 20  04 4E 31 44 51  06 46 4E 34 32 68 6E
             0D 48 6F 6D 65 62 72 65 77 20 76 35 2E 36  00 00
99 93 00 2C  04 4E 31 44 51  00 D6 B3 27  03 50 53 4B  01  47 95 32 54
             06 4B 42 31 4D 42 58  00 D6 B4 CB  03 50 53 4B  01  47 95 32 68
             00 00
""")
# And the same data later in the session, without the descriptors.
LATER = HEX("""
00 0A 00 5C 47 95 32 72 00 00 00 04 00 00 00 00
99 92 00 20  04 4E 31 44 51  06 46 4E 34 32 68 6E
             0D 48 6F 6D 65 62 72 65 77 20 76 35 2E 36  00 00
99 93 00 2C  04 4E 31 44 51  00 D6 B3 27  03 50 53 4B  01  47 95 32 54
             06 4B 42 31 4D 42 58  00 D6 B4 CB  03 50 53 4B  01  47 95 32 68
             00 00
""")
# The sender descriptor with a locator, which is the one hamdash uses.
WITH_LOCATOR = HEX("""
00 02 00 34 99 93 00 06
80 01 FF FF 00 00 76 8F  80 05 00 04 00 00 76 8F  80 0A FF FF 00 00 76 8F
80 0B 00 01 00 00 76 8F  80 03 FF FF 00 00 76 8F  00 96 00 04
""")
NO_LOCATOR = tuple(f for f in p.TX_FIELDS if f[0] != 3)


class TestTheBytes(unittest.TestCase):

    def example(self, templates, sequence):
        spots = [p.tx_record({"call": "N1DQ", "freq": 14070567,
                              "mode": "PSK", "at": 1200960084}, NO_LOCATOR),
                 p.tx_record({"call": "KB1MBX", "freq": 14070987,
                              "mode": "PSK", "at": 1200960104}, NO_LOCATOR)]
        return p.datagram(export_time=1200960114, sequence=sequence,
                          domain=0,
                          receiver=p.rx_record("N1DQ", "FN42hn",
                                               "Homebrew v5.6"),
                          spots=spots, templates=templates,
                          tx_fields=NO_LOCATOR)

    def test_the_pages_example_datagram(self):
        self.assertEqual(self.example(True, 1).hex(" "), EXAMPLE.hex(" "))

    def test_and_later_without_the_descriptors(self):
        self.assertEqual(self.example(False, 4).hex(" "), LATER.hex(" "))

    def test_the_descriptors(self):
        self.assertEqual(p.rx_template(), EXAMPLE[16:16 + 36])
        self.assertEqual(p.tx_template(), WITH_LOCATOR)

    def test_lengths_and_padding(self):
        for n in range(0, 7):
            spots = [p.tx_record({"call": "K1ABC" + "X" * k, "freq": 7074000,
                                  "mode": "FT8", "locator": "FN42"[:k % 5],
                                  "at": 1}) for k in range(n)]
            msg = p.datagram(export_time=1, sequence=0, domain=7,
                             receiver=p.rx_record("VA7HDX", "CN89", "hamdash"),
                             spots=spots, templates=bool(n % 2))
            with self.subTest(n):
                self.assertEqual(struct.unpack(">H", msg[2:4])[0], len(msg))
                at = 16
                while at < len(msg):
                    sid, size = struct.unpack(">HH", msg[at:at + 4])
                    self.assertEqual(size % 4, 0, f"set {sid:#x} unpadded")
                    at += size
                self.assertEqual(at, len(msg))

    def test_a_long_string_is_cut_not_overflowed(self):
        self.assertEqual(len(p.string("x" * 400)), 255)
        self.assertEqual(p.string("x" * 400)[0], 254)


def parse(msg):
    """A small IPFIX reader, independent of the encoder: the records each
    data set holds, decoded by the templates the message itself carries."""
    version, length, when, seq, domain = struct.unpack(">HHIII", msg[:16])
    assert version == 10 and length == len(msg)
    templates, out, at = {}, {}, 16
    while at < len(msg):
        sid, size = struct.unpack(">HH", msg[at:at + 4])
        body, at = msg[at + 4:at + size], at + size
        if sid in (2, 3):
            tid, count = struct.unpack(">HH", body[:4])
            pos = 6 if sid == 3 else 4
            fields = []
            for _ in range(count):
                ident, flen = struct.unpack(">HH", body[pos:pos + 4])
                pos += 8 if ident & 0x8000 else 4
                fields.append((ident & 0x7FFF, flen))
            templates[tid] = fields
            continue
        fields = TEMPLATES.get(sid) or templates[sid]
        TEMPLATES[sid] = fields
        recs, pos = [], 0
        while pos < len(body) and any(body[pos:]):
            rec = {}
            for ident, flen in fields:
                if flen == 0xFFFF:
                    n = body[pos]
                    rec[ident] = body[pos + 1:pos + 1 + n].decode()
                    pos += 1 + n
                else:
                    rec[ident] = int.from_bytes(body[pos:pos + flen], "big")
                    pos += flen
            recs.append(rec)
        out[sid] = recs
    return {"time": when, "seq": seq, "domain": domain,
            "templates": sorted(templates), "records": out}


TEMPLATES: dict = {}


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def event(*decodes, mode="FT8", simulated=False, source="decoder",
          slot=1_000_000.0):
    return {"topic": "decoder.decoded", "source": source,
            "payload": {"mode": mode, "slot_ts": slot, "simulated": simulated,
                        "dial_hz": 7074000, "decodes": list(decodes)}}


def heard(call, rf=7075000, grid="", **kw):
    d = {"call": call, "rf_hz": rf, "snr": -10, "text": f"CQ {call} {grid}"}
    if grid:
        d["grid"] = grid
    d.update(kw)
    return d


class TestThePolicy(unittest.TestCase):

    def setUp(self):
        TEMPLATES.clear()
        self.clock = Clock()
        self.sent = []
        self.station = {"callsign": "VA7HDX", "grid": "CN89kg"}
        self.r = p.Reporter(lambda: self.station, "1.4.0",
                            send=self.sent.append, clock=self.clock)
        self.r.set_enabled(True)

    def run_for(self, seconds, step=2.0):
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.clock.t += step
            self.r.tick()

    def test_off_sends_nothing_and_holds_nothing(self):
        self.r.set_enabled(False)
        self.assertEqual(self.r.offer(event(heard("K1ABC"))), 0)
        self.run_for(1000)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.r.status()["blocked"], "off")

    def test_no_callsign_or_locator_means_no_upload(self):
        for station, why in (({"callsign": "", "grid": "CN89"}, "no callsign"),
                             ({"callsign": "VA7HDX", "grid": ""}, "no locator")):
            self.station = station
            with self.subTest(why):
                self.assertEqual(self.r.offer(event(heard("K1ABC"))), 0)
                self.run_for(400)
                self.assertEqual(self.sent, [])
                self.assertEqual(self.r.status()["blocked"], why)

    def test_one_datagram_per_five_minutes_from_the_start(self):
        self.r.offer(event(heard("K1ABC")))
        self.run_for(298)
        self.assertEqual(self.sent, [], "sent before five minutes")
        self.run_for(4)
        self.assertEqual(len(self.sent), 1)
        self.r.offer(event(heard("W9XYZ")))
        self.run_for(290)
        self.assertEqual(len(self.sent), 1)
        self.run_for(12)
        self.assertEqual(len(self.sent), 2)

    def test_the_schedule_is_not_the_wall_clock(self):
        # Started at an odd moment, sends at that moment plus five minutes
        # - not on a minute boundary.
        self.clock.t = 1_000_037.0
        self.r.set_enabled(False)
        self.r.set_enabled(True)
        self.r.offer(event(heard("K1ABC")))
        self.run_for(310, step=1.0)
        when = parse(self.sent[0])["time"]
        self.assertEqual(when, 1_000_037 + 300)

    def test_a_full_datagram_goes_at_once(self):
        calls = [f"K{n}ABC" for n in range(10)] + \
            [f"W{n}X{c}" for n in range(10) for c in "ABCDEFGHIJ"]
        self.r.offer(event(*[heard(c) for c in calls]))
        self.run_for(4)
        self.assertGreaterEqual(len(self.sent), 1)
        self.assertTrue(all(len(m) <= p.MAX_DATAGRAM for m in self.sent))
        self.run_for(300)
        got = [rec[1] for m in self.sent
               for rec in parse(m)["records"][p.TX_TEMPLATE]]
        self.assertEqual(sorted(got), sorted(calls))

    def test_templates_in_the_first_three_and_then_hourly(self):
        with_templates = []
        for k in range(16):
            self.r.offer(event(heard(f"K{k % 10}A{chr(65 + k)}")))
            self.run_for(300)
            with_templates.append(bool(parse(self.sent[-1])["templates"]))
        self.assertEqual(with_templates[:4], [True, True, True, False])
        # An hour after the third, which went at 15 minutes: the fifteenth.
        self.assertEqual([k for k, t in enumerate(with_templates) if t],
                         [0, 1, 2, 14])

    def test_the_sequence_counts_reports(self):
        self.r.offer(event(heard("K1ABC"), heard("W9XYZ"), heard("G4ABC")))
        self.run_for(302)
        self.r.offer(event(heard("JA1XYZ")))
        self.run_for(302)
        self.assertEqual([parse(m)["seq"] for m in self.sent], [0, 3])

    def test_what_a_spot_says(self):
        self.r.offer(event(heard("K1ABC", rf=7075234, grid="FN42"),
                           mode="FT4", slot=1_000_007.5))
        self.run_for(302)
        m = parse(self.sent[0])
        rx = m["records"][p.RX_TEMPLATE][0]
        self.assertEqual((rx[2], rx[4], rx[8]),
                         ("VA7HDX", "CN89kg", "hamdash 1.4.0"))
        tx = m["records"][p.TX_TEMPLATE][0]
        self.assertEqual((tx[1], tx[5], tx[10], tx[11], tx[3], tx[150]),
                         ("K1ABC", 7075234, "FT4", 1, "FN42", 1_000_007))

    def test_a_callsign_once_in_five_minutes_and_an_hour_per_band(self):
        self.assertEqual(self.r.offer(event(heard("K1ABC"))), 1)
        self.clock.t += 60
        self.assertEqual(self.r.offer(event(heard("K1ABC"))), 0)
        # Another band inside five minutes: still too soon.
        self.assertEqual(self.r.offer(event(heard("K1ABC", rf=14075000))), 0)
        self.clock.t += 300
        self.assertEqual(self.r.offer(event(heard("K1ABC"))), 0,
                         "same band within the hour")
        self.assertEqual(self.r.offer(event(heard("K1ABC", rf=14075000))), 1,
                         "a change of band is a change")
        self.clock.t += 3600
        self.assertEqual(self.r.offer(event(heard("K1ABC", rf=14075000))), 1)

    def test_never_the_operators_own_call(self):
        for call in ("VA7HDX", "va7hdx", "VA7HDX/P", "VE7/VA7HDX"):
            with self.subTest(call):
                self.assertEqual(self.r.offer(event(heard(call))), 0)
        self.assertEqual(self.r.skipped["own callsign"], 4)

    def test_never_without_a_dial_frequency(self):
        self.assertEqual(self.r.offer(event(heard("K1ABC", rf=None))), 0)
        self.run_for(400)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.r.skipped["no dial frequency"], 1)

    def test_never_what_was_not_heard_for_real(self):
        cases = {
            "simulated": event(heard("K1ABC"), simulated=True),
            "not FT8 or FT4": event(heard("K1ABC"), mode="CW"),
            "not from the decoder": event(heard("K1ABC"), source="ui"),
            "no callsign, or a hashed one": event(heard("<...>")),
            "uncertain decode": event(heard("K1ABC", osd=0.06)),
        }
        for why, evt in cases.items():
            with self.subTest(why):
                self.assertEqual(self.r.offer(evt), 0)
                self.assertGreaterEqual(self.r.skipped.get(why, 0), 1)
        self.assertEqual(self.r.offer(event(heard("<PJ4/K1ABC>"))), 0)
        # A sure OSD decode is a decode.
        self.assertEqual(self.r.offer(event(heard("W9XYZ", osd=0.03))), 1)
        self.run_for(400)
        got = [rec[1] for m in self.sent
               for rec in parse(m)["records"][p.TX_TEMPLATE]]
        self.assertEqual(got, ["W9XYZ"])

    def test_a_failed_send_keeps_the_spots(self):
        def fail(_msg):
            raise OSError("network is unreachable")
        self.r._send = fail
        self.r.offer(event(heard("K1ABC")))
        self.run_for(302)
        self.assertEqual(self.r.status()["queued"], 1)
        self.assertIn("unreachable", self.r.status()["error"])
        self.r._send = self.sent.append
        self.run_for(300)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.r.status()["error"], "")

    def test_turning_it_off_drops_what_was_waiting(self):
        self.r.offer(event(heard("K1ABC")))
        self.r.set_enabled(False)
        self.r.set_enabled(True)
        self.run_for(400)
        self.assertEqual(self.sent, [])

    def test_it_hears_the_decoder_on_the_bus(self):
        bus = Bus()
        bus.listen("decoder.decoded", self.r.offer)
        evt = event(heard("K1ABC"))
        bus.publish("decoder.decoded", evt["payload"], source="decoder")
        bus.publish("decoder.decoded", evt["payload"], source="someone")
        bus.publish("decoder.other", evt["payload"], source="decoder")
        self.assertEqual(self.r.status()["queued"], 1)

    def test_a_listener_that_raises_does_not_break_publishing(self):
        bus = Bus()

        def boom(_evt):
            raise RuntimeError("no")
        bus.listen("x.*", boom)
        self.assertEqual(bus.publish("x.y", {})["topic"], "x.y")


class TestOverTheWire(unittest.TestCase):
    """One real UDP send - to a socket on this machine and nowhere else."""

    def test_a_local_socket_gets_the_datagram(self):
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        rx.settimeout(2)
        self.addCleanup(rx.close)
        clock = Clock()
        r = p.Reporter(lambda: {"callsign": "VA7HDX", "grid": "CN89"},
                       "1.4.0", clock=clock, host="127.0.0.1",
                       port=rx.getsockname()[1])
        self.addCleanup(r.stop)
        r.set_enabled(True)
        r.offer(event(heard("K1ABC")))
        clock.t += 301
        r.tick()
        msg, _ = rx.recvfrom(2048)
        TEMPLATES.clear()
        self.assertEqual(parse(msg)["records"][p.TX_TEMPLATE][0][1], "K1ABC")
        # Every datagram from one source port: the server keys its cached
        # templates on it.
        first = r._sock.getsockname()[1]
        r.offer(event(heard("W9XYZ")))
        clock.t += 300
        r.tick()
        _, addr = rx.recvfrom(2048)
        self.assertEqual(addr[1], first)

    def test_the_real_address_is_pskreporters(self):
        self.assertEqual((p.HOST, p.PORT), ("report.pskreporter.info", 4739))
        r = p.Reporter(lambda: {}, "x")
        self.assertEqual((r.host, r.port), (p.HOST, p.PORT))
        self.assertFalse(r.enabled, "must start off")


class TestItIsOffByDefault(unittest.TestCase):

    def test_the_config_default(self):
        from hamdash import config
        self.assertIs(config.DEFAULTS["pskreporter"]["enabled"], False)


if __name__ == "__main__":
    unittest.main()
