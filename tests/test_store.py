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


class TestStore(unittest.TestCase):
    def test_write_accumulates(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x", "loupe.db")
            s = store.Store(path)
            k = (3600 + 300, "10.9.1.20", "203.0.113.10", 443, "tcp", "www.example")
            s.write({k: ["02:00:00:00:00:01", "sni", 0, 10, 100, 3, 1]}, {(0, "10.9.1.20", "www.example", "sni"): [1, 5, 5]},
                    {"02:00:00:00:00:01": {"ip": "10.9.1.20", "ts": 5, "hostname": "box",
                                           "info": {"mdns_models": ["model=X"]}}})
            k2 = (3600 + 600, *k[1:])
            s.write({k2: ["", "sni", 0, 1, 2, 1, 0]}, {(0, "10.9.1.20", "www.example", "sni"): [2, 6, 9]},
                    {"02:00:00:00:00:01": {"ts": 9, "info": {"mdns_models": ["model=Y"]}}})
            db = sqlite3.connect(path)
            self.assertEqual(db.execute("SELECT count(*), sum(down) FROM flows_5m").fetchone(), (2, 102))
            self.assertEqual(db.execute("SELECT bucket, mac, up, down, conns FROM flows_1h").fetchall(),
                             [(3600, "02:00:00:00:00:01", 11, 102, 1)])
            self.assertEqual(db.execute("SELECT count, first_seen, last_seen FROM lookups").fetchone(), (3, 5, 9))
            row = db.execute("SELECT ip, hostname, info, first_seen, last_seen FROM devices").fetchone()
            self.assertEqual(row, ("10.9.1.20", "box", '{"mdns_models": ["model=X", "model=Y"]}', 5, 9))
            k3 = (3600 + 900, *k[1:])
            s.write({k3: ["", "sni", 0, 1, 1, 1, 0]}, {}, {}, now=4000)
            s.write({k3: ["", "sni", 0, 1, 1, 1, 0]}, {}, {}, now=3990)      # never goes back
            self.assertEqual(db.execute("SELECT last FROM flows_5m WHERE bucket = ?", (k3[0],)).fetchone(), (4000,))
            self.assertGreater(db.execute("SELECT last FROM flows_5m WHERE bucket = ?", (k2[0],)).fetchone()[0], 4000)
            s.prune(3600 + 31 * 86400)
            self.assertEqual(db.execute("SELECT count(*) FROM flows_5m").fetchone(), (0,))
            self.assertEqual(db.execute("SELECT count(*) FROM flows_1h").fetchone(), (1,))


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
        text = ("all udp 224.0.0.251:5353 <- 10.9.1.20:5353       MULTIPLE:SINGLE\n"
                "   age 00:00:01, expires in 00:00:59, 1:0 pkts, 60:0 bytes, rule 5\n"
                "   id: 0000000000000009 creatorid: aaaa0001\n   origif: bridge0\n")
        self.assertEqual(pfstate.parse(text, pfstate.local_matcher(["10.9.0.0/16"]), {"bridge0"}), {})
