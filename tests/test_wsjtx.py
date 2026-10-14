"""WSJT-X's UDP protocol, and the QSOs it logs.

**The danger in this file is that it could test nothing.** If the bytes
were built by a helper that mirrors the decoder, the two would agree
with each other and both be wrong together - which is exactly how
`bandsBetween` passed its own tests perfectly and threw the moment it
met a real reply.

So the datagrams here are written out as literal bytes with the field
layout spelled out beside them, the way a capture would look, and the
things asserted are the ones that can be checked from outside this
tree:

  * the magic number `0xADBCCBDA`, which is a fixed fact about the
    protocol and is checked at a known byte offset;
  * Qt's string encoding - a `quint32` count then UTF-8, with
    `0xFFFFFFFF` meaning null rather than empty;
  * JDN 2440588 being 1970-01-01, which is arithmetic, not a guess;
  * and that a short or junk datagram cannot take the reader down.

What this cannot prove is that WSJT-X puts the fields in the order
written here. Nothing in this repo can: WSJT-X is not installed on the
machine this was built on. That is proven by an operator pressing Log
QSO once with the UDP server pointed here, the same way PTT was proven
with somebody watching the radio, and until that has happened the
feature is unproven end to end and says so.

Run with:  python -m unittest discover -s tests
"""
import socket
import struct
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hamdash.services import wsjtx


# --------------------------------------------------------------- building
# Written as the protocol describes them rather than by calling anything
# the decoder uses, so a mistake in the decoder cannot hide here.

def qstr(s: str | None) -> bytes:
    """Qt utf8: a big-endian count, then the bytes. None is 0xFFFFFFFF."""
    if s is None:
        return b"\xff\xff\xff\xff"
    raw = s.encode("utf-8")
    return struct.pack(">I", len(raw)) + raw


def header(kind: int, schema: int = 2, ident: str = "WSJT-X") -> bytes:
    return struct.pack(">III", 0xADBCCBDA, schema, kind) + qstr(ident)


def qdatetime(jd: int, ms: int, spec: int = 1, offset: int | None = None) -> bytes:
    out = struct.pack(">q", jd) + struct.pack(">I", ms) + bytes([spec])
    if spec == 2:
        out += struct.pack(">i", offset or 0)
    return out


ADIF_RECORD = (
    "<call:5>VA7XY <gridsquare:4>CN89 <mode:3>FT8 <rst_sent:3>-07 "
    "<rst_rcvd:3>-12 <qso_date:8>20261013 <time_on:6>211500 "
    "<qso_date_off:8>20261013 <time_off:6>211700 <band:3>20m "
    "<freq:9>14.074000 <station_callsign:6>VA7SLL <my_gridsquare:6>DO00HA "
    "<eor>"
)


class TestTheHeader(unittest.TestCase):
    def test_the_magic_number_is_the_first_four_bytes(self):
        packet = header(wsjtx.HEARTBEAT)
        self.assertEqual(packet[:4], b"\xad\xbc\xcb\xda")
        self.assertEqual(struct.unpack(">I", packet[:4])[0], wsjtx.MAGIC)

    def test_anything_that_is_not_wsjtx_is_ignored(self):
        # The port may be a multicast group somebody else is also using,
        # so a stranger's datagram is dropped without comment rather than
        # being parsed into nonsense.
        self.assertIsNone(wsjtx.decode(b"hello there"))
        self.assertIsNone(wsjtx.decode(b"\x00\x00\x00\x00" + b"\x00" * 40))
        self.assertIsNone(wsjtx.decode(b""))
        self.assertIsNone(wsjtx.decode(b"\xad\xbc\xcb"))      # one byte short

    def test_the_type_and_id_come_out(self):
        msg = wsjtx.decode(header(wsjtx.HEARTBEAT, schema=3, ident="JTDX"))
        self.assertEqual(msg["type"], wsjtx.HEARTBEAT)
        self.assertEqual(msg["schema"], 3)
        self.assertEqual(msg["id"], "JTDX")


class TestQtEncoding(unittest.TestCase):
    def test_a_null_string_is_not_an_empty_one_on_the_wire(self):
        self.assertEqual(qstr(None), b"\xff\xff\xff\xff")
        self.assertEqual(qstr(""), b"\x00\x00\x00\x00")
        # Both arrive as "", because nothing downstream wants to tell an
        # absent grid from a blank one - but reading the 0xFFFFFFFF as a
        # length would try to take four gigabytes.
        packet = header(wsjtx.LOGGED_ADIF) + qstr(None)
        self.assertEqual(wsjtx.decode(packet)["adif"], "")

    def test_utf8_survives(self):
        packet = header(wsjtx.LOGGED_ADIF) + qstr("<name:7>Jörgen <eor>")
        self.assertIn("Jörgen", wsjtx.decode(packet)["adif"])

    def test_the_julian_epoch_constant_is_right(self):
        # JDN 2440588 is 1970-01-01. Arithmetic, not a guess - and the
        # whole timestamp hangs off it.
        r = wsjtx._Reader(qdatetime(2440588, 0, 1))
        self.assertEqual(r.when(),
                         datetime(1970, 1, 1, tzinfo=timezone.utc))

    def test_days_and_milliseconds_advance_as_they_should(self):
        a = wsjtx._Reader(qdatetime(2440588 + 1, 0, 1)).when()
        self.assertEqual(a, datetime(1970, 1, 2, tzinfo=timezone.utc))
        b = wsjtx._Reader(qdatetime(2440588, 3_600_000, 1)).when()
        self.assertEqual(b, datetime(1970, 1, 1, 1, 0, tzinfo=timezone.utc))

    def test_an_offset_spec_carries_four_more_bytes(self):
        # Spec 2 has a trailing qint32 and the others do not. Reading it
        # when it is not there - or missing it when it is - shifts every
        # field after the timestamp, which is the misparse that turns a
        # callsign into somebody's grid square.
        plain = qdatetime(2440588, 0, 1)
        offset = qdatetime(2440588, 0, 2, 3600)
        self.assertEqual(len(offset) - len(plain), 4)
        self.assertEqual(wsjtx._Reader(offset).when(),
                         datetime(1969, 12, 31, 23, 0, tzinfo=timezone.utc))

    def test_a_short_read_raises_rather_than_guessing(self):
        with self.assertRaises(wsjtx.Truncated):
            wsjtx._Reader(b"\x00\x01").u32()
        with self.assertRaises(wsjtx.Truncated):
            wsjtx._Reader(struct.pack(">I", 99) + b"abc").string()


class TestTheMessages(unittest.TestCase):
    def test_a_heartbeat_carries_the_version(self):
        packet = (header(wsjtx.HEARTBEAT)
                  + struct.pack(">I", 3) + qstr("2.7.0") + qstr("a1b2c3"))
        msg = wsjtx.decode(packet)
        self.assertEqual(msg["version"], "2.7.0")
        self.assertEqual(msg["max_schema"], 3)

    def test_a_logged_adif_carries_the_record(self):
        packet = header(wsjtx.LOGGED_ADIF) + qstr(ADIF_RECORD)
        msg = wsjtx.decode(packet)
        self.assertEqual(msg["type"], wsjtx.LOGGED_ADIF)
        self.assertIn("VA7XY", msg["adif"])
        self.assertIn("<eor>", msg["adif"])

    def test_a_status_message_reads_the_dial(self):
        packet = (header(wsjtx.STATUS)
                  + struct.pack(">Q", 14074000) + qstr("FT8") + qstr("VA7XY")
                  + qstr("-07") + qstr("FT8")
                  + bytes([1, 0, 1])
                  + struct.pack(">II", 1500, 1500)
                  + qstr("VA7SLL") + qstr("DO00HA") + qstr("CN89"))
        msg = wsjtx.decode(packet)
        self.assertEqual(msg["dial_hz"], 14074000)
        self.assertEqual(msg["mode"], "FT8")
        self.assertEqual(msg["de_call"], "VA7SLL")
        self.assertIs(msg["tx_enabled"], True)
        self.assertIs(msg["transmitting"], False)
        self.assertIs(msg["decoding"], True)

    def test_a_qso_logged_reads_the_contact(self):
        packet = (header(wsjtx.QSO_LOGGED)
                  + qdatetime(2461327, 76_620_000, 1)
                  + qstr("VA7XY") + qstr("CN89")
                  + struct.pack(">Q", 14074000)
                  + qstr("FT8") + qstr("-07") + qstr("-12") + qstr("50")
                  + qstr("") + qstr("Chris")
                  + qdatetime(2461327, 76_500_000, 1)
                  + qstr("VA7SLL") + qstr("VA7SLL") + qstr("DO00HA"))
        msg = wsjtx.decode(packet)
        self.assertEqual(msg["dx_call"], "VA7XY")
        self.assertEqual(msg["dx_grid"], "CN89")
        self.assertEqual(msg["tx_hz"], 14074000)
        self.assertEqual(msg["name"], "Chris")
        self.assertEqual(msg["my_grid"], "DO00HA")
        self.assertLess(msg["on"], msg["off"])

    def test_a_truncated_message_keeps_what_it_read(self):
        # Schemas gain fields on the end. A version that sends fewer must
        # not cost us the ones it did send, and must not throw.
        packet = header(wsjtx.STATUS) + struct.pack(">Q", 7074000) + qstr("FT4")
        msg = wsjtx.decode(packet)
        self.assertTrue(msg["short"])
        self.assertEqual(msg["dial_hz"], 7074000)
        self.assertEqual(msg["mode"], "FT4")

    def test_an_unknown_type_is_still_counted(self):
        # Decodes, Clears and the rest are not unpacked, but they prove
        # the link is alive, which is most of what the status says.
        msg = wsjtx.decode(header(wsjtx.DECODE))
        self.assertEqual(msg["type"], wsjtx.DECODE)


class TestAgainstRealCapturedBytes(unittest.TestCase):
    """Datagrams captured off a live WSJT-X 3.0.2, byte for byte.

    This is the test the rest of the file could not be. Everything else
    here builds its own datagrams, so a mistake in the layout would be
    made identically by the builder and the parser and they would agree
    with each other - the `bandsBetween` trap. These bytes came off the
    wire from WSJT-X itself, so they are the one thing in this file that
    can disagree with the code.

    What they pin: the magic number, Qt's utf8 string encoding, the
    quint64 dial frequency, three consecutive bools, a pair of quint32
    offsets, and the field *order* of Heartbeat and Status.

    What they still do not pin is the field order of `QSO_LOGGED` (5),
    which only fires when a contact is logged. `LOGGED_ADIF` (12) is one
    string after the header and both of those are proven here, so the
    path that actually writes to the log is covered.
    """

    # WSJT-X 3.0.2 heartbeat, exactly as it arrived.
    HEARTBEAT = bytes.fromhex(
        "adbccbda"                      # magic
        "00000002"                      # schema
        "00000000"                      # type 0, Heartbeat
        "00000006" "57534a542d58"       # "WSJT-X"
        "00000003"                      # max schema it understands
        "00000005" "332e302e32"         # "3.0.2"
        "00000006" "636364666166"       # "ccdfaf"
    )

    # The same session, decoding FT8 on 20 m.
    STATUS = bytes.fromhex(
        "adbccbda"
        "00000002"
        "00000001"                      # type 1, Status
        "00000006" "57534a542d58"       # "WSJT-X"
        "0000000000d6c090"              # dial, quint64: 14 074 000 Hz
        "00000003" "465438"             # "FT8"
        "00000006" "454133474957"       # "EA3GIW"
        "00000003" "2d3136"             # "-16"
        "00000003" "465438"             # "FT8"
        "00" "00" "01"                  # tx_enabled, transmitting, decoding
        "0000032c" "0000032c"           # rx df, tx df: 812 Hz
        "00000006" "564137534c4c"       # "VA7SLL"
        "00000006" "444f30304841"       # "DO00HA"
        "00000004" "4a4e3131"           # "JN11"
    )

    def test_the_heartbeat_decodes_as_wsjtx_sends_it(self):
        msg = wsjtx.decode(self.HEARTBEAT)
        self.assertEqual(msg["type"], wsjtx.HEARTBEAT)
        self.assertEqual(msg["id"], "WSJT-X")
        self.assertEqual(msg["schema"], 2)
        self.assertEqual(msg["max_schema"], 3)
        self.assertEqual(msg["version"], "3.0.2")
        self.assertEqual(msg["revision"], "ccdfaf")

    def test_the_status_decodes_as_wsjtx_sends_it(self):
        msg = wsjtx.decode(self.STATUS)
        self.assertEqual(msg["type"], wsjtx.STATUS)
        # 14.074 is the FT8 watering hole, which is what makes this
        # number worth asserting: it is recognisably right, so a
        # mis-sized integer would be obvious rather than plausible.
        self.assertEqual(msg["dial_hz"], 14074000)
        self.assertEqual(msg["mode"], "FT8")
        self.assertEqual(msg["dx_call"], "EA3GIW")
        self.assertEqual(msg["report"], "-16")
        self.assertEqual(msg["tx_mode"], "FT8")
        # Three bools in a row. Reading two, or four, shifts both of the
        # offsets below and every string after them.
        self.assertIs(msg["tx_enabled"], False)
        self.assertIs(msg["transmitting"], False)
        self.assertIs(msg["decoding"], True)
        self.assertEqual(msg["rx_df"], 812)
        self.assertEqual(msg["tx_df"], 812)
        self.assertEqual(msg["de_call"], "VA7SLL")
        self.assertEqual(msg["de_grid"], "DO00HA")
        # EA3 is Spain and JN11 is Barcelona, so the last field being
        # the *DX* grid rather than anything else is checkable from
        # outside this file.
        self.assertEqual(msg["dx_grid"], "JN11")

    def test_a_real_status_is_never_treated_as_a_logged_contact(self):
        # The operator's rule: a contact is logged when they log one,
        # not when WSJT-X decodes one. A live session sends hundreds of
        # these and not one may reach the log.
        for raw in (self.HEARTBEAT, self.STATUS):
            self.assertNotEqual(wsjtx.decode(raw)["type"], wsjtx.LOGGED_ADIF)
            self.assertNotIn("adif", wsjtx.decode(raw))


class TestTheListener(unittest.TestCase):
    """A real socket, real datagrams, and the thread that reads them."""

    def setUp(self):
        self.got = []
        # Ask the OS for a free port and give it straight back, rather
        # than picking one and hoping.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.listener = wsjtx.Listener(
            "127.0.0.1", self.port,
            on_adif=lambda text: (self.got.append(text),
                                  {"added": 1, "updated": 0})[1],
            log=lambda line: None)

    def tearDown(self):
        self.listener.stop()

    def send(self, packet: bytes) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(packet, ("127.0.0.1", self.port))
        s.close()

    def wait_for(self, check, timeout=3.0):
        end = time.time() + timeout
        while time.time() < end:
            if check():
                return True
            time.sleep(0.02)
        return False

    def test_a_logged_contact_reaches_the_log(self):
        self.assertTrue(self.listener.start())
        self.send(header(wsjtx.LOGGED_ADIF) + qstr(ADIF_RECORD))
        self.assertTrue(self.wait_for(lambda: self.got), "nothing was logged")
        self.assertIn("VA7XY", self.got[0])
        self.assertEqual(self.listener.status()["logged"], 1)

    def test_the_status_tells_the_three_states_apart(self):
        # Not listening at all.
        self.assertFalse(self.listener.status()["listening"])
        # Listening, but WSJT-X has never said anything. This is the
        # common case when something is wrong - the port is fine and the
        # other end points somewhere else - and a bare "enabled" flag
        # cannot express it.
        self.listener.start()
        s = self.listener.status()
        self.assertTrue(s["listening"])
        self.assertFalse(s["connected"])
        self.assertEqual(s["version"], "")
        # Listening and it is there.
        self.send(header(wsjtx.HEARTBEAT) + struct.pack(">I", 3)
                  + qstr("2.7.0") + qstr("rev"))
        self.assertTrue(self.wait_for(
            lambda: self.listener.status()["connected"]))
        self.assertEqual(self.listener.status()["version"], "2.7.0")

    def test_rubbish_on_the_port_cannot_kill_the_reader(self):
        # The thread dying is the failure that looks like nothing: the
        # process stays up, the status still says listening, and QSOs
        # quietly stop being logged.
        self.listener.start()
        for junk in (b"", b"\x00", b"not wsjt-x at all",
                     b"\xad\xbc\xcb\xda" + b"\xff" * 3,
                     header(wsjtx.QSO_LOGGED) + b"\x01\x02\x03",
                     bytes(range(256))):
            self.send(junk)
        self.send(header(wsjtx.LOGGED_ADIF) + qstr(ADIF_RECORD))
        self.assertTrue(self.wait_for(lambda: self.got),
                        "the reader stopped after junk")

    def test_a_handler_that_throws_cannot_kill_the_reader_either(self):
        def boom(_text):
            raise RuntimeError("the log is on fire")

        self.listener.on_adif = boom
        self.listener.start()
        self.send(header(wsjtx.LOGGED_ADIF) + qstr(ADIF_RECORD))
        self.send(header(wsjtx.HEARTBEAT) + struct.pack(">I", 3)
                  + qstr("2.7.0") + qstr("rev"))
        self.assertTrue(self.wait_for(
            lambda: self.listener.status()["version"] == "2.7.0"),
            "a throwing handler took the thread with it")

    def test_a_port_already_taken_says_what_is_probably_holding_it(self):
        held = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        held.bind(("127.0.0.1", self.port))
        second = wsjtx.Listener("127.0.0.1", self.port, log=lambda x: None)
        try:
            self.assertFalse(second.start())
            self.assertFalse(second.listening)
            # Names the likely culprits and a way out, not just errno.
            self.assertIn("JTAlert", second.error)
            self.assertIn(str(self.port), second.error)
            # **Another hamdash comes first in that list**, because it is
            # the one nobody suspects. It happened: a leftover `--home`
            # instance from a different session held 2237 for a quarter
            # of an hour while the real station retried and the operator
            # read "JTAlert" and went looking at a program that was not
            # even installed.
            self.assertIn("hamdash", second.error)
            self.assertIn("--home", second.error)
            # And it has not given up - False from start() means "not
            # yet", which is the whole point of the retry below.
            self.assertTrue(second.retrying)
        finally:
            second.stop()
            held.close()

    def test_it_keeps_trying_and_takes_the_port_when_it_is_freed(self):
        """The reason the thread outlives a refused bind.

        JTAlert and GridTracker hold 2237 and get closed during a
        session. Requiring a restart of hamdash to notice is the wrong
        answer - and the failure is silent, because the status would go
        on saying the port is busy long after it was not.
        """
        held = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        held.bind(("127.0.0.1", self.port))
        was, wsjtx.RETRY_S = wsjtx.RETRY_S, 0.1
        try:
            self.assertFalse(self.listener.start())
            self.assertTrue(self.listener.retrying)
            self.assertFalse(self.listener.listening)

            held.close()                      # the other program quits
            self.assertTrue(
                self.wait_for(lambda: self.listener.listening, timeout=5),
                "it never took the port after it was freed")
            self.assertEqual(self.listener.error, "")

            # And it is a working listener, not merely a bound socket.
            self.send(header(wsjtx.LOGGED_ADIF) + qstr(ADIF_RECORD))
            self.assertTrue(self.wait_for(lambda: self.got))
        finally:
            wsjtx.RETRY_S = was
            try:
                held.close()
            except OSError:
                pass

    def test_stopping_is_idempotent_and_releases_the_port(self):
        self.listener.start()
        self.listener.stop()
        self.listener.stop()
        self.assertFalse(self.listener.listening)
        again = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            again.bind(("127.0.0.1", self.port))   # would raise if still held
        finally:
            again.close()

    def test_only_the_adif_message_writes_to_the_log(self):
        # WSJT-X sends both for one contact. Logging both would put the
        # same QSO through twice - the importer's dedupe would catch it,
        # but relying on that to cover a double write is not a design.
        self.listener.start()
        self.send(header(wsjtx.QSO_LOGGED)
                  + qdatetime(2461327, 76_620_000, 1)
                  + qstr("VA7XY") + qstr("CN89")
                  + struct.pack(">Q", 14074000)
                  + qstr("FT8") + qstr("-07") + qstr("-12") + qstr("50")
                  + qstr("") + qstr("")
                  + qdatetime(2461327, 76_500_000, 1)
                  + qstr("VA7SLL") + qstr("VA7SLL") + qstr("DO00HA"))
        self.assertTrue(self.wait_for(
            lambda: self.listener.status()["qsos"] == 1))
        self.assertEqual(self.got, [])
        self.assertEqual(self.listener.status()["logged"], 0)
        # And it says so, because a version too old to send ADIF would
        # otherwise look exactly like nothing being logged for no reason.
        self.assertTrue(self.listener.status()["adif_missing"])


class TestItReachesTheRealLogbook(unittest.TestCase):
    """End to end against the actual importer, not a stand-in."""

    def test_a_wsjtx_record_lands_in_the_log_and_dedupes(self):
        from hamdash.services.logbook import Logbook

        home = Path(tempfile.mkdtemp(prefix="hamdash-wsjtx-"))
        book = Logbook(home / "log.db")

        first = book.import_adif(ADIF_RECORD)
        self.assertEqual(first["added"], 1)

        rows = book.search("VA7XY")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["call"], "VA7XY")
        self.assertEqual(rows[0]["mode"], "FT8")
        self.assertEqual(rows[0]["band"], "20m")

        # The same contact again - WSJT-X resending, or the operator
        # logging it in two places - must not become two QSOs.
        again = book.import_adif(ADIF_RECORD)
        self.assertEqual(again["added"], 0)
        self.assertEqual(len(book.search("VA7XY")), 1)


if __name__ == "__main__":
    unittest.main()
