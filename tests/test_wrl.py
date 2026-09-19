"""World Radio League sync.

Untested against the live service - there is no WRL account here. The HTTP
shapes come from their published OpenAPI document and the tests mock them, so
what this guards is the translation and the paging, not their server.

The translation is the interesting part: WRL is the one logbook here that does
not speak ADIF over the wire. It keeps a single instant where ADIF keeps a
date and a time, camelCases its fields, and will take a band as a number of
metres or as a name.

Run with:  python -m unittest discover -s tests
"""
import unittest
from unittest import mock

import requests

from hamdash.services.qsl import (SyncError, WrlLogbook, adif_record, as_contact,
                                  as_qso, mid_of)


def a_reply(data=None, meta=None, error=None, status=200):
    r = mock.Mock(status_code=status, headers={})
    r.json.return_value = {"data": data, "meta": meta or {}, "error": error}
    return r


class TestToWrl(unittest.TestCase):
    """A logged QSO as the JSON they want."""

    def a_qso(self, **over):
        row = {"call": "ja0rug", "qso_date": "20260918", "time_on": "1234",
               "freq": "14.074", "band": "20m", "mode": "ft8"}
        row.update(over)
        return as_contact(row, my_callsign="VA7SLL")

    def test_the_required_fields_are_all_there(self):
        got = self.a_qso()
        for key in ("programId", "call", "timestamp", "freq", "band", "mode"):
            self.assertIn(key, got, f"{key} is required by the API")

    def test_date_and_time_become_one_utc_instant(self):
        """Amateur radio logs UTC by convention, so an instant without a zone
        would be read as local and be silently wrong."""
        self.assertEqual(self.a_qso()["timestamp"], "2026-09-18T12:34:00Z")

    def test_a_callsign_is_normalised(self):
        self.assertEqual(self.a_qso()["call"], "JA0RUG")

    def test_the_station_callsign_falls_back_to_mine(self):
        self.assertEqual(self.a_qso()["stationCallsign"], "VA7SLL")

    def test_my_callsign_on_the_qso_wins(self):
        """A QSO logged portable was not made from the home station."""
        self.assertEqual(self.a_qso(my_callsign="VA7SLL/7")["stationCallsign"],
                         "VA7SLL/7")

    def test_a_band_with_no_frequency_gets_one(self):
        """They require both; a QSO logged by band alone still has a place on
        the dial, and the middle of the band is honest about being a guess."""
        got = self.a_qso(freq="", band="40m")
        self.assertEqual(got["band"], "40m")
        self.assertAlmostEqual(got["freq"], 7.15, places=2)

    def test_a_frequency_with_no_band_gets_one(self):
        self.assertEqual(self.a_qso(band="")["band"], "20m")

    def test_neither_is_refused_by_name(self):
        with self.assertRaises(SyncError) as got:
            self.a_qso(freq="", band="")
        self.assertIn("JA0RUG", str(got.exception))

    def test_a_qso_with_no_date_is_refused_by_name(self):
        with self.assertRaises(SyncError) as got:
            self.a_qso(qso_date="")
        self.assertIn("JA0RUG", str(got.exception))

    def test_empty_fields_are_left_out_rather_than_sent_blank(self):
        self.assertNotIn("notes", self.a_qso(comment=""))

    def test_the_fields_that_do_carry_over(self):
        got = self.a_qso(rst_sent="-05", rst_rcvd="-13", gridsquare="PM97",
                         comment="nice chat", name="Taro", state="WA")
        self.assertEqual(got["rstSent"], "-05")
        self.assertEqual(got["rstRcvd"], "-13")
        self.assertEqual(got["gridsquare"], "PM97")
        self.assertEqual(got["notes"], "nice chat")
        self.assertEqual(got["name"], "Taro")
        self.assertEqual(got["state"], "WA")


class TestFromWrl(unittest.TestCase):
    """One of their contacts in this log's own field names."""

    def a_contact(self, **over):
        c = {"call": "VE7SL", "timestamp": "2026-09-18T12:34:56Z",
             "freq": 14.074, "band": 20, "mode": "FT8"}
        c.update(over)
        return as_qso(c)

    def test_the_instant_splits_back_into_date_and_time(self):
        got = self.a_contact()
        self.assertEqual(got["qso_date"], "20260918")
        self.assertEqual(got["time_on"], "1234")

    def test_a_band_in_metres_becomes_a_band_name(self):
        self.assertEqual(self.a_contact()["band"], "20m")

    def test_centimetre_bands_survive_the_trip(self):
        self.assertEqual(self.a_contact(band=0.7, freq=432.1)["band"], "70cm")

    def test_a_band_alone_is_kept(self):
        """The bug this guards: a conditional that reached for the frequency
        first threw away a perfectly good band whenever there was no
        frequency."""
        self.assertEqual(self.a_contact(band="40m", freq=None)["band"], "40m")

    def test_an_unknown_band_defers_to_the_frequency(self):
        self.assertEqual(self.a_contact(band=999, freq=7.03)["band"], "40m")

    def test_notes_become_the_comment(self):
        self.assertEqual(self.a_contact(notes="hi")["comment"], "hi")

    def test_a_round_trip_keeps_what_matters(self):
        row = {"call": "JA0RUG", "qso_date": "20260918", "time_on": "1234",
               "freq": "14.074000", "band": "20m", "mode": "FT8",
               "rst_sent": "-05", "rst_rcvd": "-13", "gridsquare": "PM97"}
        back = as_qso(as_contact(row, my_callsign="VA7SLL"))
        for key in ("call", "qso_date", "time_on", "band", "mode",
                    "rst_sent", "rst_rcvd", "gridsquare"):
            self.assertEqual(back[key], row[key], key)

    def test_it_renders_as_adif_the_importer_can_dedupe(self):
        rec = adif_record(self.a_contact())
        self.assertIn("<CALL:5>VE7SL", rec)
        self.assertIn("<QSO_DATE:8>20260918", rec)
        self.assertTrue(rec.endswith("<EOR>"))


class TestMidOf(unittest.TestCase):
    def test_it_knows_the_bands(self):
        self.assertAlmostEqual(mid_of("20m"), 14.175, places=3)

    def test_it_says_so_when_it_does_not(self):
        with self.assertRaises(SyncError):
            mid_of("11m")


class TestTheApi(unittest.TestCase):
    def setUp(self):
        self.book = WrlLogbook("wrl_live_test")

    def test_no_key_is_refused_with_where_to_get_one(self):
        with self.assertRaises(SyncError) as got:
            WrlLogbook("").me()
        self.assertIn("Developer API", str(got.exception))

    def test_the_key_rides_as_a_bearer_token(self):
        with mock.patch("hamdash.services.qsl.requests.request",
                        return_value=a_reply({"uid": "VA7SLL"})) as req:
            self.book.me()
        self.assertEqual(req.call_args.kwargs["headers"]["Authorization"],
                         "Bearer wrl_live_test")

    def test_an_error_envelope_becomes_a_readable_refusal(self):
        reply = a_reply(error={"code": "INVALID_KEY", "message": "bad key"},
                        status=401)
        with mock.patch("hamdash.services.qsl.requests.request",
                        return_value=reply):
            with self.assertRaises(SyncError) as got:
                self.book.me()
        self.assertIn("bad key", str(got.exception))
        self.assertIn("check the API key", str(got.exception))

    def test_a_rate_limit_says_how_long_and_that_nothing_was_lost(self):
        r = mock.Mock(status_code=429, headers={"Retry-After": "31"})
        with mock.patch("hamdash.services.qsl.requests.request", return_value=r):
            with self.assertRaises(SyncError) as got:
                self.book.me()
        self.assertIn("31s", str(got.exception))
        self.assertIn("Nothing was lost", str(got.exception))

    def test_an_unreachable_server_says_so(self):
        with mock.patch("hamdash.services.qsl.requests.request",
                        side_effect=requests.ConnectionError("no route")):
            with self.assertRaises(SyncError) as got:
                self.book.me()
        self.assertIn("could not reach", str(got.exception))

    def test_a_reply_that_is_not_json_is_reported_not_swallowed(self):
        r = mock.Mock(status_code=502, headers={}, text="<html>gateway</html>")
        r.json.side_effect = ValueError("nope")
        with mock.patch("hamdash.services.qsl.requests.request", return_value=r):
            with self.assertRaises(SyncError) as got:
                self.book.me()
        self.assertIn("502", str(got.exception))

    def test_paging_walks_the_cursor_to_the_end(self):
        pages = [
            a_reply([{"call": "A", "timestamp": "2026-09-18T00:00:00Z"}],
                    {"nextCursor": "c1"}),
            a_reply([{"call": "B", "timestamp": "2026-09-17T00:00:00Z"}],
                    {"nextCursor": None}),
        ]
        with mock.patch("hamdash.services.qsl.requests.request",
                        side_effect=pages) as req:
            got = list(self.book.contacts())
        self.assertEqual([c["call"] for c in got], ["A", "B"])
        self.assertEqual(req.call_args_list[1].kwargs["params"]["cursor"], "c1")

    def test_paging_stops_on_an_empty_page_even_with_a_cursor(self):
        """A cursor that never goes null would otherwise page for ever."""
        with mock.patch("hamdash.services.qsl.requests.request",
                        return_value=a_reply([], {"nextCursor": "always"})):
            self.assertEqual(list(self.book.contacts()), [])

    def test_an_empty_logbook_says_so_rather_than_importing_nothing(self):
        with mock.patch("hamdash.services.qsl.requests.request",
                        return_value=a_reply([], {"nextCursor": None})):
            with self.assertRaises(SyncError) as got:
                self.book.pull()
        self.assertIn("no contacts", str(got.exception))

    def test_a_pull_comes_back_as_adif(self):
        page = a_reply([{"call": "VE7SL", "timestamp": "2026-09-18T12:34:00Z",
                         "freq": 14.074, "band": 20, "mode": "FT8"}],
                       {"nextCursor": None})
        with mock.patch("hamdash.services.qsl.requests.request", return_value=page):
            text = self.book.pull()
        self.assertIn("<EOH>", text)
        self.assertIn("<CALL:5>VE7SL", text)

    def test_a_push_posts_one_contact(self):
        row = {"call": "VE7SL", "qso_date": "20260918", "time_on": "1234",
               "freq": "14.074", "band": "20m", "mode": "FT8"}
        with mock.patch("hamdash.services.qsl.requests.request",
                        return_value=a_reply({"id": "abc", "isDuplicate": False})) as req:
            out = self.book.push_record(row, my_callsign="VA7SLL")
        self.assertEqual(req.call_args.args[0], "POST")
        self.assertEqual(req.call_args.kwargs["json"]["call"], "VE7SL")
        self.assertEqual(out["id"], "abc")
        self.assertFalse(out["duplicate"])

    def test_a_push_reports_what_they_call_a_duplicate(self):
        with mock.patch("hamdash.services.qsl.requests.request",
                        return_value=a_reply({"id": "x", "isDuplicate": True})):
            out = self.book.push_record(
                {"call": "VE7SL", "qso_date": "20260918", "time_on": "1234",
                 "freq": "14.074", "band": "20m", "mode": "FT8"})
        self.assertTrue(out["duplicate"])

    def test_a_logbook_id_is_sent_when_one_is_configured(self):
        book = WrlLogbook("k", "book-7")
        with mock.patch("hamdash.services.qsl.requests.request",
                        return_value=a_reply({"id": "x"})) as req:
            book.push_record({"call": "A", "qso_date": "20260918",
                              "time_on": "1200", "freq": "14.074",
                              "band": "20m", "mode": "FT8"})
        self.assertEqual(req.call_args.kwargs["json"]["logbookId"], "book-7")

    def test_delete_is_not_implemented(self):
        """It exists in their API. Nothing here should be able to remove
        somebody's contacts - the same rule as QRZ."""
        import inspect
        for name in dir(WrlLogbook):
            self.assertNotIn("delete", name.lower())
        # No DELETE request is ever built, whatever the docstring says about
        # the endpoint existing.
        src = inspect.getsource(WrlLogbook)
        self.assertNotIn('"DELETE"', src)
        self.assertNotIn("'DELETE'", src)


if __name__ == "__main__":
    unittest.main()
