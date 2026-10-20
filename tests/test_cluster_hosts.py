"""A spots tile can follow several DX cluster nodes.

The telnet cluster was one host per tile. The `DX de` line is the one
spot format that is genuinely shared - DXSpider, AR-Cluster, CC-Cluster,
RBN and HamAlert all speak it - so letting the operator list several
nodes is how a source is added without anybody writing an adapter, where
a generic "paste a JSON URL" box would have to guess units and time
zones and get them silently wrong.

The cluster is also a network: a spot entered on one node reaches the
others, so two ordinary nodes deliver mostly the same spots, and those
are merged.

Run with:  python -m unittest discover -s tests
"""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent / "plugins" / "spots"
sys.path.insert(0, str(HERE))

import telnet                                           # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


spots_card = _load("spots_card_hosts", HERE / "card.py")


class TestTheHostList(unittest.TestCase):
    def test_a_single_host_still_reads_as_itself(self):
        """Every tile set up before this has one host in the box."""
        self.assertEqual(telnet.cluster_hosts("dxc.ve7cc.net", 23),
                         [("dxc.ve7cc.net", 23)])

    def test_several_with_and_without_ports(self):
        self.assertEqual(
            telnet.cluster_hosts("dxc.ve7cc.net, dxc.nc7j.com:7373\nve7cc.net;x", 7300),
            [("dxc.ve7cc.net", 7300), ("dxc.nc7j.com", 7373),
             ("ve7cc.net", 7300), ("x", 7300)])

    def test_a_repeat_is_one_node(self):
        self.assertEqual(telnet.cluster_hosts("A:7300, a:7300, a", 23),
                         [("a", 7300), ("a", 23)])

    def test_a_bad_port_skips_that_entry_only(self):
        self.assertEqual(telnet.cluster_hosts("x:abc, y:99999, y:0, z", 23),
                         [("z", 23)])

    def test_no_more_than_five(self):
        """Each is a login on somebody else's server."""
        got = telnet.cluster_hosts(",".join(f"h{i}" for i in range(9)), 23)
        self.assertEqual(len(got), telnet.MAX_CLUSTERS)
        self.assertEqual(got[0], ("h0", 23))

    def test_nothing_is_nothing(self):
        for raw in ("", None, " , ;\n"):
            self.assertEqual(telnet.cluster_hosts(raw, 23), [], repr(raw))

    def test_a_junk_default_port_is_23(self):
        self.assertEqual(telnet.cluster_hosts("x", "abc"), [("x", 23)])


class FakeStream:
    made = []

    def __init__(self, kind, host, port, call, cmds):
        self.kind, self.host, self.port, self.cmds = kind, host, port, cmds
        FakeStream.made.append((kind, host, port))

    def stop(self):
        pass


class TestTheCardOpensOneFeedPerNode(unittest.TestCase):
    def setUp(self):
        FakeStream.made = []
        spots_card._streams.clear()
        spots_card._stream_wanted.clear()
        self.addCleanup(spots_card._streams.clear)
        self.addCleanup(spots_card._stream_wanted.clear)
        p1 = mock.patch.object(telnet, "SpotStream", FakeStream)
        p2 = mock.patch.object(spots_card, "my_call", return_value="VA7SLL")
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def conf(self, hosts, port=23, cmds=""):
        return {"show_cluster": True, "cluster_host": hosts,
                "cluster_port": port, "cluster_commands": cmds}

    def test_one_feed_for_each_node(self):
        got = spots_card.stream_for(self.conf("dxc.ve7cc.net, dxc.nc7j.com:7373"))
        self.assertEqual([(s.host, s.port) for s in got],
                         [("dxc.ve7cc.net", 23), ("dxc.nc7j.com", 7373)])

    def test_every_node_gets_the_commands(self):
        got = spots_card.stream_for(self.conf("a, b", cmds="set/skimmer; set/ft8"))
        self.assertEqual([s.cmds for s in got],
                         [["set/skimmer", "set/ft8"]] * 2)

    def test_an_existing_single_host_tile_is_unchanged(self):
        got = spots_card.stream_for(self.conf("dxc.ve7cc.net", port=7300))
        self.assertEqual([(s.kind, s.host, s.port) for s in got],
                         [("cluster", "dxc.ve7cc.net", 7300)])

    def test_asking_again_reuses_the_connections(self):
        spots_card.stream_for(self.conf("a, b"))
        spots_card.stream_for(self.conf("a, b"))
        self.assertEqual(len(FakeStream.made), 2)

    def test_off_means_none(self):
        conf = dict(self.conf("a, b"), show_cluster=False)
        self.assertEqual(spots_card.stream_for(conf), [])


def spot(**kw):
    row = {"kind": "cluster", "call": "EA3CU", "hz": 7_185_000,
           "spotter": "F6JON", "at": 1_792_500_000.0}
    row.update(kw)
    return row


class TestMergingTheSameSpot(unittest.TestCase):
    def test_the_same_spot_from_two_nodes_is_one(self):
        self.assertEqual(len(telnet.merge_cluster([spot(), spot()])), 1)

    def test_the_first_is_kept(self):
        got = telnet.merge_cluster([spot(comment="a"), spot(comment="b")])
        self.assertEqual(got[0]["comment"], "a")

    def test_a_different_spotter_is_a_different_spot(self):
        self.assertEqual(len(telnet.merge_cluster(
            [spot(), spot(spotter="K1ABC")])), 2)

    def test_a_different_frequency_is_a_different_spot(self):
        self.assertEqual(len(telnet.merge_cluster(
            [spot(), spot(hz=7_186_000)])), 2)

    def test_a_later_minute_is_a_new_spot(self):
        self.assertEqual(len(telnet.merge_cluster(
            [spot(), spot(at=1_792_500_120.0)])), 2)

    def test_other_sources_are_never_merged(self):
        """RBN sends one line per skimmer on purpose - each is a report."""
        rows = [spot(kind="rbn"), spot(kind="rbn"), spot(kind="dx"), spot(kind="dx")]
        self.assertEqual(len(telnet.merge_cluster(rows)), 4)


class TestTheStatusNamesTheNode(unittest.TestCase):
    def test_label(self):
        st = telnet.SpotStream.__new__(telnet.SpotStream)
        st.kind, st.host, st.status, st.error = "cluster", "dxc.ve7cc.net", "running", ""
        st.spots, st.last_line, st.connected_at = [], 0, 0
        self.assertEqual(st.status_line()["label"], "cluster dxc.ve7cc.net")
        st.kind = "rbn"
        self.assertEqual(st.status_line()["label"], "rbn")

    def test_the_page_shows_it(self):
        page = (HERE / "index.html").read_text(encoding="utf-8")
        self.assertIn("s.label || s.name", page)


if __name__ == "__main__":
    unittest.main()
