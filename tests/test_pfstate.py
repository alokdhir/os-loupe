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
import socket
import struct
import sys
import unittest

import helpers  # noqa: F401  (sets sys.path)
from lib import pfnl, pfstate

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import bench_pfstate  # noqa: E402

# Synthetic pf states as the kernel sends them over netlink: LAN 10.9.0.0/16, WAN 198.51.100.7,
# servers in 203.0.113.0/24. left/right as pfctl prints them ("left <- right" when inbound).
M = bench_pfstate.message


def table(bytes1=(1000, 50000), bytes5=(400000, 3000)):
    return [
        M(1, "bridge0", True, ("203.0.113.10", 443), ("10.9.1.20", 51000), "tcp", (10, 20), bytes1, 5),
        M(2, "ix1", False, ("10.9.1.20", 51000), ("203.0.113.10", 443), "tcp", (10, 20), (1000, 50000), 5),  # NAT'd twin
        M(3, "bridge0", True, ("2001:db8::5", 443), ("2001:db8:1::20", 60000), "udp", (5, 7), (700, 9000), 3600),
        M(4, "ix1", True, ("10.9.10.10", 22), ("203.0.113.99", 55555), "tcp", (30, 40), (3000, 400000), 600),
        M(5, "bridge0", False, ("203.0.113.99", 55555), ("10.9.10.10", 22), "tcp", (30, 40), bytes5[::-1], 600),
        M(6, "bridge0", True, ("10.9.0.1", 53), ("10.9.1.20", 5353), "udp", (1, 1), (60, 120), 1),          # LAN <-> router
        M(7, "bridge0", True, ("2001:db8:1::1", 135), ("fe80::ac00:ff:fe00:1", 0), "ipv6-icmp", (1, 1), (72, 0), 1),  # NS
        M(8, "bridge0", True, ("2001:db8:1::30", 135), ("::", 0), "ipv6-icmp", (0, 1), (0, 72), 1),               # DAD
    ]


LOCAL = pfstate.local_matcher(["10.9.0.0/16", "2001:db8:1::/64"])


def states(msgs):
    return pfstate.from_netlink(pfnl.parse_states(msgs, {"bridge0"}), LOCAL, {"bridge0"})


class TestPfState(unittest.TestCase):
    def parse(self):
        return states(table())

    def test_lan_side_only(self):
        keys = {int(k[0], 16) for k in self.parse()}
        # NAT'd WAN state (2), WAN side of the port forward (4), LAN<->router (6) and neighbour discovery from a
        # link-local address to the router's global one (7: only the router's end is in a local network) and
        # duplicate address detection from :: (8: would make every new address a device) are dropped
        self.assertEqual(keys, {1, 3, 5})

    def test_outbound_directions(self):
        st = self.parse()[(f"{1:016x}", "aaaa0001")]
        self.assertEqual((st.local, st.lport, st.remote, st.rport), ("10.9.1.20", 51000, "203.0.113.10", 443))
        self.assertTrue(st.outbound)
        self.assertEqual((st.up, st.down, st.pkts_up, st.pkts_down), (1000, 50000, 10, 20))

    def test_ipv6(self):
        st = self.parse()[(f"{3:016x}", "aaaa0001")]
        self.assertEqual((st.local, st.remote, st.rport, st.proto), ("2001:db8:1::20", "2001:db8::5", 443, "udp"))
        self.assertEqual(st.age, 3600)

    def test_inbound_port_forward(self):
        st = self.parse()[(f"{5:016x}", "aaaa0001")]
        self.assertEqual((st.local, st.lport, st.remote), ("10.9.10.10", 22, "203.0.113.99"))
        self.assertFalse(st.outbound)
        self.assertEqual((st.up, st.down), (400000, 3000))  # the server sent the big side

    def test_tracker(self):
        t = pfstate.Tracker(fresh=15)
        d = {int(x.state.key[0], 16): x for x in t.update(self.parse())}
        self.assertEqual(set(d), {1})                        # old states are a baseline only
        d = {int(x.state.key[0], 16): x for x in t.update(states(table((1500, 90000), (400100, 3000))))}
        self.assertEqual((d[1].up, d[1].down, d[1].new), (500, 40000, False))
        self.assertEqual((d[5].up, d[5].down), (100, 0))
        self.assertNotIn(3, d)                               # unchanged

    def test_wan_twin_skipped_before_parsing(self):
        self.assertEqual(len(pfnl.parse_states(table(), {"bridge0"})), 6)       # states 2 and 4 never decoded


class TestTrackerUnseen(unittest.TestCase):
    def test_old_state_first_seen_is_a_baseline(self):
        """A long connection that reappears (networks were missing) must not be counted for its whole life."""
        t = pfstate.Tracker(fresh=10)
        old = pfstate.State(("1", "c"), "bridge0", "tcp", "10.9.1.20", 51000, "203.0.113.10", 443, True,
                            5_000_000, 9_000_000, 10, 10, 3600)
        new = old._replace(key=("2", "c"), up=100, down=200, age=4)
        self.assertEqual(t.update({}), [])                      # first snapshot: nothing local yet
        deltas = t.update({old.key: old, new.key: new})
        self.assertEqual([(d.state.key, d.up, d.down) for d in deltas], [(("2", "c"), 100, 200)])

    def test_download_started_during_a_gap_counts(self):
        t = pfstate.Tracker(fresh=10)
        t.update({}, now=1000)
        st = pfstate.State(("3", "c"), "bridge0", "tcp", "10.9.1.20", 51001, "203.0.113.10", 443, True,
                           1000, 2_000_000_000, 10, 10, 60)
        self.assertEqual([d.down for d in t.update({st.key: st}, now=1070)], [2_000_000_000])      # 70 s gap, 60 s old


class TestLargeTable(unittest.TestCase):
    def test_synthetic_dump(self):
        """The benchmark's table: every connection kept once, from its LAN-side state, with the right direction."""
        got = states(bench_pfstate.synthetic(300))
        self.assertEqual(len(got), 300)
        self.assertTrue(all(st.origif == "bridge0" for st in got.values()))
        st = got[(f"{0:016x}", "aaaa0001")]
        self.assertEqual((st.local, st.remote, st.rport, st.outbound, st.up, st.down), ("10.9.0.1", "203.0.113.1", 443, True, 0, 0))

class TestNeighbours(unittest.TestCase):
    def record(self, sa_dst, mac):
        from lib import neighbours
        dl = bytes([20, neighbours.AF_LINK, 1, 0, 6, 0, 6, 0]) + bytes.fromhex(mac.replace(":", "")) + b"\0" * 6
        body = sa_dst + b"\0" * (-len(sa_dst) % neighbours.ALIGN) + dl + b"\0" * (-len(dl) % neighbours.ALIGN)
        hdr = bytearray(neighbours.HDR)
        struct.pack_into("H", hdr, 0, neighbours.HDR + len(body))
        struct.pack_into("i", hdr, neighbours.ADDRS, neighbours.RTA_DST | neighbours.RTA_GATEWAY)
        return bytes(hdr) + body

    def test_parse(self):
        from lib import neighbours
        v4 = bytes([16, socket.AF_INET, 0, 0]) + socket.inet_aton("10.9.1.20") + b"\0" * 8
        ll = bytearray(socket.inet_pton(socket.AF_INET6, "fe80::1"))
        ll[3] = 5                                                   # embedded scope id
        v6 = bytes([28, socket.AF_INET6, 0, 0, 0, 0, 0, 0]) + bytes(ll) + b"\0" * 4
        got = neighbours.parse(self.record(v4, "02:00:00:00:00:01") + self.record(v6, "02:00:00:00:00:02"))
        self.assertEqual(got, {"10.9.1.20": "02:00:00:00:00:01", "fe80::1": "02:00:00:00:00:02"})


class TestNetif(unittest.TestCase):
    def test_short_netmask_padded(self):
        import ctypes
        from lib import netif
        mask = ctypes.create_string_buffer(bytes([7, 0, 0, 0, 255, 255, 240]))     # trimmed /20, as the kernel sends it
        self.assertEqual(netif._sockaddr(ctypes.addressof(mask), socket.AF_INET, 4), bytes([255, 255, 240, 0]))

    @unittest.skipUnless(sys.platform.startswith(("freebsd", "darwin")), "BSD sockaddr layout")
    def test_loopback(self):
        from lib import netif
        self.assertIn("127.0.0.1/8", [str(i) for _, i in netif.addresses({"lo0"})])


class TestBpfCompile(unittest.TestCase):
    def setUp(self):
        import ctypes.util
        if not ctypes.util.find_library("pcap"):
            self.skipTest("libpcap not installed")

    def test_compile(self):
        from lib import bpf
        prog = bpf.compile_filter("udp src port 53")
        self.assertGreater(len(prog), 3)
        self.assertEqual(prog[-1][0] & 0x07, 0x06)          # ends in a return
        with self.assertRaises(ValueError):
            bpf.compile_filter("tcp and bogus(")


if __name__ == "__main__":
    unittest.main()
