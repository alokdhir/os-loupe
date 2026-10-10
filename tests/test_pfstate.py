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


if __name__ == "__main__":
    unittest.main()
