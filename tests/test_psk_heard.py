"""Stations decoded here go on the PSK Reporter map, once.

The decoder publishes every FT8/FT4 slot on `decoder.decoded`; each decode
is a reception report with the operator as receiver, needing no internet.
The PSK Reporter card stores them beside the live feed's. When PSK
Reporter spotting is on, the feed sends our own uploads back: the uploader
sends the slot start and the radio frequency to the hertz, so the echo
usually lands on the same key, but either way **ours is kept** - it carries
the SNR, which the upload does not - in whichever order the two arrive.

Nothing here touches the network.

Run with:  python -m unittest discover -s tests
"""
import importlib.util
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "psk_reporter"
sys.path.insert(0, str(PLUGIN))

import store as pskstore                                      # noqa: E402


def _card():
    spec = importlib.util.spec_from_file_location(
        "psk_card_for_heard_tests", PLUGIN / "card.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ME = {"callsign": "VA7SLL", "grid": "DO00ha"}
SLOT = 1793700000


def ours(**kw):
    r = {"tx": "JA1XYZ", "rx": "VA7SLL", "tx_grid": "PM95", "rx_grid": "DO00ha",
         "hz": 14075210, "mode": "FT8", "snr": -14, "at": SLOT,
         "is_tx": False, "is_rx": True}
    r.update(kw)
    return r


def echo(**kw):
    """The feed's copy of the same report: no SNR, as our upload sent none."""
    r = ours(snr=None)
    r.update(kw)
    return r


class TestOneReportNotTwo(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="hamdash-pskheard-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.store = pskstore.Store(self.home / "r.sqlite3")

    def rows(self, target="VA7SLL"):
        return self.store.since(target, 10 ** 9)

    def test_ours_first_then_the_exact_echo(self):
        self.store.add_local("VA7SLL", [ours()])
        self.store.add("VA7SLL", [echo()])
        (r,) = self.rows()
        self.assertTrue(r["local"])
        self.assertEqual(r["snr"], -14, "our SNR survives the echo")

    def test_an_echo_a_little_off_in_time_and_frequency(self):
        self.store.add_local("VA7SLL", [ours()])
        self.store.add("VA7SLL", [echo(at=SLOT + 13, hz=14075210 + 4)])
        self.assertEqual(len(self.rows()), 1)

    def test_the_echo_first_then_ours(self):
        self.store.add("VA7SLL", [echo(at=SLOT + 2)])
        self.store.add_local("VA7SLL", [ours()])
        (r,) = self.rows()
        self.assertTrue(r["local"])
        self.assertEqual(r["snr"], -14)

    def test_the_same_decode_twice_is_one(self):
        self.store.add_local("VA7SLL", [ours()])
        self.store.add_local("VA7SLL", [ours()])
        self.assertEqual(len(self.rows()), 1)

    def test_what_is_not_the_same_report_is_kept(self):
        self.store.add_local("VA7SLL", [ours()])
        # FT4 at a different second: the store's key is stations, time and
        # frequency, so the same second and hertz would be the same report -
        # which on the air it would be, FT8 and FT4 slots never sharing a
        # start. Inside SAME_SECONDS, so the mode is what keeps them apart.
        for other in (echo(tx="JA2ABC"), echo(mode="FT4", at=SLOT + 7),
                      echo(at=SLOT + 120), echo(hz=14076210)):
            self.store.add("VA7SLL", [other])
        self.assertEqual(len(self.rows()), 5)

    def test_somebody_else_hearing_the_same_station_is_not_ours(self):
        self.store.add_local("VA7SLL", [ours()])
        self.store.add("VA7SLL", [echo(rx="VE7XYZ")])
        self.assertEqual(len(self.rows()), 2)

    def test_a_store_from_before_this_is_upgraded(self):
        old = self.home / "old.sqlite3"
        with sqlite3.connect(old) as db:
            cols = [c for c in pskstore.COLUMNS if c != "source"]
            db.execute(f"CREATE TABLE report ({', '.join(f'{c} TEXT' for c in cols)}, "
                       "PRIMARY KEY (target, tx, rx, at, hz))")
            db.execute(f"INSERT INTO report ({', '.join(cols)}) VALUES "
                       f"({', '.join('?' * len(cols))})",
                       ["VA7SLL", "JA1XYZ", "VA7SLL", "PM95", "DO00ha",
                        "14075210", "FT8", "", str(SLOT), "0", "1", "", "", "", ""])
        db.close()
        s = pskstore.Store(old)
        s.add_local("VA7SLL", [ours()])
        (r,) = s.since("VA7SLL", 10 ** 9)
        self.assertTrue(r["local"])


class TestHeard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.card = _card()

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="hamdash-pskheard-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.store = pskstore.Store(self.home / "r.sqlite3")
        self.heard = self.card.Heard(self.store, station=lambda: ME)

    def slot(self, decodes, **kw):
        p = {"mode": "FT8", "slot_ts": SLOT, "dial_hz": 14074000,
             "simulated": False, "source": "alsa_input.usb-x", "decodes": decodes}
        p.update(kw)
        return p

    def decode(self, **kw):
        d = {"call": "JA1XYZ", "grid": "PM95", "snr": -14,
             "rf_hz": 14075210, "osd": None}
        d.update(kw)
        return d

    def test_stored_under_the_call_and_the_square(self):
        self.heard.offer(self.slot([self.decode()]))
        (by_call,) = self.store.since("VA7SLL", 10 ** 9)
        (by_grid,) = self.store.since("DO00", 10 ** 9)
        self.assertTrue(by_call["is_rx"] and by_call["local"])
        self.assertFalse(by_grid["is_rx"], "a square is not a station")
        self.assertEqual((by_call["tx"], by_call["snr"], by_call["mode"]),
                         ("JA1XYZ", -14, "FT8"))

    def test_a_grid_sent_earlier_places_a_later_report(self):
        self.heard.offer(self.slot([self.decode()]))
        self.heard.offer(self.slot([self.decode(grid="", snr=-10)],
                                   slot_ts=SLOT + 30))
        grids = {r["tx_grid"] for r in self.store.since("VA7SLL", 10 ** 9)}
        self.assertEqual(grids, {"PM95"})

    def test_what_is_left_out(self):
        for d in (self.decode(call="VA7SLL"), self.decode(call="<...>"),
                  self.decode(osd=0.8), self.decode(rf_hz=None),
                  self.decode(call="")):
            self.assertEqual(self.heard.offer(self.slot([d])), 0, d)
        self.assertEqual(self.heard.offer(self.slot([self.decode()],
                                                    simulated=True)), 0)
        self.assertEqual(self.heard.offer(self.slot([self.decode()],
                                                    source="mock:ft8")), 0)

    def test_wspr_is_heard_here_too(self):
        """A WSPR beacon decoded here is a reception report like an FT8
        station, and the map's mode filter tells them apart. A type 3
        message whose call is only an unheard hash names nobody."""
        n = self.heard.offer(self.slot(
            [self.decode(call="K1ABC", grid="FN42", snr=-24,
                         rf_hz=10140185),
             self.decode(call="", grid="FK52UD", rf_hz=10140100)],
            mode="WSPR", dial_hz=10138700))
        self.assertEqual(n, 2)                 # one report, two targets
        (row,) = self.store.since("VA7SLL", 10 ** 9)
        self.assertEqual((row["tx"], row["mode"], row["snr"], row["hz"]),
                         ("K1ABC", "WSPR", -24, 10140185))

    def test_nothing_without_a_callsign(self):
        h = self.card.Heard(self.store, station=lambda: {"callsign": ""})
        self.assertEqual(h.offer(self.slot([self.decode()])), 0)

    def test_the_card_listens_for_them(self):
        import json
        manifest = json.loads((PLUGIN / "card.json").read_text(encoding="utf-8"))
        self.assertIn("decoder.decoded", manifest["subscribes"])



class TestEveryRowHasACountry(unittest.TestCase):
    """The Country filter used the feed's DXCC numbers, which our own decodes
    do not have - so choosing a country hid every station heard here, and
    the dropdown read as numbers. Both ends get a name from the callsign,
    the same table the flags come from, for feed rows and ours alike."""

    def test_names_from_callsigns(self):
        card = _card()
        rows = card.with_countries([
            {"tx": "KC0REW", "rx": "VA7SLL"},
            {"tx": "PY1SX", "rx": "VE7PPE"},
            {"tx": "<...>", "rx": ""},
        ])
        self.assertEqual((rows[0]["tx_country"], rows[0]["rx_country"]),
                         ("United States", "Canada"))
        self.assertEqual(rows[1]["tx_country"], "Brazil")
        self.assertEqual((rows[2]["tx_country"], rows[2]["rx_country"]), ("", ""))

    def test_the_page_filters_on_the_name(self):
        page = (PLUGIN / "index.html").read_text(encoding="utf-8")
        self.assertIn("tx_country", page)
        self.assertIn("rx_country", page)
        i = page.index("function visible(")
        self.assertNotIn("_dxcc", page[i:page.index("\n}\n", i)],
                         "a local decode has no DXCC number to match")

if __name__ == "__main__":
    unittest.main()
