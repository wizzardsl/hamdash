"""A contact from WSJT-X is filled in from a lookup, like a typed one.

The bug this guards: contacts typed into the logbook card go through its
prefill, which copies name, QTH, country and state from the callsign
lookup. Contacts from WSJT-X went straight into the log as WSJT-X sent
them - call, grid, reports, frequency - so they had none of that.
Reported as Canadian contacts with a blank province (VE4FX, VA5KEN) when
the lookup answered MB and SK for them; a US contact logged the same way
was just as blank, so it was never about Canada.

Run with:  python -m unittest discover -s tests
"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from hamdash.services.logbook import fill_blanks

# As WSJT-X 3.0.2 sends one, trimmed to the fields that matter here.
VE4FX = ("<call:5>VE4FX <gridsquare:4>EN19 <mode:3>FT8 <rst_sent:3>-08 "
         "<rst_rcvd:3>-18 <qso_date:8>20261017 <time_on:6>173430 "
         "<qso_date_off:8>20261017 <time_off:6>173556 <band:3>20m "
         "<freq:9>14.075323 <station_callsign:6>VA7SLL "
         "<my_gridsquare:6>DO00HA <tx_pwr:2>50 <eor>")

QRZ_SAYS = {"ok": True, "callsign": "VE4FX", "source": "qrz",
            "approximate": False, "name": "Stephen C Keith",
            "qth": "Winnipeg", "country": "Canada", "state": "MB",
            "locator": "EN19KT"}


class TestFillBlanks(unittest.TestCase):
    def test_the_province_comes_from_the_lookup(self):
        got = fill_blanks({"call": "VE4FX"}, QRZ_SAYS)
        self.assertEqual(got["state"], "MB")
        self.assertEqual((got["name"], got["qth"], got["country"]),
                         ("Stephen C Keith", "Winnipeg", "Canada"))

    def test_what_the_record_carries_wins(self):
        got = fill_blanks({"call": "VE4FX", "state": "ON"}, QRZ_SAYS)
        self.assertEqual(got["state"], "ON")

    def test_the_worked_grid_is_not_replaced(self):
        """The locator sent over the air is where they were; the lookup's
        is where the licence is registered."""
        got = fill_blanks({"call": "VE4FX", "gridsquare": "EN19"}, QRZ_SAYS)
        self.assertEqual(got["gridsquare"], "EN19")
        got = fill_blanks({"call": "VE4FX"}, QRZ_SAYS)
        self.assertNotIn("gridsquare", got)

    def test_a_prefix_guess_gives_the_country_only(self):
        guess = {"ok": True, "approximate": True, "country": "Canada",
                 "state": "MB", "name": "x", "qth": "y"}
        got = fill_blanks({"call": "VE4FX"}, guess)
        self.assertEqual(got.get("country"), "Canada")
        for field in ("state", "name", "qth"):
            self.assertFalse(got.get(field), field)

    def test_a_failed_lookup_fills_nothing(self):
        got = fill_blanks({"call": "VE4FX"}, {"ok": False, "state": "MB"})
        self.assertFalse(got.get("state"))

    def test_my_callsign_from_wsjtx_or_from_settings(self):
        self.assertEqual(fill_blanks({"call": "A", "station_callsign": "va7sll"},
                                     {})["my_callsign"], "VA7SLL")
        self.assertEqual(fill_blanks({"call": "A"}, {}, "VA7SLL")["my_callsign"],
                         "VA7SLL")


class FakeLookup:
    def __init__(self, answer=None, delay=0.0, boom=False):
        self.answer, self.delay, self.boom = answer, delay, boom
        self.asked = []

    def lookup(self, call, refresh=False):
        self.asked.append(call)
        if self.delay:
            time.sleep(self.delay)
        if self.boom:
            raise RuntimeError("provider down")
        return dict(self.answer or {})


class TestTheWsjtxPath(unittest.TestCase):
    """Through the app's own handler, into a real logbook."""

    def host(self, fake):
        home = Path(tempfile.mkdtemp(prefix="hamdash-wsjtx-fill-"))
        (home / "config.json").write_text(json.dumps({
            "station": {"callsign": "VA7SLL", "grid": "DO00HA"},
            "radio": {"mock": True}, "wsjtx": {"enabled": False},
        }), encoding="utf-8")
        (home / "layout.json").write_text("[]", encoding="utf-8")
        seen = {}

        class Capture:
            def __init__(self, on_adif=None, **kw):
                seen["on_adif"] = on_adif
                self.listening = self.retrying = False
                self.bind, self.port = "127.0.0.1", 2237

            def start(self):
                return False

            def stop(self):
                pass

            def status(self):
                return {}

        from hamdash.app import create_app
        from hamdash.config import Config
        with mock.patch("hamdash.app.wsjtxsvc.Listener", Capture), \
             mock.patch("hamdash.app._make_lookup", return_value=fake):
            app, shutdown = create_app(Config(home), port=0)
        self.addCleanup(lambda: shutdown() if callable(shutdown) else None)
        return seen["on_adif"], app.test_client()

    def logged(self, client):
        return client.get("/api/log").get_json()[0]

    def test_a_canadian_contact_gets_its_province(self):
        on_adif, client = self.host(FakeLookup(QRZ_SAYS))
        on_adif(VE4FX)
        row = self.logged(client)
        self.assertEqual(row["state"], "MB")
        self.assertEqual(row["qth"], "Winnipeg")
        self.assertEqual(row["gridsquare"], "EN19")
        self.assertEqual(row["my_callsign"], "VA7SLL")

    def test_a_slow_lookup_does_not_hold_the_contact(self):
        """Logged without it, promptly, rather than after every provider
        has timed out in turn."""
        fake = FakeLookup(QRZ_SAYS, delay=2.0)
        on_adif, client = self.host(fake)
        with mock.patch("hamdash.app.ENRICH_WAIT", 0.2):
            t0 = time.time()
            on_adif(VE4FX)
            took = time.time() - t0
        self.assertLess(took, 1.5)
        row = self.logged(client)
        self.assertEqual(row["call"], "VE4FX")
        self.assertFalse(row.get("state"))

    def test_a_failing_lookup_still_logs_the_contact(self):
        on_adif, client = self.host(FakeLookup(boom=True))
        on_adif(VE4FX)
        self.assertEqual(self.logged(client)["call"], "VE4FX")


if __name__ == "__main__":
    unittest.main()
