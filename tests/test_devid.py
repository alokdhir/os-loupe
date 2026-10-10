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
        self.assertEqual(devid.classify("02:00:00:00:00:10", info=info)[:2], ("Server", "mdns homebridge"))
        info = {"mdns_services": ["_hap._tcp", "_channels_dvr._tcp"]}
        self.assertEqual(devid.classify("ac:00:00:00:00:11", info=info)[:2],
                         ("Server", "mdns _hap._tcp + _channels_dvr._tcp"))
        info = {"mdns_services": ["_hap._tcp"], "mdns_models": ["md=Smart Plug"]}
        self.assertEqual(devid.classify("ac:00:00:00:00:12", info=info)[0], "HomeKit accessory")


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
