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


if __name__ == "__main__":
    unittest.main()


class TestVendorWords(unittest.TestCase):
    def test_whole_words(self):
        oui = {"ACBBCC": "Shenzhen Example Intelligent Technology Co.", "DDEEFF": "TP-Link Systems Inc"}
        self.assertEqual(devid.classify("ac:bb:cc:00:00:01", None, {}, (), oui)[0], None)
        self.assertEqual(devid.classify("dd:ee:ff:00:00:01", None, {}, (), oui)[0], "TP-Link device")
        self.assertEqual(devid.classify("dd:ee:ff:00:00:02", "EP25", {}, (), oui)[0], "Smart plug")
