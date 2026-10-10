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

import socket
import struct
import sys
import unittest

import helpers  # noqa: F401  (sets sys.path)
from lib import pfstate

# Synthetic pfctl -ss -vv output: LAN 10.9.0.0/16, WAN 198.51.100.7, servers in 203.0.113.0/24.
TEXT = """\
No ALTQ support in kernel
all tcp 203.0.113.10:443 <- 10.9.1.20:51000       ESTABLISHED:ESTABLISHED
   [1 + 2] wscale 7  [3 + 4] wscale 6
   age 00:00:05, expires in 23:59:55, 10:20 pkts, 1000:50000 bytes, rule 83
   id: 0000000000000001 creatorid: aaaa0001
   origif: bridge0
all tcp 198.51.100.7:40000 (10.9.1.20:51000) -> 203.0.113.10:443       ESTABLISHED:ESTABLISHED
   [3 + 4] wscale 6  [1 + 2] wscale 7
   age 00:00:05, expires in 23:59:55, 10:20 pkts, 1000:50000 bytes, rule 79
   id: 0000000000000002 creatorid: aaaa0001
   origif: ix1
all udp 2001:db8::5[443] <- 2001:db8:1::20[60000]       MULTIPLE:MULTIPLE
   age 01:00:00, expires in 00:00:59, 5:7 pkts, 700:9000 bytes, rule 90
   id: 0000000000000003 creatorid: aaaa0001
   origif: bridge0
all tcp 10.9.10.10:22 <- 203.0.113.99:55555       ESTABLISHED:ESTABLISHED
   age 00:10:00, expires in 23:50:00, 30:40 pkts, 3000:400000 bytes, rule 12
   id: 0000000000000004 creatorid: aaaa0001
   origif: ix1
all tcp 203.0.113.99:55555 -> 10.9.10.10:22       ESTABLISHED:ESTABLISHED
   age 00:10:00, expires in 23:50:00, 30:40 pkts, 3000:400000 bytes, rule 12
   id: 0000000000000005 creatorid: aaaa0001
   origif: bridge0
all udp 10.9.0.1:53 <- 10.9.1.20:5353       MULTIPLE:SINGLE
   age 00:00:01, expires in 00:00:59, 1:1 pkts, 60:120 bytes, rule 5
   id: 0000000000000006 creatorid: aaaa0001
   origif: bridge0
"""

LOCAL = pfstate.local_matcher(["10.9.0.0/16", "2001:db8:1::/64"])


class TestPfState(unittest.TestCase):
    def parse(self):
        return pfstate.parse(TEXT, LOCAL, {"bridge0"})

    def test_lan_side_only(self):
        keys = {k[0][-1] for k in self.parse()}
        # NAT'd WAN state (2), WAN side of the port forward (4) and LAN<->router (6) are dropped
        self.assertEqual(keys, {"1", "3", "5"})

    def test_outbound_directions(self):
        st = self.parse()[("0000000000000001", "aaaa0001")]
        self.assertEqual((st.local, st.lport, st.remote, st.rport), ("10.9.1.20", 51000, "203.0.113.10", 443))
        self.assertTrue(st.outbound)
        self.assertEqual((st.up, st.down, st.pkts_up, st.pkts_down), (1000, 50000, 10, 20))

    def test_ipv6(self):
        st = self.parse()[("0000000000000003", "aaaa0001")]
        self.assertEqual((st.local, st.remote, st.rport, st.proto), ("2001:db8:1::20", "2001:db8::5", 443, "udp"))
        self.assertEqual(st.age, 3600)

    def test_inbound_port_forward(self):
        st = self.parse()[("0000000000000005", "aaaa0001")]
        self.assertEqual((st.local, st.lport, st.remote), ("10.9.10.10", 22, "203.0.113.99"))
        self.assertFalse(st.outbound)
        self.assertEqual((st.up, st.down), (400000, 3000))  # the server sent the big side

    def test_tracker(self):
        t = pfstate.Tracker(fresh=15)
        d = {x.state.key[0][-1]: x for x in t.update(self.parse())}
        self.assertEqual(set(d), {"1"})                      # old states are a baseline only
        later = TEXT.replace("1000:50000 bytes", "1500:90000 bytes").replace("3000:400000", "3000:400100")
        d = {x.state.key[0][-1]: x for x in t.update(pfstate.parse(later, LOCAL, {"bridge0"}))}
        self.assertEqual((d["1"].up, d["1"].down, d["1"].new), (500, 40000, False))
        self.assertEqual((d["5"].up, d["5"].down), (100, 0))
        self.assertNotIn("3", d)                             # unchanged


class TestLargeTable(unittest.TestCase):
    def test_synthetic_dump(self):
        """The benchmark's dump: every connection kept once, from its LAN-side state, with the right direction."""
        import os
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        import bench_pfstate
        states = pfstate.parse(bench_pfstate.synthetic(300), LOCAL, {"bridge0"})
        self.assertEqual(len(states), 300)
        self.assertTrue(all(st.origif == "bridge0" for st in states.values()))
        st = states[("0000000000000000", "aaaa0001")]
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


class TestNetlink(unittest.TestCase):
    """Synthetic GETSTATES replies give the same states as the text parser does for TEXT's first two records."""

    def msg(self, sid, origif, inbound, a0, p0, a1, p1, counts, age):
        from lib import pfnl
        A = pfnl._attr
        key = (A(pfnl.STK_ADDR0, socket.inet_aton(a0)) + A(pfnl.STK_ADDR1, socket.inet_aton(a1))
               + A(pfnl.STK_PORT0, struct.pack("!H", p0)) + A(pfnl.STK_PORT1, struct.pack("!H", p1)))
        return (A(pfnl.ST_ID, struct.pack("Q", sid)) + A(pfnl.ST_CREATORID, struct.pack("I", 0xaaaa0001))
                + A(3, b"all\0") + A(pfnl.ST_ORIG_IFNAME, origif.encode() + b"\0") + A(pfnl.ST_KEY_STACK, key)
                + A(pfnl.ST_CREATION, struct.pack("I", age)) + A(pfnl.ST_PACKETS0, struct.pack("Q", counts[0]))
                + A(pfnl.ST_PACKETS1, struct.pack("Q", counts[1])) + A(pfnl.ST_BYTES0, struct.pack("Q", counts[2]))
                + A(pfnl.ST_BYTES1, struct.pack("Q", counts[3])) + A(pfnl.ST_PROTO, b"\x06")
                + A(pfnl.ST_DIRECTION, bytes([pfnl.PF_IN if inbound else 2])) + A(40, b"label\0"))

    def test_same_as_text(self):
        from lib import pfnl
        msgs = [self.msg(1, "bridge0", True, "10.9.1.20", 51000, "203.0.113.10", 443, (10, 20, 1000, 50000), 5),
                self.msg(2, "ix1", False, "203.0.113.10", 443, "198.51.100.7", 40000, (10, 20, 1000, 50000), 5)]
        got = pfstate.from_netlink(pfnl.parse_states(msgs, {"bridge0"}), LOCAL, {"bridge0"})
        want = {k: v for k, v in pfstate.parse(TEXT, LOCAL, {"bridge0"}).items() if k[0] == "0000000000000001"}
        self.assertEqual(list(got.values()), list(want.values()))


if __name__ == "__main__":
    unittest.main()
