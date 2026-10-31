# Copyright (C) 2026 Alok K. Dhir
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
# OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import os
import sqlite3
import tempfile
import unittest

import helpers  # noqa: F401
from lib import names, pfstate, store


def state(key="1", local="10.9.1.20", lport=51000, remote="203.0.113.10", rport=443):
    return pfstate.State((key, "c"), "bridge0", "tcp", local, lport, remote, rport, True, 0, 0, 0, 0, 0)


class TestRollup(unittest.TestCase):
    def test_minutes_roll_up(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "loupe.db")
            s = store.Store(path)
            self.addCleanup(s.close)
            key = ("10.9.1.20", "203.0.113.10", 443, "tcp", "www.example")
            s.write({(7260, *key): ["", "sni", 0, 1, 100, 1, 1], (7320, *key): ["", "sni", 0, 2, 200, 1, 0]}, {}, {})
            db = sqlite3.connect(path)
            self.addCleanup(db.close)
            self.assertEqual(db.execute("SELECT bucket, down FROM flows_1m ORDER BY bucket").fetchall(), [(7260, 100), (7320, 200)])
            self.assertEqual(db.execute("SELECT bucket, down, conns FROM flows_5m").fetchall(), [(7200, 300, 1)])
            self.assertEqual(db.execute("SELECT bucket, down FROM flows_1h").fetchall(), [(7200, 300)])


class TestTransactions(unittest.TestCase):
    def test_failed_update_rolls_back(self):
        with tempfile.TemporaryDirectory() as d:
            s = store.Store(os.path.join(d, "loupe.db"))
            self.addCleanup(s.close)
            with self.assertRaises(sqlite3.Error):
                s.set_device_types([("n", "v", "t", "s", "m")])          # wrong number of values
            s.write({}, {}, {})                                            # would fail inside a stuck transaction
            self.assertFalse(s.db.in_transaction)


class TestNames(unittest.TestCase):
    def test_priority(self):
        n = names.Names()
        n.dns_answer({"ts": 100, "client": "10.9.1.20", "name": "cdn.example", "addrs": ["203.0.113.10"], "ttl": 60})
        n.dns_answer({"ts": 100, "client": "10.9.1.21", "name": "other.example", "addrs": ["203.0.113.11"], "ttl": 60})
        n.tls({"ts": 100, "client": "10.9.1.20", "cport": 51000, "server": "203.0.113.10", "port": 443,
               "sni": "www.example"})
        self.assertEqual(n.name_for(state(), 101), ("www.example", "sni"))
        self.assertEqual(n.name_for(state("2", lport=51001), 101), ("cdn.example", "dns"))
        self.assertEqual(n.name_for(state("3", remote="203.0.113.11"), 101), ("other.example", "dns*"))
        self.assertEqual(n.name_for(state("4", remote="203.0.113.12"), 101), (None, None))

    def test_sticky_and_expiry(self):
        n = names.Names()
        n.tls({"ts": 100, "client": "10.9.1.20", "cport": 51000, "server": "203.0.113.10", "port": 443, "sni": "a.example"})
        st = state()
        n.name_for(st, 101)
        n.dns_answer({"ts": 102, "client": "10.9.1.20", "name": "b.example", "addrs": ["203.0.113.10"], "ttl": 60})
        self.assertEqual(n.name_for(st, 103), ("a.example", "sni"))      # never renamed
        n.expire(set(), 104)
        self.assertEqual(n.name_for(st, 105), ("b.example", "dns"))      # state gone -> fresh lookup


class TestLateHello(unittest.TestCase):
    """capture can hand over a connection's hello after pf shows its state: the first bytes wait for it"""

    def setUp(self):
        import loupd
        self.lp = object.__new__(loupd.Loupe)
        self.lp.names, self.lp.flows, self.lp.held, self.lp.ip_mac, self.lp.stopping = names.Names(), {}, [], {}, False
        self.lp.names.dns_answer({"ts": 100, "client": "10.9.1.21", "name": "guess.example", "addrs": ["203.0.113.10"],
                                  "ttl": 60})

    def rows(self):
        return {k[5]: v[3:5] for k, v in self.lp.flows.items()}

    def test_late_hello(self):
        st = state()
        self.lp.count(110, [pfstate.Delta(st, 10, 20, 1, 1, True)])
        self.assertEqual(self.lp.flows, {})                                 # held, not named "guess.example"
        self.lp.names.tls({"ts": 109, "client": "10.9.1.20", "cport": 51000, "server": "203.0.113.10", "port": 443,
                           "sni": "www.example"})
        self.lp.count(120, [pfstate.Delta(st, 5, 5, 1, 1, False)])
        self.assertEqual(self.rows(), {"www.example": [15, 25]})

    def test_hello_already_in(self):
        self.lp.names.tls({"ts": 109, "client": "10.9.1.20", "cport": 51000, "server": "203.0.113.10", "port": 443,
                           "sni": "www.example"})
        self.lp.count(110, [pfstate.Delta(state(), 10, 20, 1, 1, True)])
        self.assertEqual(self.rows(), {"www.example": [10, 20]})

    def test_neighbours_replace_the_address_map(self):
        """Each refresh is the kernel's tables as they are: a rotated IPv6 address goes, and an address another
        device took is that device's."""
        import loupd
        from unittest import mock
        self.lp.ip_mac = {"10.9.1.20": "02:00:00:00:00:01", "2001:db8::99": "02:00:00:00:00:01"}
        with mock.patch.object(loupd.neighbours, "table", return_value={"10.9.1.20": "02:00:00:00:00:02"}):
            self.lp.refresh_macs()
        self.assertEqual(self.lp.ip_mac, {"10.9.1.20": "02:00:00:00:00:02"})

    def test_no_hello(self):
        self.lp.count(110, [pfstate.Delta(state(), 10, 20, 1, 1, True)])
        self.lp.count(120, [])
        self.assertEqual(self.rows(), {"guess.example": [10, 20]})        # plain connections: one poll later


class TestStore(unittest.TestCase):
    def test_write_accumulates(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x", "loupe.db")
            s = store.Store(path)
            k = (3600 + 300, "10.9.1.20", "203.0.113.10", 443, "tcp", "www.example")
            s.write({k: ["02:00:00:00:00:01", "sni", 0, 10, 100, 3, 1]}, {(0, "10.9.1.20", "www.example", "sni"): [1, 5, 5, ""]},
                    {"02:00:00:00:00:01": {"ip": "10.9.1.20", "ts": 5, "hostname": "box",
                                           "info": {"mdns_models": ["model=X"]}}})
            k2 = (3600 + 600, *k[1:])
            s.write({k2: ["", "sni", 0, 1, 2, 1, 0]}, {(0, "10.9.1.20", "www.example", "sni"): [2, 6, 9, "02:00:00:00:00:01"]},
                    {"02:00:00:00:00:01": {"ts": 9, "info": {"mdns_models": ["model=Y"]}}})
            db = sqlite3.connect(path)
            self.addCleanup(db.close)
            self.addCleanup(s.close)
            self.assertEqual(db.execute("SELECT count(*), sum(down) FROM flows_5m").fetchone(), (2, 102))
            # a minute whose device wasn't known (no MAC) is kept apart: it isn't credited to whichever device the
            # address had in that hour
            self.assertEqual(sorted(db.execute("SELECT bucket, mac, up, down, conns FROM flows_1h")),
                             [(3600, "", 1, 2, 0), (3600, "02:00:00:00:00:01", 10, 100, 1)])
            self.assertEqual(db.execute("SELECT count, first_seen, last_seen, mac FROM lookups").fetchone(),
                             (3, 5, 9, "02:00:00:00:00:01"))
            row = db.execute("SELECT ip, hostname, info, first_seen, last_seen FROM devices").fetchone()
            self.assertEqual(row, ("10.9.1.20", "box", '{"mdns_models": ["model=X", "model=Y"]}', 5, 9))
            k3 = (3600 + 900, *k[1:])
            s.write({k3: ["", "sni", 0, 1, 1, 1, 0]}, {}, {}, now=4000)
            s.write({k3: ["", "sni", 0, 1, 1, 1, 0]}, {}, {}, now=3990)      # never goes back
            self.assertEqual(db.execute("SELECT last FROM flows_5m WHERE bucket = ?", (k3[0],)).fetchone(), (4000,))
            self.assertGreater(db.execute("SELECT last FROM flows_5m WHERE bucket = ?", (k2[0],)).fetchone()[0], 4000)
            self.assertEqual(db.execute("SELECT count(*), sum(down) FROM flows_1m").fetchone()[0], 3)
            s.prune(3600 + 31 * 86400)
            self.assertEqual(db.execute("SELECT count(*) FROM flows_1m").fetchone(), (0,))
            self.assertEqual(db.execute("SELECT count(*) FROM flows_5m").fetchone(), (0,))
            self.assertEqual(db.execute("SELECT count(*) FROM flows_1h").fetchone(), (2,))


    def test_lookups_by_mac(self):
        """Names follow the device across addresses; rows without a MAC still count by address."""
        with tempfile.TemporaryDirectory() as d:
            s = store.Store(os.path.join(d, "loupe.db"))
            self.addCleanup(s.close)
            mac = "02:00:00:00:00:02"
            s.write({}, {(0, "10.9.1.21", "a.example", "dns"): [1, 5, 5, mac],
                         (0, "2001:db8::21", "b.example", "dns"): [1, 5, 5, mac],
                         (0, "10.9.1.99", "c.example", "dns"): [1, 5, 5, ""]},
                    {mac: {"ip": "10.9.1.99", "ts": 5}})
            [(m, ip, _hn, _info, names)] = s.devices_with_names(0)
            self.assertEqual((m, ip, names), (mac, "10.9.1.99", ["a.example", "b.example", "c.example"]))

    def test_lookups_mac_migration(self):
        """An older database gets the column, filled from the traffic rows of that day."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "loupe.db")
            db = sqlite3.connect(path)
            db.executescript(store.SCHEMA.replace("    mac TEXT NOT NULL DEFAULT '',   -- the device at that address then ('' = not known)\n", ""))
            db.execute("INSERT INTO lookups VALUES (86400, '10.9.1.22', 'a.example', 'dns', 1, 86400, 86400)")
            db.execute("INSERT INTO lookups VALUES (86400, '10.9.1.23', 'b.example', 'dns', 1, 86400, 86400)")
            db.execute("INSERT INTO flows_1h (bucket, ip, mac, server, port, proto) "
                       "VALUES (90000, '10.9.1.22', '02:00:00:00:00:03', '203.0.113.1', 443, 'tcp')")
            db.commit()
            db.close()
            s = store.Store(path)
            self.addCleanup(s.close)
            self.assertEqual(s.db.execute("SELECT ip, mac FROM lookups ORDER BY ip").fetchall(),
                             [("10.9.1.22", "02:00:00:00:00:03"), ("10.9.1.23", "")])


class TestDhcp(unittest.TestCase):
    def test_junk_hostname_ignored(self):
        from lib import dhcp
        base = bytearray(240)
        base[0], base[2] = 1, 6
        base[28:34] = bytes.fromhex("ac0000000001")
        base[236:240] = dhcp.MAGIC
        good = bytes(base) + bytes([12, 6]) + b"box-01" + b"\xff"
        junk = bytes(base) + bytes([12, 8]) + b" &\t0\xef\xbf\x01\x10" + b"\xff"
        self.assertEqual(dhcp.parse(good)["hostname"], "box-01")
        self.assertNotIn("hostname", dhcp.parse(junk))


class TestMalformed(unittest.TestCase):
    def test_truncated_dns_is_valueerror(self):
        from lib import dns, mdns
        msg = bytes.fromhex("1234 8180 0001 0001 0000 0000".replace(" ", "")) + b"\x03foo\x00\x00"   # cut short
        with self.assertRaises(ValueError):
            dns.parse(msg)
        self.assertIsNone(mdns.parse(msg))


class TestUnboundConf(unittest.TestCase):
    def test_remote_control_settings(self):
        from lib import unbound
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
            f.write("server:\n    interface: 0.0.0.0\n    control-port: 1\nremote-control:\n    control-enable: yes\n"
                    "    control-interface: 127.0.0.1\n    control-port: 8953  # comment\n"
                    "    server-cert-file: \"/x/s.pem\"\n    control-key-file: /x/c.key\n    control-cert-file: /x/c.pem\n")
        try:
            self.assertEqual(unbound.control_settings(f.name), ("127.0.0.1", 8953, "/x/s.pem", "/x/c.pem", "/x/c.key"))
        finally:
            os.unlink(f.name)


class TestSeenMerge(unittest.TestCase):
    def test_latest_time_per_item_kept(self):
        with tempfile.TemporaryDirectory() as d:
            s = store.Store(os.path.join(d, "loupe.db"))
            mac = "02:00:00:00:00:71"
            s.write({}, {}, {mac: {"ip": "10.9.1.71", "ts": 100, "info": {"mdns_models": ["model=Mac14,14"], "seen": {"m|model=Mac14,14": 100, "h|x.local": 100}}}})
            s.write({}, {}, {mac: {"ip": "10.9.1.71", "ts": 50, "info": {"seen": {"m|model=Mac14,14": 50, "h|y.local": 60}}}})
            info = {m: i for m, _ip, _hn, i, _n in s.devices_with_names(0)}[mac]
            s.close()
            self.assertEqual(info["seen"], {"m|model=Mac14,14": 100, "h|x.local": 100, "h|y.local": 60})
            self.assertEqual(info["mdns_models"], ["model=Mac14,14"])


if __name__ == "__main__":
    unittest.main()


class TestSeed(unittest.TestCase):
    def test_unbound_cache(self):
        n = names.Names()
        n.seed_unbound("START_RRSET_CACHE\n;rrset 86 1 0 3 3\nwww.example.\t86\tIN\tCNAME\tedge.cdn.example.\n"
                       ";rrset 20 1 0 3 3\nedge.cdn.example.\t20\tIN\tA\t203.0.113.10\nEND_RRSET_CACHE\n", 100)
        self.assertEqual(n.name_for(state(), 101), ("www.example", "dns*"))


class TestGroup(unittest.TestCase):
    def test_multicast_dropped(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        import bench_pfstate
        from lib import pfnl
        msg = bench_pfstate.message(9, "bridge0", True, ("224.0.0.251", 5353), ("10.9.1.20", 5353), "udp", (1, 0), (60, 0), 1)
        self.assertEqual(pfstate.from_netlink(pfnl.parse_states([msg], {"bridge0"}),
                                              pfstate.local_matcher(["10.9.0.0/16"]), {"bridge0"}), {})


class TestMacInKey(unittest.TestCase):
    OLD = """CREATE TABLE {t} (bucket INTEGER NOT NULL, ip TEXT NOT NULL, mac TEXT NOT NULL DEFAULT '', server TEXT NOT NULL,
             port INTEGER NOT NULL, proto TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
             inbound INTEGER NOT NULL DEFAULT 0, up INTEGER NOT NULL DEFAULT 0, down INTEGER NOT NULL DEFAULT 0,
             pkts INTEGER NOT NULL DEFAULT 0, conns INTEGER NOT NULL DEFAULT 0, last INTEGER NOT NULL DEFAULT 0,
             PRIMARY KEY (bucket, ip, server, port, proto, name)) WITHOUT ROWID"""

    def test_old_tables_get_the_mac_in_their_key(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "loupe.db")
            db = sqlite3.connect(path)
            for t in ("flows_1m", "flows_5m", "flows_1h"):
                db.execute(self.OLD.format(t=t))
                db.execute(f"CREATE INDEX {t}_ip ON {t} (ip, bucket)")
                db.execute(f"INSERT INTO {t} VALUES (3600, '10.9.4.1', '02:00:00:00:00:81', '192.0.2.40', 443, 'tcp', "
                           f"'a.example', 'sni', 0, 5, 50, 3, 1, 3700)")
            db.commit()
            db.close()
            s = store.Store(path)
            for t in ("flows_1m", "flows_5m", "flows_1h"):
                key = [r[1] for r in sorted(s.db.execute(f"PRAGMA table_info({t})"), key=lambda r: r[5]) if r[5]]
                self.assertEqual(key, ["bucket", "ip", "mac", "server", "port", "proto", "name"])
                self.assertEqual(s.db.execute(f"SELECT up, down, last FROM {t}").fetchall(), [(5, 50, 3700)])
                idx = {r[1] for r in s.db.execute(f"PRAGMA index_list({t})")}
                self.assertIn(f"{t}_ip", idx)
            # another device on the same address in the same hour: its own row, not added to the first one's
            s.write({(3660, "10.9.4.1", "192.0.2.40", 443, "tcp", "a.example"): ["02:00:00:00:00:82", "sni", 0, 1, 10, 2, 1]},
                    {}, {}, now=3720)
            self.assertEqual(sorted(s.db.execute("SELECT mac, down FROM flows_1h")),
                             [("02:00:00:00:00:81", 50), ("02:00:00:00:00:82", 10)])
            s.close()
            store.Store(path).close()       # opening again: nothing left to migrate
