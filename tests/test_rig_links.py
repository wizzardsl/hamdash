"""Two rig tiles, two radios, at the same time.

Every rig tile used to control the one selected radio: the host opened one
backend and ran one watchdog. A tile can now name its own radio, and that
radio is opened beside the selected one with its own backend, its own
polling thread and its own PTT watchdog. These pin what that must not
break: radios that do not interfere, one owner per serial port, a radio
nobody asks about being closed - never one on the air - and every card
that names no radio still meaning the selected one.

Run with:  python -m unittest discover -s tests
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hamdash.app import create_app
from hamdash.config import Config
from hamdash.services import rigs

A = {"id": "rig_a", "name": "Radio A", "backend": "mock"}
B = {"id": "rig_b", "name": "Radio B", "backend": "mock"}


def a_hub(*entries, active="rig_a", ptt_timeout=120):
    home = Path(tempfile.mkdtemp(prefix="hamdash-links-"))
    (home / "config.json").write_text(json.dumps({
        "radio": {"rigs": list(entries or (A, B)), "active": active,
                  "ptt_timeout": ptt_timeout}}), encoding="utf-8")
    (home / "layout.json").write_text("[]", encoding="utf-8")
    cfg = Config(home)
    rigs.ensure_rigs(cfg)
    return rigs.RigManager(cfg), home


class Recording(rigs.MockBackend):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = []
        self.fail_unkey = False

    def set_ptt(self, on):
        self.calls.append(bool(on))
        if not on and self.fail_unkey:
            raise rigs.RigError("the radio did not answer")
        super().set_ptt(on)


class Base(unittest.TestCase):
    def hub(self, *entries, **kw):
        man, home = a_hub(*entries, **kw)
        self.addCleanup(shutil.rmtree, home, True)
        self.addCleanup(man.stop)
        return man


class TestRadiosAreIndependent(Base):
    def test_two_radios_tune_separately(self):
        man = self.hub()
        man.link("rig_a").set_freq(7074000)
        man.link("rig_b").set_freq(14074000)
        self.assertEqual(man.link("rig_a").state()["freq"], 7074000)
        self.assertEqual(man.link("rig_b").state()["freq"], 14074000)
        self.assertEqual(man.state()["freq"], 7074000,
                         "no id must still mean the selected radio")

    def test_each_says_which_radio_it_is(self):
        man = self.hub()
        self.assertEqual(man.link("rig_b").state()["rig_id"], "rig_b")
        self.assertEqual(man.link("rig_b").state()["rig"], "Radio B")
        self.assertEqual(man.link("selected").state()["rig_id"], "rig_a")

    def test_an_unknown_radio_is_a_clear_error_not_the_selected_one(self):
        """A tile pointed at a radio that was deleted must not quietly
        start driving a different one."""
        man = self.hub()
        with self.assertRaises(rigs.RigError) as got:
            man.link("gone")
        self.assertIn("Settings -> Radio", str(got.exception))

    def test_the_watchdog_on_one_radio_leaves_the_other_alone(self):
        man = self.hub()
        a, b = man.link("rig_a"), man.link("rig_b")
        a._backend, b._backend = Recording(), Recording()
        now = [1000.0]
        with mock.patch.object(rigs.time, "time", lambda: now[0]):
            a.set_ptt(True)
            b.set_ptt(True)
            for _ in range(10):         # B is held, A is not
                now[0] += 1.0
                b.set_ptt(True)
                a._watch_ptt(a.state())
                b._watch_ptt(b.state())
        self.assertEqual(a._backend.calls, [True, False], "A was left on air")
        self.assertEqual(b._backend.calls, [True], "B was unkeyed for A")
        self.assertTrue(b.keyed())

    def test_ptt_on_one_radio_is_not_reported_on_another(self):
        man = self.hub()
        man.link("rig_a")._backend = Recording()
        man.link("rig_a").set_ptt(True)
        self.assertNotIn("ptt_ours", man.link("rig_b").state())

    def test_one_radio_raising_does_not_stop_the_other_polling(self):
        man = self.hub()
        a, b = man.link("rig_a"), man.link("rig_b")
        seen = []
        with mock.patch.object(a, "state", side_effect=RuntimeError("COM")), \
                mock.patch.object(b, "publish", side_effect=seen.append):
            man.start_polling(hz=50.0)
            deadline = rigs.time.time() + 2.0
            while not seen and rigs.time.time() < deadline:
                rigs.time.sleep(0.01)
        self.assertTrue(seen, "B stopped because A's backend raised")


class TestOnePortOneOwner(Base):
    """Two programs cannot share a serial port and neither can two of ours.
    Only one radio open at a time used to enforce that by itself."""

    HL1 = {"id": "hl1", "name": "FT-900 Hamlib", "backend": "hamlib",
           "model": 1013, "port": "COM13"}
    HL2 = {"id": "hl2", "name": "FT-900 again", "backend": "hamlib",
           "model": 1013, "port": "com13"}

    def setUp(self):
        made = []

        def fake(rig):
            made.append(rig["id"])
            return rigs.MockBackend()
        patch = mock.patch.object(rigs, "make_backend", side_effect=fake)
        patch.start()
        self.addCleanup(patch.stop)
        self.made = made

    def test_a_second_radio_on_the_same_port_is_refused_by_name(self):
        man = self.hub(self.HL1, self.HL2, active="hl1")
        st = man.link("hl2").state()
        self.assertFalse(st["ok"])
        self.assertIn("FT-900 Hamlib", st["error"])
        self.assertIn("COM13", st["error"])
        self.assertNotIn("hl2", self.made, "it opened the port anyway")

    def test_it_gets_the_port_once_the_other_lets_go(self):
        man = self.hub(self.HL1, self.HL2, A, active="rig_a")
        man.link("hl1")
        self.assertFalse(man.link("hl2").state()["ok"])
        man.reap(now=rigs.time.time() + rigs.LINK_IDLE + 1)
        self.assertIn("hl1", self.made)
        man.link("hl2")
        self.assertIn("hl2", self.made)

    def test_testing_a_radio_that_is_open_asks_the_open_one(self):
        """A second rigctld on a COM port somebody already has fails, and
        says so misleadingly."""
        man = self.hub(self.HL1, active="hl1")
        self.made.clear()
        got = man.test(dict(self.HL1))
        self.assertTrue(got["ok"])
        self.assertEqual(self.made, [], "it spawned a second backend")

    def test_testing_a_clashing_radio_refuses_rather_than_opening(self):
        man = self.hub(self.HL1, active="hl1")
        self.made.clear()
        got = man.test(dict(self.HL2))
        self.assertFalse(got["ok"])
        self.assertIn("FT-900 Hamlib", got["error"])
        self.assertEqual(self.made, [])

    def test_what_counts_as_the_same_place(self):
        self.assertEqual(rigs.resource(self.HL1), rigs.resource(self.HL2))
        self.assertIsNone(rigs.resource(A), "a simulator holds nothing")
        self.assertNotEqual(
            rigs.resource({"backend": "omnirig", "slot": 1}),
            rigs.resource({"backend": "omnirig", "slot": 2}))


class TestClosingRadios(Base):
    def test_a_radio_nobody_asks_about_is_closed(self):
        man = self.hub()
        man.link("rig_b")
        man.reap(now=rigs.time.time() + rigs.LINK_IDLE + 1)
        self.assertNotIn("rig_b", man.open_ids())

    def test_a_radio_still_asked_about_is_not(self):
        man = self.hub()
        man.link("rig_b")
        man.reap(now=rigs.time.time() + rigs.LINK_IDLE / 2)
        self.assertIn("rig_b", man.open_ids())

    def test_the_selected_radio_is_never_reaped(self):
        man = self.hub()
        man.reap(now=rigs.time.time() + 10 * rigs.LINK_IDLE)
        self.assertIn("rig_a", man.open_ids())

    def test_a_radio_on_the_air_is_never_reaped(self):
        """Closing it would leave nothing watching the transmission."""
        man = self.hub()
        b = man.link("rig_b")
        b._backend = Recording()
        b.set_ptt(True)
        man.reap(now=rigs.time.time() + 10 * rigs.LINK_IDLE)
        self.assertIn("rig_b", man.open_ids())
        self.assertEqual(b._backend.calls, [True])

    def test_selecting_another_radio_keeps_one_a_tile_is_driving(self):
        man = self.hub()
        man.link("rig_a")           # a tile names the selected radio
        man.select("rig_b")
        self.assertIn("rig_a", man.open_ids())

    def test_selecting_another_radio_closes_one_nobody_is_driving(self):
        """As it always did: the radio before it lets go of its port."""
        man = self.hub()
        man.select("rig_b")
        self.assertNotIn("rig_a", man.open_ids())

    def test_saving_the_list_leaves_unchanged_radios_alone(self):
        man = self.hub()
        b = man.link("rig_b")
        man.reconcile()
        self.assertIs(man.link("rig_b"), b, "an unchanged radio was reopened")

    def test_saving_the_list_reopens_an_edited_radio_and_drops_a_removed_one(self):
        man = self.hub(A, B, {"id": "rig_c", "name": "C", "backend": "mock"})
        b = man.link("rig_b")
        man.link("rig_c")
        rl = man.cfg.data["radio"]["rigs"]
        rl[:] = [r for r in rl if r["id"] != "rig_c"]
        next(r for r in rl if r["id"] == "rig_b")["name"] = "Radio B, renamed"
        man.reconcile()
        self.assertNotIn("rig_c", man.open_ids())
        self.assertIsNot(man.link("rig_b"), b)

    def test_shutdown_unkeys_every_radio_and_carries_on_past_a_failure(self):
        man = self.hub(A, B, {"id": "rig_c", "name": "C", "backend": "mock"})
        links = [man.link(i) for i in ("rig_a", "rig_b", "rig_c")]
        for link in links:
            link._backend = Recording()
            link.set_ptt(True)
        links[0]._backend.fail_unkey = True
        backends = [link._backend for link in links]
        man.stop()
        for be in backends:
            self.assertEqual(be.calls, [True, False])


class TestTheBus(Base):
    def test_the_selected_radio_says_both_and_the_others_only_radio_rig(self):
        man = self.hub()
        sent = []
        with mock.patch.object(rigs.BUS, "publish",
                               side_effect=lambda t, p, **k: sent.append(
                                   (t, p["rig_id"]))):
            man.link("rig_a").publish(man.link("rig_a").state())
            man.link("rig_b").publish(man.link("rig_b").state())
        self.assertIn(("radio.state", "rig_a"), sent)
        self.assertIn(("radio.rig", "rig_a"), sent)
        self.assertIn(("radio.rig", "rig_b"), sent)
        self.assertNotIn(("radio.state", "rig_b"), sent,
                         "every card following radio.state would jump "
                         "to a radio that is not the selected one")


class TestTheRoutes(unittest.TestCase):
    """`?rig=` or `"rig"` names a radio; neither means the selected one."""

    def setUp(self):
        home = Path(tempfile.mkdtemp(prefix="hamdash-routes-"))
        self.addCleanup(shutil.rmtree, home, True)
        (home / "config.json").write_text(json.dumps({
            "radio": {"rigs": [A, B], "active": "rig_a"}}), encoding="utf-8")
        (home / "layout.json").write_text("[]", encoding="utf-8")
        app, _ = create_app(Config(home), port=8765)
        self.c = app.test_client()

    def test_a_body_rig_tunes_that_radio_only(self):
        self.c.post("/api/radio/freq", json={"hz": 7074000})
        self.c.post("/api/radio/freq", json={"hz": 14074000, "rig": "rig_b"})
        self.assertEqual(self.c.get("/api/radio/state").get_json()["freq"],
                         7074000)
        got = self.c.get("/api/radio/state?rig=rig_b").get_json()
        self.assertEqual((got["rig_id"], got["freq"]), ("rig_b", 14074000))

    def test_selected_is_the_selected_radio(self):
        got = self.c.get("/api/radio/state?rig=selected").get_json()
        self.assertEqual(got["rig_id"], "rig_a")

    def test_an_unknown_radio_says_so(self):
        got = self.c.get("/api/radio/state?rig=gone").get_json()
        self.assertFalse(got["ok"])
        self.assertIn("gone", got["error"])
        r = self.c.post("/api/radio/freq", json={"hz": 7074000, "rig": "gone"})
        self.assertEqual(r.status_code, 502)

    def test_meters_and_vfos_reach_the_named_radio(self):
        self.assertTrue(self.c.get("/api/radio/meters?rig=rig_b")
                        .get_json()["ok"])
        r = self.c.post("/api/radio/vfo", json={"which": "B", "rig": "rig_b"})
        self.assertEqual(r.get_json()["rig_id"], "rig_b")

    def test_the_rigs_list_says_which_are_open(self):
        self.c.get("/api/radio/state?rig=rig_b")
        self.assertEqual(sorted(self.c.get("/api/rigs").get_json()["open"]),
                         ["rig_a", "rig_b"])


if __name__ == "__main__":
    unittest.main()
