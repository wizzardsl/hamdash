"""The log says when it has changed, and says it once.

`log.qso_added` has been published by `Logbook.add` all along, and since
`import_adif` adds through `add`, every writer already announced itself.
That is worth writing down because it is easy to look at the *routes* in
`app.py`, see no publish, and add one - which is a second event per
contact and every logbook tile refreshing twice.

What was actually wrong was the other end of the same wire, twice:

  * an import of a file published one event **per contact**, and
    subscriber queues are 256 deep, so a few thousand QSOs dropped every
    open page's stream as a stalled consumer part-way through its own
    import;
  * and the card refreshed *in place*, so a new contact - which sorts
    onto the first page - was invisible to an operator who had paged
    back through the log. That is how it was reported: the contact was
    in the log, and the card went on showing the old list.

Run with:  python -m unittest discover -s tests
"""
import json
import tempfile
import unittest
from pathlib import Path

from hamdash.bus import BUS
from hamdash.services.logbook import Logbook


def an_adif(n: int) -> str:
    """`n` contacts that are genuinely distinct.

    The length in an ADIF tag is authoritative, so `<call:6>` on a
    seven-character callsign silently truncates it - which collapsed
    TEST150 onto TEST15 and had the importer correctly dedupe contacts
    this fixture meant to be different. The lengths are computed here
    rather than written down for that reason.
    """
    out = []
    for i in range(n):
        call = f"T{i:05d}"                       # always six
        hh, mm = divmod(i, 60)
        time_on = f"{hh % 24:02d}{mm:02d}00"
        out.append(f"<call:{len(call)}>{call} <mode:3>FT8 "
                   f"<qso_date:8>20261013 "
                   f"<time_on:{len(time_on)}>{time_on} <band:3>20m <eor>")
    return "".join(out)


class Heard:
    """Events on one topic, by wrapping publish.

    Not `BUS.recent`: the bus is a module-level singleton, so its history
    outlives a test and one test's contacts would be counted by the next.
    """

    def __init__(self, topic="log.qso_added"):
        self.topic = topic
        self.events = []

    def __enter__(self):
        self._real = BUS.publish

        def spy(topic, payload=None, source="host"):
            if topic == self.topic:
                self.events.append(payload or {})
            return self._real(topic, payload, source)

        BUS.publish = spy
        return self

    def __exit__(self, *_):
        BUS.publish = self._real


def a_log() -> Logbook:
    return Logbook(Path(tempfile.mkdtemp(prefix="hamdash-logevt-")) / "log.db")


class TestOneContact(unittest.TestCase):
    def test_adding_one_announces_it(self):
        book = a_log()
        with Heard() as h:
            book.add({"call": "VA7XY", "band": "20m", "mode": "FT8"})
        self.assertEqual(len(h.events), 1)
        self.assertEqual(h.events[0]["call"], "VA7XY")

    def test_a_refused_contact_announces_nothing(self):
        book = a_log()
        with Heard() as h:
            with self.assertRaises(ValueError):
                book.add({})                      # no callsign
        self.assertEqual(h.events, [])

    def test_a_bulk_caller_can_ask_for_silence(self):
        book = a_log()
        with Heard() as h:
            book.add({"call": "VA7XY", "band": "20m"}, notify=False)
        self.assertEqual(h.events, [])
        # ...and the contact is still in the log. Quiet, not skipped.
        self.assertEqual(len(book.search("VA7XY")), 1)


class TestAnImport(unittest.TestCase):
    def test_a_file_of_contacts_is_one_event_not_hundreds(self):
        """The bug this exists to stop coming back.

        Subscriber queues are `QUEUE_DEPTH` (256) deep and a full queue
        drops the subscriber, so an import big enough to overrun them
        disconnected every open page in the middle of its own import.
        """
        book = a_log()
        with Heard() as h:
            res = book.import_adif(an_adif(300))
        self.assertEqual(res["added"], 300)
        self.assertEqual(len(h.events), 1,
                         f"{len(h.events)} events for one import")

    def test_the_event_carries_a_contact_and_the_counts(self):
        # A contact, so `payload["call"]` keeps working - that is the
        # shape `add` has always published and what docs/cards.md shows
        # an author reading. The counts as well, for a file.
        book = a_log()
        with Heard() as h:
            book.import_adif(an_adif(3))
        p = h.events[0]
        self.assertRegex(p["call"], r"^T\d{5}$")
        self.assertEqual(p["band"], "20m")
        self.assertEqual(p["added"], 3)
        self.assertEqual(p["updated"], 0)

    def test_one_record_looks_exactly_like_add(self):
        # The WSJT-X case, which is one record per contact.
        book = a_log()
        with Heard() as h:
            book.import_adif(
                "<call:5>JA1AB <mode:3>FT8 <qso_date:8>20261013 "
                "<time_on:6>211500 <band:3>20m <eor>")
        self.assertEqual(len(h.events), 1)
        self.assertEqual(h.events[0]["call"], "JA1AB")
        self.assertEqual(h.events[0]["added"], 1)

    def test_an_import_that_changed_nothing_says_nothing(self):
        # A tile redrawing for an import that added no rows is a fetch
        # and a flicker for no reason.
        book = a_log()
        text = an_adif(5)
        book.import_adif(text)
        with Heard() as h:
            res = book.import_adif(text)
        self.assertEqual(res["added"], 0)
        self.assertEqual(h.events, [])

    def test_an_import_that_only_updated_still_says_so(self):
        # A QSL pull usually adds nothing and updates plenty, and the
        # confirmation columns change - so the tiles need telling.
        book = a_log()
        book.import_adif("<call:5>JA1AB <mode:3>FT8 <qso_date:8>20261013 "
                         "<time_on:6>211500 <band:3>20m <eor>")
        with Heard() as h:
            res = book.import_adif(
                "<call:5>JA1AB <mode:3>FT8 <qso_date:8>20261013 "
                "<time_on:6>211500 <band:3>20m <gridsquare:4>PM95 <eor>")
        self.assertEqual(res["updated"], 1)
        self.assertEqual(len(h.events), 1)


class TestTheCardActsOnIt(unittest.TestCase):
    """The event is useless if nothing listens, or if listening is not
    enough to put the contact on screen."""

    def setUp(self):
        self.card = Path(__file__).resolve().parent.parent / "plugins" / "logbook"
        self.page = (self.card / "index.html").read_text(encoding="utf-8")

    def test_the_manifest_subscribes_to_it(self):
        manifest = json.loads((self.card / "card.json").read_text())
        self.assertIn("log.qso_added", manifest.get("subscribes", []),
                      "the card would never be sent the event")

    def test_it_goes_back_to_the_first_page(self):
        # Refreshing in place leaves an operator who had paged back
        # looking at the same page while the contact sits on page one.
        handler = self.page.split("HD.on('log.qso_added'", 1)[1][:200]
        self.assertIn("offset = 0", handler,
                      "the card refreshes but never shows the new contact")

    def test_it_leaves_an_active_search_alone(self):
        # A search is an explicit question; clearing it would answer a
        # different one.
        handler = self.page.split("HD.on('log.qso_added'", 1)[1][:200]
        self.assertIn("#q", handler)


if __name__ == "__main__":
    unittest.main()
