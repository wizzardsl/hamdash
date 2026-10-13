"""PSK Reporter's live feed, over a hand-rolled MQTT subscriber.

`mqtt.py` imports nothing from the card, so this drives it with bytes -
no network, no broker. That matters more than usual here because the
failure mode is not an exception: MQTT is a *stream*, one `recv` can
carry half a packet or three, and reading the wrong number of bytes
desynchronises everything after it. What that looks like is garbled
topics and JSON with two bytes of binary on the front, not a crash.

Found exactly that way: the first version read two bytes of SUBACK when
it is three, and every packet after it was shifted by one.

Run with:  python -m unittest discover -s tests
"""
import importlib.util
import json
import struct
import unittest
from pathlib import Path

CARD = Path(__file__).resolve().parent.parent / "plugins" / "psk_reporter"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"psk_{name}",
                                                  CARD / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mqtt = _load("mqtt")

# A real spot, copied off the live feed.
SPOT = {"sq": 73665028629, "f": 14076550, "md": "FT8", "rp": -17,
        "t": 1791866549, "t_tx": 1791866535, "sc": "VA7ZT",
        "sl": "DN19di41", "rc": "BG2MGR", "rl": "PN23pv",
        "sa": 1, "ra": 318, "b": "20m"}
TOPIC = "pskr/filter/v2/20m/FT8/VA7ZT/BG2MGR/DN19/PN23/1/318"


def publish(topic, payload, qos=0):
    """A PUBLISH packet as the broker would send it."""
    body = (struct.pack("!H", len(topic.encode())) + topic.encode()
            + (b"\x00\x01" if qos else b"") + payload.encode())
    first = (mqtt.PUBLISH << 4) | (qos << 1)
    return bytes([first]) + mqtt._varint(len(body)) + body


class TestTheWireFormat(unittest.TestCase):
    def test_remaining_length_is_seven_bits_a_byte(self):
        """The one piece of MQTT that is not obvious, and the one that
        breaks everything after it when it is wrong."""
        self.assertEqual(mqtt._varint(0), b"\x00")
        self.assertEqual(mqtt._varint(127), b"\x7f")
        self.assertEqual(mqtt._varint(128), b"\x80\x01")
        self.assertEqual(mqtt._varint(16383), b"\xff\x7f")
        self.assertEqual(mqtt._varint(16384), b"\x80\x80\x01")

    def test_connect_declares_mqtt_3_1_1_and_a_clean_session(self):
        p = mqtt.connect_packet("hamdash-test")
        self.assertEqual(p[0] >> 4, mqtt.CONNECT)
        self.assertIn(b"MQTT", p)
        self.assertIn(b"hamdash-test", p)
        body = p[2:]
        self.assertEqual(body[6], 4, "protocol level should be 3.1.1")
        self.assertEqual(body[7] & 0x02, 0x02, "clean session")

    def test_subscribe_sets_the_mandatory_flag_bit(self):
        """SUBSCRIBE must have 0x02 in the fixed header. Without it a
        broker is entitled to drop the connection, which presents as a
        feed that connects and then silently never delivers."""
        p = mqtt.subscribe_packet(["a/b"])
        self.assertEqual(p[0] >> 4, mqtt.SUBSCRIBE)
        self.assertEqual(p[0] & 0x0F, 0x02)

    def test_several_topics_go_in_one_subscribe(self):
        p = mqtt.subscribe_packet(["a/b", "c/d"])
        self.assertIn(b"a/b", p)
        self.assertIn(b"c/d", p)


class TestTheStreamIsNotAMessage(unittest.TestCase):
    """One recv is not one packet, in either direction."""

    def packets(self, *chunks):
        r = mqtt.Reader()
        got = []
        for c in chunks:
            r.feed(c)
            got.extend(r.packets())
        return got

    def test_three_packets_in_one_read(self):
        blob = (publish(TOPIC, json.dumps(SPOT))
                + publish(TOPIC, json.dumps(SPOT))
                + publish(TOPIC, json.dumps(SPOT)))
        self.assertEqual(len(self.packets(blob)), 3)

    def test_one_packet_split_across_reads(self):
        p = publish(TOPIC, json.dumps(SPOT))
        for cut in (1, 2, 5, len(p) // 2, len(p) - 1):
            with self.subTest(cut=cut):
                got = self.packets(p[:cut], p[cut:])
                self.assertEqual(len(got), 1, f"lost a packet split at {cut}")

    def test_a_partial_packet_is_kept_not_dropped(self):
        p = publish(TOPIC, json.dumps(SPOT))
        r = mqtt.Reader()
        r.feed(p[:4])
        self.assertEqual(list(r.packets()), [])
        r.feed(p[4:])
        self.assertEqual(len(list(r.packets())), 1)

    def test_a_long_payload_uses_a_multibyte_length(self):
        """Over 127 bytes the remaining length is two bytes, which is
        where an off-by-one stops being theoretical."""
        big = json.dumps(dict(SPOT, note="x" * 400))
        got = self.packets(publish(TOPIC, big))
        self.assertEqual(len(got), 1)
        _, flags, body = got[0]
        topic, payload = mqtt.publish_parts(body, flags)
        self.assertEqual(topic, TOPIC)
        self.assertEqual(json.loads(payload)["note"], "x" * 400)


class TestReadingASpot(unittest.TestCase):
    def test_topic_and_payload_come_apart_cleanly(self):
        r = mqtt.Reader()
        r.feed(publish(TOPIC, json.dumps(SPOT)))
        kind, flags, body = list(r.packets())[0]
        self.assertEqual(kind, mqtt.PUBLISH)
        topic, payload = mqtt.publish_parts(body, flags)
        self.assertEqual(topic, TOPIC)
        self.assertEqual(json.loads(payload)["sc"], "VA7ZT")

    def test_a_qos1_packet_id_is_not_read_as_json(self):
        """We subscribe at QoS 0, so there should be no packet id. If one
        arrives and is not skipped, two bytes of binary land on the front
        of the payload and it stops being JSON."""
        r = mqtt.Reader()
        r.feed(publish(TOPIC, json.dumps(SPOT), qos=1))
        kind, flags, body = list(r.packets())[0]
        topic, payload = mqtt.publish_parts(body, flags)
        self.assertEqual(topic, TOPIC)
        self.assertEqual(json.loads(payload)["sc"], "VA7ZT")


class TestNothingOnTheSocketEndsTheReader(unittest.TestCase):
    """The lesson the SDK's bus reader already taught this tree."""

    def feed_one(self, packet):
        got = []
        f = mqtt.Feed(["x/#"], got.append)
        r = mqtt.Reader()
        r.feed(packet)
        for kind, flags, body in r.packets():
            if kind == mqtt.PUBLISH:
                f._deliver(flags, body)
        return f, got

    def test_a_payload_that_is_not_json_is_dropped(self):
        f, got = self.feed_one(publish(TOPIC, "<html>down for maintenance"))
        self.assertEqual(got, [])
        self.assertEqual(f.spots, 0)

    def test_a_payload_that_is_json_but_not_an_object_is_dropped(self):
        f, got = self.feed_one(publish(TOPIC, "[1, 2, 3]"))
        self.assertEqual(got, [])

    def test_a_handler_that_raises_does_not_kill_the_feed(self):
        f = mqtt.Feed(["x/#"], lambda s: (_ for _ in ()).throw(RuntimeError()))
        r = mqtt.Reader()
        r.feed(publish(TOPIC, json.dumps(SPOT)))
        for kind, flags, body in r.packets():
            f._deliver(flags, body)             # must not raise
        self.assertEqual(f.spots, 1)

    def test_the_topic_rides_along_with_the_spot(self):
        f, got = self.feed_one(publish(TOPIC, json.dumps(SPOT)))
        self.assertEqual(got[0]["topic"], TOPIC)
        self.assertEqual(got[0]["b"], "20m")


class TestWhatToSubscribeTo(unittest.TestCase):
    def test_a_callsign_asks_both_directions_separately(self):
        t = mqtt.topics_for(call="VA7SLL")
        self.assertEqual(len(t), 2)
        self.assertIn("pskr/filter/v2/+/+/VA7SLL/+/+/+/+/+", t)
        self.assertIn("pskr/filter/v2/+/+/+/VA7SLL/+/+/+/+", t)

    def test_one_direction_when_that_is_all_that_is_wanted(self):
        self.assertEqual(mqtt.topics_for(call="VA7SLL", want="tx"),
                         ["pskr/filter/v2/+/+/VA7SLL/+/+/+/+/+"])

    def test_a_callsign_is_upper_case_and_trimmed(self):
        self.assertIn("VA7SLL", mqtt.topics_for(call="  va7sll ")[0])

    def test_a_grid_is_the_fallback_and_is_four_characters(self):
        """The topic carries four-character squares, so a six-character
        locator subscribed verbatim matches nothing at all - which looks
        exactly like a quiet band."""
        t = mqtt.topics_for(grid="CN89kg")
        self.assertEqual(t, ["pskr/filter/v2/+/+/+/+/+/CN89/+/+"])

    def test_nothing_to_go_on_subscribes_to_nothing(self):
        """Not the whole world: that is thousands of spots a second and
        none of them yours."""
        self.assertEqual(mqtt.topics_for(), [])


if __name__ == "__main__":
    unittest.main()
