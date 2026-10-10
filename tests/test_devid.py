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

import unittest

import helpers  # noqa: F401
from lib import devid

OUI = {"AABBCC": "Sony Interactive Entertainment Inc.", "DDEEFF": "Apple, Inc.", "112233": "Tuya Smart Inc."}


class TestDevid(unittest.TestCase):
    def test_mdns_model_wins(self):
        t = devid.classify("dd:ee:ff:00:00:01", "Family-Room", {"mdns_models": ["model=AppleTV14,1"]}, (), OUI)
        self.assertEqual(t, ("Apple TV", "mdns model AppleTV14,1", "Apple, Inc."))

    def test_hostname(self):
        self.assertEqual(devid.classify("dd:ee:ff:00:00:02", "Sams-iPhone", {}, (), OUI)[0], "iPhone")

    def test_traffic_hint(self):
        t = devid.classify("aa:bb:cc:00:00:01", None, {}, ["auth.np.ac.playstation.net"], OUI)
        self.assertEqual(t[:2], ("PlayStation", "talks to playstation.net"))

    def test_dhcp(self):
        self.assertEqual(devid.classify("00:00:00:00:00:01", None, {"dhcp_vendor_class": "MSFT 5.0"}, (), OUI)[0],
                         "Windows PC")
        apple = {"dhcp_params": [1, 121, 3, 6, 15, 108, 114, 119, 252, 95, 44, 46]}
        self.assertEqual(devid.classify("06:00:00:00:00:01", None, apple, (), OUI)[0], "iPhone/iPad/Mac")

    def test_vendor_and_private(self):
        self.assertEqual(devid.classify("11:22:33:00:00:01", None, {}, (), OUI)[:2],
                         ("Smart home device", "vendor Tuya Smart Inc."))
        self.assertEqual(devid.classify("02:00:00:00:00:01", None, {}, (), OUI)[:2], ("Phone/tablet/laptop", "private MAC"))
        self.assertEqual(devid.classify("52:54:00:00:00:01", None, {}, (), OUI)[0], "Virtual machine")

    def test_vendor_lookup_lengths(self):
        self.assertEqual(devid.vendor("aa:bb:cc:12:34:56", {"AABBCC1": "Small Co"}), "Small Co")


    def test_homekit_host_is_server(self):
        info = {"mdns_services": ["_hap._tcp", "_smb._tcp"], "mdns_models": ["md=homebridge", "model=MacSamba"]}
        self.assertEqual(devid.classify("02:00:00:00:00:10", info=info)[:2], ("Server", "mdns model homebridge"))
        info = {"mdns_services": ["_hap._tcp", "_channels_dvr._tcp"]}
        self.assertEqual(devid.classify("ac:00:00:00:00:11", info=info)[:2],
                         ("Server", "mdns _hap._tcp + mdns _channels_dvr._tcp"))
        info = {"mdns_services": ["_hap._tcp"], "mdns_models": ["md=Smart Plug"]}
        self.assertEqual(devid.classify("ac:00:00:00:00:12", info=info)[0], "HomeKit accessory")


    def test_ip_kvm(self):
        info = {"dhcp_vendor_class": "udhcp 1.37.0", "mdns_hosts": ["kvm-0a1b.local"]}
        self.assertEqual(devid.classify("ac:00:00:00:0a:1b", info=info)[:2], ("NanoKVM", "hostname kvm-0a1b.local"))
        self.assertEqual(devid.classify("ac:00:00:00:00:13", hostname="pikvm")[0], "PiKVM")
        self.assertNotEqual(devid.classify("ac:00:00:00:00:14", hostname="kvm-host-1")[0], "NanoKVM")


    def test_app_traffic_does_not_beat_fingerprint(self):
        info = {"dhcp_params": "1,121,3,6,15,108,114,119,162,252"}
        self.assertEqual(devid.classify("02:00:00:00:00:20", info=info, names=["a1.tuyaus.com"])[0], "iPhone/iPad/Mac")

    def test_mdns_name_skips_ids_and_relayed(self):
        info = {"mdns_hosts": ["0a1b2c3d4e5f6a7b.local", "lock-plus.local", "den.local"],
                "mdns_names": ["0a1b2c3d4e5f6a7b", "lock plus", "70-35-60-63.1 den", "den"]}
        self.assertEqual(devid.mdns_name(info), "den")
        self.assertEqual(devid.mdns_name({"mdns_hosts": ["0a1b2c3d4e5f.local", "box.local"]}), "box")


    def test_placeholder_names(self):
        self.assertEqual(devid.display_name("Mac", "Mac", "studio"), "studio")
        self.assertEqual(devid.display_name("", "iPhone", None), "iPhone")          # nothing better
        self.assertEqual(devid.display_name("office-pc", "x", "y"), "office-pc")


class TestUserRules(unittest.TestCase):
    def tearDown(self):
        devid.set_user_rules([])

    def test_user_rule_wins_over_builtin(self):
        oui = {"AC0000": "Tuya Smart Inc."}
        before = devid.classify("ac:00:00:00:00:01", names=["a1.tuyaus.com"], oui=oui)
        self.assertEqual(before[0], "Smart home device")                 # built-in: talks to tuyaus.com
        self.assertEqual(devid.set_user_rules([{"when": {"vendor": "Tuya"}, "type": "Window shade"}]), [])
        t, src, ven = devid.classify("ac:00:00:00:00:01", names=["a1.tuyaus.com"], oui=oui)
        self.assertEqual((t, src, ven), ("Window shade", "custom rule: vendor Tuya Smart Inc.", "Tuya Smart Inc."))

    def test_comma_list(self):
        devid.set_user_rules([{"when": {"mac_prefix": "ac:00:00:01, ac:00:00:02"}, "type": "Window shade"},
                              {"when": {"hostname": "^a,b$"}, "type": "Comma host"}])       # regex: not split
        self.assertEqual(devid.classify("ac:00:00:02:00:09")[0], "Window shade")
        self.assertNotEqual(devid.classify("ac:00:00:03:00:09")[0], "Window shade")
        self.assertEqual(devid.classify("ac:00:00:04:00:09", hostname="a,b")[0], "Comma host")

    def test_bad_rules_skipped(self):
        bad = devid.set_user_rules([{"when": {"hostname": "(unclosed"}, "type": "X"},
                                    {"when": {"nonsense": "x"}, "type": "Y"},
                                    {"when": {"hostname": "^ok-"}, "type": ""},
                                    {"when": {"hostname": "^ok-"}, "type": "Good"}])
        self.assertEqual([p.split(":")[0] for _, p in bad], ["bad regular expression", "unknown field nonsense", "no type"])
        self.assertEqual(devid.classify("ac:00:00:00:00:02", hostname="ok-1")[0], "Good")


if __name__ == "__main__":
    unittest.main()


class TestVendorWords(unittest.TestCase):
    def test_whole_words(self):
        oui = {"ACBBCC": "Shenzhen Example Intelligent Technology Co.", "DDEEFF": "TP-Link Systems Inc"}
        self.assertEqual(devid.classify("ac:bb:cc:00:00:01", None, {}, (), oui)[0], None)
        self.assertEqual(devid.classify("dd:ee:ff:00:00:01", None, {}, (), oui)[0], "TP-Link device")
        self.assertEqual(devid.classify("dd:ee:ff:00:00:02", "EP25", {}, (), oui)[0], "Smart plug")


class TestModelAndName(unittest.TestCase):
    def test_model(self):
        self.assertEqual(devid.model({"mdns_models": ["model=Mac15,13"]}), "MacBook Air 15″ (M3)")
        self.assertIsNone(devid.model({"mdns_models": ["model=Mac99,1"]}))

    def test_mdns_name(self):
        self.assertEqual(devid.mdns_name({"mdns_hosts": ["box.local"], "mdns_names": ["Other"]}), "box")
        self.assertEqual(devid.mdns_name({"mdns_names": ["Living Room"]}), "Living Room")
        self.assertIsNone(devid.mdns_name({}))
