import unittest

import helpers  # noqa: F401
from lib import services


class TestServices(unittest.TestCase):
    def setUp(self):
        self.m = services.ServiceMap({"example.org": "Example", "youtube.com": "Tube"})

    def test_longest_suffix(self):
        self.assertEqual(self.m.by_name("rr3---sn-abc.googlevideo.com"), "YouTube")
        self.assertEqual(self.m.by_name("mask.icloud.com"), "iCloud Private Relay")
        self.assertEqual(self.m.by_name("p42-contacts.icloud.com"), "iCloud")
        self.assertEqual(self.m.by_name("notgoogle.com"), None)

    def test_user_overrides(self):
        self.assertEqual(self.m.by_name("a.example.org"), "Example")
        self.assertEqual(self.m.by_name("www.youtube.com"), "Tube")

    def test_fallbacks(self):
        self.assertEqual(self.m.service("", "17.250.96.102", 443, "udp"), "iCloud Private Relay")
        self.assertEqual(self.m.service("", "17.57.147.7", 5223, "tcp"), "Apple")
        self.assertEqual(self.m.service("", "203.0.113.9", 51820, "udp"), "WireGuard")
        self.assertEqual(self.m.service("", "203.0.113.9", 9999, "tcp"), None)


if __name__ == "__main__":
    unittest.main()
