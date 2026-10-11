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
from lib import services, store


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
                    {(0, "10.9.1.30", "api.nordvpn.com", "dns"): [1, b, b]}, {})
            s.close()
            db = sqlite3.connect(path)
            try:
                lab = query.Labels(db, services.ServiceMap(), "flows_5m", b)
                self.assertEqual(lab.service("10.9.1.30", "", "203.0.113.50", 51820, "udp"), "VPN (NordVPN)")
                self.assertEqual(lab.service("10.9.1.30", "", "203.0.113.50", 993, "tcp"), "VPN (NordVPN)")
                self.assertEqual(lab.service("10.9.1.30", "", "203.0.113.70", 993, "tcp"), "Email (IMAP)")
                self.assertEqual(lab.service("10.9.1.31", "", "203.0.113.60", 51820, "udp"), "VPN (WireGuard)")
                self.assertEqual(lab.service("10.9.1.30", "www.youtube.com", "203.0.113.50", 443, "tcp"), "YouTube")
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
