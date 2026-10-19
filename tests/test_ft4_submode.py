"""FT4 is logged as FT4, though ADIF spells it MFSK + FT4.

ADIF never gave FT4 a mode of its own: it is MODE=MFSK, SUBMODE=FT4, and
that is exactly what WSJT-X sends. The logbook had no SUBMODE field, so
the FT4 was thrown away on import and the contact logged as MFSK -
reported as "you are logging FT4 from WSJT-X as MFSK" - and a contact
pushed in that state reached QRZ and World Radio League as MFSK too.

The submode is kept now, written back out as ADIF (MFSK + FT4 is what
LoTW expects), shown as the mode, sent to WRL as FT4, and matched so that
the copies already out there as bare MFSK are recognised rather than
pulled back in as new contacts.

Run with:  python -m unittest discover -s tests
"""
import tempfile
import unittest
from pathlib import Path

from hamdash.services.logbook import Logbook, same_mode, shown_mode
from hamdash.services.qsl import adif_record, as_contact, as_qso

# As WSJT-X 3.0.2 logs an FT4 contact, trimmed to the fields that matter.
WSJTX_FT4 = ("<EOH>\n<call:3>W7B <gridsquare:4>DN06 <mode:4>MFSK "
             "<submode:3>FT4 <rst_sent:3>-05 <rst_rcvd:3>-11 "
             "<qso_date:8>20261019 <time_on:6>233932 <band:3>20m "
             "<freq:9>14.081769 <station_callsign:6>VA7SLL <eor>\n")


def a_log() -> Logbook:
    return Logbook(Path(tempfile.mkdtemp(prefix="hamdash-ft4-")) / "l.db")


class TestImportKeepsIt(unittest.TestCase):
    def test_the_submode_survives_the_import(self):
        log = a_log()
        log.import_adif(WSJTX_FT4)
        row = log.recent(1)[0]
        self.assertEqual((row["mode"], row["submode"]), ("MFSK", "FT4"))

    def test_it_goes_back_out_as_adif(self):
        """MFSK + FT4 is the form LoTW and QRZ expect."""
        log = a_log()
        log.import_adif(WSJTX_FT4)
        rec = log.export_adif_record(log.recent(1)[0])
        self.assertIn("<MODE:4>MFSK", rec)
        self.assertIn("<SUBMODE:3>FT4", rec)
        self.assertIn("<SUBMODE:3>FT4", log.export_adif())

    def test_a_log_from_before_the_field_is_upgraded_in_place(self):
        """An existing logbook has no submode column; it is added, not
        rebuilt, like every column before it."""
        import sqlite3
        path = Path(tempfile.mkdtemp(prefix="hamdash-ft4-old-")) / "l.db"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE qso (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                   "call TEXT, qso_date TEXT, mode TEXT, created REAL)")
        db.execute("INSERT INTO qso (call, qso_date, mode) "
                   "VALUES ('W7B', '20261019', 'MFSK')")
        db.commit()
        db.close()
        log = Logbook(path)
        self.assertEqual(log.recent(1)[0]["submode"], None)
        log.import_adif(WSJTX_FT4.replace("W7B", "K1ABC"))
        self.assertEqual(log.recent(1)[0]["submode"], "FT4")


class TestShownAsFt4(unittest.TestCase):
    def test_the_submode_is_the_mode_an_operator_reads(self):
        self.assertEqual(shown_mode({"mode": "MFSK", "submode": "FT4"}), "FT4")
        self.assertEqual(shown_mode({"mode": "FT8"}), "FT8")
        self.assertEqual(shown_mode({"mode": "ssb", "submode": ""}), "SSB")

    def test_world_radio_league_gets_ft4(self):
        row = {"call": "W7B", "qso_date": "20261019", "time_on": "233932",
               "band": "20m", "freq": "14.081769", "mode": "MFSK",
               "submode": "FT4"}
        self.assertEqual(as_contact(row, my_callsign="VA7SLL")["mode"], "FT4")

    def test_the_mode_count_says_ft4(self):
        log = a_log()
        log.import_adif(WSJTX_FT4)
        self.assertEqual(log.stats()["by_mode"], {"FT4": 1})

    def test_the_logbook_card_shows_it(self):
        page = (Path(__file__).resolve().parent.parent / "plugins" / "logbook"
                / "index.html").read_text(encoding="utf-8")
        self.assertIn("r.submode || r.mode", page)


class TestTheSameContact(unittest.TestCase):
    MFSK_FT4 = {"mode": "MFSK", "submode": "FT4"}

    def test_ft4_however_it_is_spelt(self):
        self.assertTrue(same_mode({"mode": "FT4"}, self.MFSK_FT4))
        self.assertTrue(same_mode(self.MFSK_FT4, {"mode": "FT4"}))
        self.assertTrue(same_mode(self.MFSK_FT4, dict(self.MFSK_FT4)))

    def test_a_copy_pushed_as_bare_mfsk_is_still_that_contact(self):
        self.assertTrue(same_mode({"mode": "MFSK"}, self.MFSK_FT4))
        self.assertTrue(same_mode(self.MFSK_FT4, {"mode": "MFSK"}))

    def test_different_modes_are_different(self):
        self.assertFalse(same_mode({"mode": "FT8"}, self.MFSK_FT4))
        self.assertFalse(same_mode({"mode": "MFSK", "submode": "JS8"},
                                   self.MFSK_FT4))
        self.assertFalse(same_mode({"mode": "SSB"}, {"mode": "CW"}))

    def test_blanks_behave_as_they_always_have(self):
        self.assertTrue(same_mode({"mode": ""}, {"mode": "CW"}))
        self.assertFalse(same_mode({"mode": "CW"}, {"mode": ""}))


class TestNoDuplicateOnThePull(unittest.TestCase):
    """The point of matching on the shown mode: a pull must find the FT4
    contact, however the far end spells it, rather than add it again."""

    def test_wrl_sending_ft4_back(self):
        log = a_log()
        log.import_adif(WSJTX_FT4)
        theirs = as_qso({"call": "W7B", "timestamp": "2026-10-19T23:39:32Z",
                         "freq": 14.081769, "band": 20, "mode": "FT4"})
        res = log.import_adif("<EOH>\n" + adif_record(theirs) + "\n")
        self.assertEqual(res["added"], 0)
        self.assertEqual(log.stats()["total"], 1)

    def test_qrz_sending_back_the_bare_mfsk_it_was_given(self):
        log = a_log()
        log.import_adif(WSJTX_FT4)
        res = log.import_adif("<EOH>\n<CALL:3>W7B <QSO_DATE:8>20261019 "
                              "<TIME_ON:6>233932 <BAND:3>20m <MODE:4>MFSK <EOR>\n")
        self.assertEqual(res["added"], 0)
        self.assertEqual(log.stats()["total"], 1)

    def test_an_ft8_contact_at_the_same_time_is_still_separate(self):
        log = a_log()
        log.import_adif(WSJTX_FT4)
        res = log.import_adif("<EOH>\n<CALL:3>W7B <QSO_DATE:8>20261019 "
                              "<TIME_ON:4>2340 <BAND:3>20m <MODE:3>FT8 <EOR>\n")
        self.assertEqual(res["added"], 1)


if __name__ == "__main__":
    unittest.main()
