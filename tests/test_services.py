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

import json
import os
import sqlite3
import tempfile
import time
import unittest

import ipaddress
import random

import helpers  # noqa: F401
from lib import report, services, store


class TestServices(unittest.TestCase):
    def setUp(self):
        self.m = services.ServiceMap({"example.org": "Example", "youtube.com": "Tube"})

    def test_longest_suffix(self):
        self.assertEqual(self.m.by_name("rr3---sn-abc.googlevideo.com"), "YouTube")
        self.assertEqual(self.m.by_name("mask.icloud.com"), "iCloud Private Relay")
        self.assertEqual(self.m.by_name("p42-contacts.icloud.com"), "iCloud")
        self.assertEqual(self.m.by_name("notgoogle.com"), None)

    def test_label_prefix(self):
        self.assertEqual(self.m.by_name("vod-hulu-akc-na.media.dssott.com"), "Hulu")
        self.assertEqual(self.m.by_name("vod-hulu-fa-na.media.dssott.com"), "Hulu")
        self.assertEqual(self.m.by_name("vod-akc-na.media.dssott.com"), "Disney+")
        self.assertEqual(self.m.by_name("x.vod-hulu-akc-na.media.dssott.com"), "Hulu")      # subdomains too, like any domain rule
        m = services.ServiceMap([{"kind": "domain", "pattern": "cdn-*.example.org", "name": "CDN"},
                                 {"kind": "domain", "pattern": "cdn-eu-*.example.org", "name": "EU CDN"}])
        self.assertEqual(m.by_name("cdn-us-1.example.org"), "CDN")
        self.assertEqual(m.by_name("cdn-eu-1.example.org"), "EU CDN")                          # longest prefix
        self.assertEqual(m.by_name("www.example.org"), None)

    def test_user_overrides(self):
        self.assertEqual(self.m.by_name("a.example.org"), "Example")
        self.assertEqual(self.m.by_name("www.youtube.com"), "Tube")

    def test_fallbacks(self):
        self.assertEqual(self.m.service("", "17.250.96.102", 443, "udp"), "iCloud Private Relay")
        self.assertEqual(self.m.service("", "17.57.147.7", 5223, "tcp"), "Apple")
        self.assertEqual(self.m.service("", "203.0.113.9", 51820, "udp"), "VPN (WireGuard)")
        self.assertEqual(self.m.service("", "203.0.113.9", 9999, "tcp"), None)


    def test_user_addresses(self):
        m = services.ServiceMap({"192.0.2.10": "My server", "203.0.113.0/24": "Office", "203.0.113.128/25": "Office B",
                                 "example.org": "Example"})
        self.assertEqual(m.service("vps.amazonaws.com", "192.0.2.10", 443, "tcp"), "My server")   # beats the name
        self.assertEqual(m.service("", "203.0.113.5", 443, "tcp"), "Office")
        self.assertEqual(m.service("", "203.0.113.200", 443, "tcp"), "Office B")                  # most specific
        self.assertEqual(m.service("www.example.org", "198.51.100.1", 443, "tcp"), "Example")
        self.assertEqual(m.service("vps.amazonaws.com", "198.51.100.2", 443, "tcp"), "AWS")

    def test_user_kinds(self):
        m = services.ServiceMap([{"kind": "port", "pattern": "tcp/8443", "name": "Admin UI"},
                                 {"kind": "port", "pattern": "udp/51820", "name": "Home VPN"},
                                 {"kind": "vpn", "pattern": "myvpn.example", "name": "MyVPN"},
                                 {"kind": "address", "pattern": "198.51.100.0/24", "name": "Lab"},
                                 {"kind": "domain", "pattern": "example.net", "name": "Example"}])
        self.assertEqual(m.service("", "203.0.113.9", 8443, "tcp"), "Admin UI")
        self.assertEqual(m.service("", "203.0.113.9", 51820, "udp"), "Home VPN")                # beats the shipped port
        self.assertEqual(m.service("", "198.51.100.3", 22, "tcp"), "Lab")
        self.assertEqual(m.service("cdn.example.net", "203.0.113.9", 443, "tcp"), "Example")
        self.assertEqual(m.vpn_provider("api.myvpn.example"), "MyVPN")
        self.assertEqual(m.vpn_provider("api.nordvpn.com"), "NordVPN")

    def test_vpn_provider(self):
        self.assertEqual(services.ServiceMap().vpn_provider("api.nordvpn.com"), "NordVPN")
        self.assertEqual(services.ServiceMap().vpn_provider("nordvpn.com."), "NordVPN")
        self.assertEqual(services.ServiceMap().vpn_provider("notnordvpn.com"), None)
        self.assertEqual(services.ServiceMap().vpn_provider(""), None)


class TestAddressTable(unittest.TestCase):
    def test_same_as_scanning_the_list(self):
        nets = [(ipaddress.ip_network(n), s) for n, s in [
            ("198.51.100.0/24", "wide"), ("198.51.100.128/25", "narrow, listed later"), ("198.51.100.7/32", "one host"),
            ("203.0.113.0/26", "a"), ("203.0.113.0/26", "same network again"), ("10.0.0.0/8", "big"),
            ("2001:db8::/32", "v6"), ("2001:db8:1::/48", "v6 narrow"), ("0.0.0.0/0", "everything v4")]]

        def scan(addr):
            try:
                a = ipaddress.ip_address(addr)
            except ValueError:
                return None
            return next((s for net, s in nets if a.version == net.version and a in net), None)

        table = services.AddressTable(nets)
        rnd = random.Random(1)
        addrs = ["198.51.100.7", "198.51.100.200", "203.0.113.5", "2001:db8:1::9", "2001:db9::1", "not an address", ""]
        addrs += [str(ipaddress.IPv4Address(rnd.getrandbits(32))) for _ in range(2000)]
        addrs += ["198.51.100.%d" % i for i in range(256)] + ["2001:db8:%x::1" % i for i in range(4)]
        for a in addrs * 2:                     # twice: the second answer comes from the cache
            self.assertEqual(table.get(a), scan(a), a)
        self.assertFalse(services.AddressTable([]))


class TestSharedServer(unittest.TestCase):
    def test_borrows_one_service(self):
        import query
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "loupe.db")
            s = store.Store(path)
            b = 3600
            mac = "02:00:00:00:00:40"
            s.write({(b, "10.9.1.40", "203.0.113.10", 443, "tcp", "api.nfl.com"): [mac, "", 0, 1, 2, 1, 1],
                     (b, "10.9.1.40", "203.0.113.10", 443, "tcp", "video.example.net"): [mac, "", 0, 1, 900, 1, 1],
                     (b, "10.9.1.40", "203.0.113.10", 443, "udp", ""): [mac, "", 0, 1, 500, 1, 1],
                     (b, "10.9.1.40", "203.0.113.10", 443, "tcp", "x.fastly.net"): [mac, "", 0, 1, 5, 1, 1],
                     (b, "10.9.1.40", "203.0.113.20", 443, "tcp", "www.hulu.com"): [mac, "", 0, 1, 2, 1, 1],
                     (b, "10.9.1.40", "203.0.113.20", 443, "tcp", "www.netflix.com"): [mac, "", 0, 1, 2, 1, 1],
                     (b, "10.9.1.40", "203.0.113.20", 443, "tcp", "cdn.example.net"): [mac, "", 0, 1, 2, 1, 1],
                     (b, "10.9.1.41", "203.0.113.10", 443, "tcp", "video.example.net"): ["02:00:00:00:00:41", "", 0, 1, 2, 1, 1]},
                    {}, {})
            s.close()
            db = sqlite3.connect(path)
            try:
                lab = report.Labels(db, services.ServiceMap(), "flows_5m", b)
                for name, proto in (("video.example.net", "tcp"), ("", "udp"), ("x.fastly.net", "tcp")):
                    self.assertEqual(lab.service("10.9.1.40", name, "203.0.113.10", 443, proto), "NFL")
                    self.assertTrue(lab.inferred("10.9.1.40", name, "203.0.113.10", 443, proto))
                self.assertEqual(lab.service("10.9.1.40", "api.nfl.com", "203.0.113.10", 443, "tcp"), "NFL")
                self.assertFalse(lab.inferred("10.9.1.40", "api.nfl.com", "203.0.113.10", 443, "tcp"))
                self.assertEqual(lab.service("10.9.1.40", "cdn.example.net", "203.0.113.20", 443, "tcp"), None)  # two services there
                self.assertEqual(lab.service("10.9.1.41", "video.example.net", "203.0.113.10", 443, "tcp"), None)  # another device
            finally:
                db.close()


class TestTrafficTree(unittest.TestCase):
    def test_services_with_their_sites(self):
        import collections
        import query
        site = lambda down, ports: {"down": down, "up": 0, "conns": 1, "first": down, "last": 5, "inferred": False,
                                    "servers": {"192.0.2.1"}, "on": set(), "ports": collections.Counter(ports)}
        services = {"Netflix": {"down": 30, "up": 0, "conns": 2, "first": 7, "last": 9, "on": set()},
                    "Other": {"down": 50, "up": 0, "conns": 1, "first": 5, "last": 5, "on": set()}}
        sites = {("Netflix", "a.example"): site(10, {"tcp/443": 10}),
                 ("Netflix", "b.example"): site(20, {"udp/443": 20}),
                 ("Other", "198.51.100.7"): site(50, {"udp/51820": 40, "tcp/443": 10})}
        tree = query.traffic_tree(services, sites)
        self.assertEqual([r["service"] for r in tree], ["Other", "Netflix"])          # biggest first
        self.assertEqual([c["label"] for c in tree[1]["_children"]], ["b.example", "a.example"])
        self.assertEqual(tree[1]["search"], "b.example a.example")
        self.assertEqual(tree[1]["_children"][0]["ports"], [])                         # web ports aren't listed
        self.assertEqual(tree[0]["_children"][0]["ports"], ["udp/51820"])
        self.assertEqual(len({r["id"] for r in tree} | {c["id"] for r in tree for c in r["_children"]}), 5)
        self.assertEqual([r["pct"] for r in tree], [62.5, 37.5])                      # of the device's traffic
        self.assertEqual([c["pct"] for c in tree[1]["_children"]], [25.0, 12.5])
        self.assertEqual((tree[1]["first"], tree[1]["last"]), (7, 9))                  # from all its traffic, not its top sites


def q_lookup(query, row, hours):
    return query.cmd_lookup(row, hours, True)["traffic"]


class TestIdentities(unittest.TestCase):
    def run_query(self, path, conf, arg):
        import query
        old = query.DB, query.CONF
        query.DB, query.CONF = path, conf
        try:
            return query.cmd_identities(arg)
        finally:
            query.DB, query.CONF = old

    def test_only_the_asked_devices(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "loupe.db")
            s = store.Store(path)
            now = int(time.time())
            s.write({}, {}, {"02:00:00:00:00:60": {"ip": "10.9.1.60", "hostname": "tv", "ts": now},
                             "02:00:00:00:00:61": {"ip": "10.9.1.61", "hostname": "phone", "ts": now}})
            s.set_device_types([("Den TV", "Example", "Apple TV", "mdns", "", "02:00:00:00:00:60"),
                                ("Phone", "", "iPhone", "mdns", "", "02:00:00:00:00:61")])
            s.close()
            conf = os.path.join(d, "loupe.json")
            with open(conf, "w") as f:
                json.dump({"devices": {"02:00:00:00:00:61": {"name": "My phone"},
                                       "02:00:00:00:00:62": {"name": "Not seen yet", "type": "Printer"}}}, f)
            out = self.run_query(path, conf, "02:00:00:00:00:60, 02-00-00-00-00-62,02:00:00:00:00:99")
            self.assertEqual(out["version"], 1)
            self.assertEqual(sorted(out["devices"]), ["02:00:00:00:00:60", "02:00:00:00:00:62"])
            tv = out["devices"]["02:00:00:00:00:60"]
            self.assertEqual((tv["name"], tv["type"], tv["vendor"]), ("Den TV", "Apple TV", "Example"))
            self.assertTrue(tv["icon"])
            self.assertEqual(sorted(tv), ["icon", "model", "name", "type", "vendor"])
            self.assertEqual(out["devices"]["02:00:00:00:00:62"]["name"], "Not seen yet")       # an override only
            phone = self.run_query(path, conf, "02:00:00:00:00:61")["devices"]["02:00:00:00:00:61"]
            self.assertEqual(phone["name"], "My phone")                                        # override wins

    def test_bad_lists(self):
        for arg in ("", "nope", "02:00:00:00:00:6", ",".join(["02:00:00:00:00:01"] * 2) + ",x"):
            with self.assertRaises(ValueError):
                self.run_query("/nonexistent", "/nonexistent", arg)


class TestIcons(unittest.TestCase):
    def test_pick_applies_to_the_type(self):
        import query
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "loupe.db")
            store.Store(path).close()
            db = sqlite3.connect(path)
            self.addCleanup(db.close)
            ov = {"02:00:00:00:00:61": {"type": "Dishwasher (Fisher & Paykel)", "icon": "fa-solid fa-soap"},
                  "02:00:00:00:00:62": {"type": "dishwasher (fisher & paykel)"},
                  "02:00:00:00:00:63": {"type": "Dishwasher (Fisher & Paykel)", "icon": "fa-solid fa-kitchen-set"},
                  "02:00:00:00:00:64": {"type": "TV"}}
            info = query.devices_info(db, ov)
            self.assertEqual([info[m]["icon"] for m in sorted(ov)],
                             ["fa-solid fa-soap", "fa-solid fa-soap", "fa-solid fa-kitchen-set", "fa-solid fa-tv"])


class TestVpnLabels(unittest.TestCase):
    def test_provider_and_fallback(self):
        import query
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "loupe.db")
            s = store.Store(path)
            b = 3600
            s.write({(b, "10.9.1.30", "203.0.113.50", 51820, "udp", ""): ["02:00:00:00:00:30", "", 0, 10, 100, 3, 1],
                     (b, "10.9.1.30", "203.0.113.50", 993, "tcp", ""): ["02:00:00:00:00:30", "", 0, 1, 2, 1, 1],
                     (b, "10.9.1.31", "203.0.113.60", 51820, "udp", ""): ["02:00:00:00:00:31", "", 0, 1, 2, 1, 1]},
                    {(0, "10.9.1.30", "api.nordvpn.com", "dns"): [1, b, b, ""],
                     (0, "2001:db8::32", "api.mullvad.net", "dns"): [1, b, b, "02:00:00:00:00:32"]}, {})
            s.close()
            db = sqlite3.connect(path)
            try:
                lab = report.Labels(db, services.ServiceMap(), "flows_5m", b)
                self.assertEqual(lab.service("10.9.1.30", "", "203.0.113.50", 51820, "udp"), "VPN (NordVPN)")
                self.assertEqual(lab.service("10.9.1.30", "", "203.0.113.50", 993, "tcp"), "VPN (NordVPN)")
                self.assertEqual(lab.service("10.9.1.30", "", "203.0.113.70", 993, "tcp"), "Email (IMAP)")
                self.assertEqual(lab.service("10.9.1.31", "", "203.0.113.60", 51820, "udp"), "VPN (WireGuard)")
                self.assertEqual(lab.service("10.9.1.30", "www.youtube.com", "203.0.113.50", 443, "tcp"), "YouTube")
                # the user's own name for a VPN port wins over the shipped one
                mine = report.Labels(db, services.ServiceMap([{"kind": "port", "pattern": "udp/51820",
                                                              "name": "VPN (Home)"}]), "flows_5m", b)
                self.assertEqual(mine.service("10.9.1.31", "", "203.0.113.60", 51820, "udp"), "VPN (Home)")
                # looked up from its IPv6 address, tunnel over IPv4: the same device by MAC
                self.assertEqual(lab.service("10.9.1.32", "", "203.0.113.80", 51820, "udp", "02:00:00:00:00:32"),
                                 "VPN (Mullvad)")
                # one device's evidence (its page, on a day loupd hasn't summarized) is the house's for that device
                for keys in (("10.9.1.30",), ("10.9.1.31", "02:00:00:00:00:31"), ("10.9.1.32", "02:00:00:00:00:32")):
                    own = report.Labels(db, services.ServiceMap(), "flows_5m", b, keys=keys)
                    self.assertEqual(sorted(own.evidence()), sorted(e for e in lab.evidence() if e[1] in keys))
                    self.assertTrue(own.evidence())
            finally:
                db.close()


    def test_vendor_fallback(self):
        import query
        self.assertEqual(query.vendor_of("ac:00:00:00:00:01", "Acme", "Mac"), ("Acme", ""))
        self.assertEqual(query.vendor_of("02:00:00:00:00:01", "", "iPhone"), ("Apple", "from device type (private MAC)"))
        self.assertEqual(query.vendor_of("ac:00:00:00:00:01", "", "Mac"),
                         ("Apple", "from device type (MAC prefix not in the vendor list)"))
        self.assertEqual(query.vendor_of("02:00:00:00:00:01", "", "Android device"), ("", "private MAC"))


if __name__ == "__main__":
    unittest.main()


class TestDailySummary(unittest.TestCase):
    """loupd's per-day, per-service summary gives the long-period lists the same numbers as the hourly rows."""

    def setUp(self):
        import loupd
        import query
        from lib import report
        self.loupd, self.query, self.report = loupd, query, report
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "loupe.db")
        self.store = store.Store(self.path)
        now = int(time.time())
        self.today = report.local_day(now)
        flows = {}
        a, b = "02:00:00:00:00:61", "02:00:00:00:00:62"
        for back in range(3):               # today and the two days before, a few hours each
            day = report.local_day(self.today - back * 86400 + 3600)
            for h in range(3):
                t = max(day, min(day + h * 3600 + 120, now - 120))
                t -= t % 60
                flows[(t, "10.9.2.61", "192.0.2.10", 443, "tcp", "www.netflix.com")] = [a, "sni", 0, 10, 1000 + h, 3, 1]
                flows[(t, "10.9.2.61", "198.51.100.7", 51820, "udp", "")] = [a, "", 0, 50, 70, 4, 1]
                flows[(t, "10.9.2.62", "192.0.2.20", 443, "tcp", "")] = [b, "", 0, 5, 9 + back, 2, 2]
        self.store.write(flows, {}, {a: {"ip": "10.9.2.61", "ts": now}, b: {"ip": "10.9.2.62", "ts": now}}, now)
        self.now = now
        svc = services.ServiceMap()
        self.daemon = type("D", (), {"store": self.store, "svc": svc, "map_fingerprint": report.map_fingerprint(svc)})()
        self.old = query.DB, query.CONF, query.load
        self.opened = []

        def load():
            self.opened.append(self.old[2]())
            return self.opened[-1]
        query.DB, query.CONF, query.load = self.path, os.path.join(self.dir.name, "none.json"), load

    def tearDown(self):
        self.query.DB, self.query.CONF, self.query.load = self.old
        for db, *_ in self.opened:
            db.close()
        self.store.close()
        self.dir.cleanup()

    def summarize(self, budget=10.0):
        self.loupd.Loupe.summarize(self.daemon, self.now, budget)

    def lists(self):
        return [(self.query.cmd_devices(h)["rows"], self.query.cmd_sites(h)["rows"]) for h in (3, 24, 48, 72)]

    def test_same_lists_from_the_summary(self):
        unsummarized = self.lists()           # nothing summarized yet: named from the traffic rows
        self.summarize()
        state = self.store.summary_state(0)
        self.assertEqual(len(state), 3)
        self.assertEqual(self.lists(), unsummarized)
        unsummarized = unsummarized[-1]
        devices = {r["mac"]: r for r in unsummarized[0]}
        self.assertEqual(devices["02:00:00:00:00:61"]["down"], 3 * (1000 + 1001 + 1002) + 9 * 70)
        self.assertEqual(devices["02:00:00:00:00:61"]["top"][0], "Netflix")

    def drilldowns(self):
        """A service's traffic (a services list row clicked) and the services search, over periods read by day."""
        q = self.query
        return [(q.cmd_lookup(row, h, True)["traffic"], q.cmd_sites(h, text)["rows"])
                for h in (72, 168) for row, text in (("netflix", "netflix"), ("192.0.2.20", "192.0.2.20"),
                                                     ("vpn (wireguard)", "198.51.100.7"))]

    def test_same_drilldowns_from_the_site_summary(self):
        live = self.drilldowns()
        self.assertTrue(all(traffic and found for traffic, found in live))
        self.summarize()
        self.assertTrue(self.store.db.execute("SELECT count(*) FROM sites_1d").fetchone()[0])
        self.assertEqual(self.drilldowns(), live)
        for h in (72, 168, 720):        # the chart adds up to the list: from the flows up to a week, by the day beyond
            for row in ("netflix", "192.0.2.20", "vpn (wireguard)"):
                rows = q_lookup(self.query, row, h)
                listed = sum(r["down"] + r["up"] for r in rows)
                self.assertAlmostEqual(sum(r["pct"] for r in rows), 100, delta=0.1 * len(rows))
                chart = self.query.cmd_lookup(row, h, True, chart=True)
                self.assertEqual(sum(p[1] + p[2] for p in chart["timeline"]), listed, (row, h))
                self.assertEqual(chart["step"], 86400 if h > 168 else 3600)
        # the days before today come from the summary alone; today's hours since loupd built it are named here
        self.store.db.execute("DELETE FROM flows_1h WHERE bucket < ?", (self.today,))
        self.assertEqual(self.drilldowns(), live)
        netflix = {(r["mac"], r["name"]): r for r in live[0][0]}
        self.assertEqual(netflix[("02:00:00:00:00:61", "www.netflix.com")]["servers"], ["192.0.2.10"])
        self.assertIsInstance(netflix[("02:00:00:00:00:61", "www.netflix.com")]["icon"], str)    # shown by the name

    def test_site_names(self):
        """Each site, service and address the summaries hold, with the last day: what the search looks up first.
        A row left by a renamed service only makes the search look further; it never shows."""
        self.summarize()
        db = self.store.db
        names = {r[:4]: r[4] for r in db.execute("SELECT site, named, service, server, last_day FROM site_names")}
        self.assertEqual(names[("www.netflix.com", 1, "Netflix", "192.0.2.10")], self.today)
        self.assertEqual(names[("192.0.2.20", 0, "", "192.0.2.20")], self.today)
        before = self.drilldowns()
        db.execute("INSERT INTO site_names VALUES ('www.netflix.com', 1, 'Old name', '192.0.2.10', ?)", (self.today,))
        self.assertEqual(self.drilldowns(), before)
        self.assertEqual(self.query.cmd_sites(168, "old name")["rows"], [])
        self.store.prune(self.today + 2 * 86400, days_1h=1)
        self.assertEqual(db.execute("SELECT count(*) FROM site_names").fetchone(), (0,))

    def test_network_filter(self):
        """Traffic is filed under the network its address is in; a filter keeps those networks' traffic only."""
        orig = self.query.Networks.__init__

        def init(n, conf):
            orig(n, {"networks": [{"id": "lan", "name": "LAN"}, {"id": "opt1", "name": "IoT"}]})
            n.subnets = [(ipaddress.ip_network("10.9.2.60/31"), "lan"), (ipaddress.ip_network("10.9.2.62/31"), "opt1")]
        self.query.Networks.__init__ = init
        a, b = "02:00:00:00:00:61", "02:00:00:00:00:62"
        try:
            listed = self.query.cmd_devices(72)
            self.assertEqual(listed["networks"], [{"id": "lan", "name": "LAN"}, {"id": "opt1", "name": "IoT"}])
            self.assertEqual({r["mac"]: r["networks"] for r in listed["rows"]}, {a: ["lan"], b: ["opt1"]})
            self.assertEqual([r["mac"] for r in self.query.cmd_devices(72, {"lan"})["rows"]], [a])
            self.assertEqual([r["mac"] for r in self.query.cmd_devices(72, {"opt1"})["rows"]], [b])
            self.assertEqual(len(self.query.cmd_devices(72, {"lan", "opt1"})["rows"]), 2)
            self.assertIn("Netflix", {r["service"] for r in self.query.cmd_sites(72, "", {"lan"})["rows"]})
            self.assertNotIn("Netflix", {r["service"] for r in self.query.cmd_sites(72, "", {"opt1"})["rows"]})
            page = self.query.cmd_device(a, 72)
            self.assertEqual(page["networks"], [{"id": "lan", "name": "LAN"}])
            rows = page["traffic"] + [c for r in page["traffic"] for c in r["_children"]]
            self.assertEqual({tuple(r["networks"]) for r in rows}, {("lan",)})
        finally:
            self.query.Networks.__init__ = orig

    def test_newest_first_and_rebuilt_when_the_map_changes(self):
        self.summarize(budget=0)                # one day a call
        self.assertEqual(list(self.store.summary_state(0)), [self.today])
        self.summarize()
        built = self.store.summary_state(0)
        self.summarize()                        # nothing due: the same build times
        self.assertEqual(self.store.summary_state(0), built)
        self.daemon.map_fingerprint = "two"
        self.summarize(budget=0)
        self.assertEqual({d: fp for d, (fp, _) in self.store.summary_state(0).items() if fp == "two"}, {self.today: "two"})


class TestServiceChart(unittest.TestCase):
    def test_adds_up_to_the_list(self):
        """Named traffic, a CDN name and nameless traffic that take the service from the same server all count;
        nameless traffic its port gives another service (NTP) doesn't. On the VPN's server, the web traffic next to
        its port is the VPN's, as the list names it."""
        import query
        now = int(time.time())
        mac, ip, server, vpn = "02:00:00:00:00:72", "10.9.3.72", "192.0.2.31", "192.0.2.32"
        t = now - now % 300 - 1800
        flows = {(t, ip, server, 443, "tcp", "www.netflix.com"): [mac, "sni", 0, 10, 5000, 3, 1],
                 (t + 300, ip, server, 443, "tcp", "x.fastly.net"): [mac, "sni", 0, 10, 900, 3, 1],
                 (t + 600, ip, server, 443, "tcp", ""): [mac, "", 0, 10, 700, 3, 1],
                 (t + 600, ip, server, 123, "udp", ""): [mac, "", 0, 10, 50, 3, 1],     # NTP by its port: not Netflix
                 (t + 600, ip, vpn, 51820, "udp", ""): [mac, "", 0, 10, 300, 3, 1],
                 (t + 900, ip, vpn, 443, "tcp", ""): [mac, "", 0, 10, 100, 3, 1]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "loupe.db")
            s = store.Store(path)
            s.write(flows, {}, {mac: {"ip": ip, "ts": now}}, now)
            old = query.DB, query.CONF, query.load
            opened = []

            def load():
                opened.append(old[2]())
                return opened[-1]
            query.DB, query.CONF, query.load = path, os.path.join(d, "none.json"), load
            try:
                for hours in (3, 24):
                    for service, total in (("netflix", 6630), ("vpn (wireguard)", 420), ("ntp", 60)):
                        rows = query.cmd_lookup(service, hours, True)["traffic"]
                        listed = sum(r["down"] + r["up"] for r in rows)
                        chart = query.cmd_lookup(service, hours, True, chart=True)["timeline"]
                        self.assertEqual(listed, total, (service, hours))
                        self.assertEqual(sum(p[1] + p[2] for p in chart), listed, (service, hours))
            finally:
                query.DB, query.CONF, query.load = old
                for db, *_ in opened:
                    db.close()
                s.close()


class TestNamedByDay(unittest.TestCase):
    """Nameless traffic takes its service from named traffic to the same server that day, whatever the period."""

    def test_one_hour_uses_the_day(self):
        import query
        now = int(time.time())
        today = report.local_day(now)
        if now - today < 3 * 3600:
            self.skipTest("needs a few hours of today")
        mac = "02:00:00:00:00:71"
        early = today + 60
        late = now - now % 60 - 60
        flows = {(early, "10.9.3.71", "192.0.2.30", 443, "tcp", "www.netflix.com"): [mac, "sni", 0, 10, 5000, 3, 1],
                 (late, "10.9.3.71", "192.0.2.30", 443, "tcp", ""): [mac, "", 0, 10, 7000, 3, 1]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "loupe.db")
            s = store.Store(path)
            s.write(flows, {}, {mac: {"ip": "10.9.3.71", "ts": now}}, now)
            old = query.DB, query.CONF, query.load
            opened = []

            def load():
                opened.append(old[2]())
                return opened[-1]
            query.DB, query.CONF, query.load = path, os.path.join(d, "none.json"), load
            try:
                for summarized in (False, True):
                    if summarized:
                        daemon = type("D", (), {"store": s, "svc": services.ServiceMap()})()
                        daemon.map_fingerprint = report.map_fingerprint(daemon.svc)
                        import loupd
                        loupd.Loupe.summarize(daemon, now, 10.0)
                    for hours in (1, 24, 168):
                        row = next(r for r in query.cmd_devices(hours)["rows"] if r["mac"] == mac)
                        self.assertEqual(row["top"], ["Netflix"], (summarized, hours))
                    page = query.cmd_device(mac, 1)
                    self.assertEqual([(x["service"], x["down"]) for x in page["traffic"]], [("Netflix", 7000)])
                    self.assertTrue(page["traffic"][0]["_children"][0]["inferred"])
            finally:
                query.DB, query.CONF, query.load = old
                for db, *_ in opened:
                    db.close()
                s.close()
