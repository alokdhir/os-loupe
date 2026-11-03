import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "opnsense", "scripts", "loupe"))
from lib import hass  # noqa: E402


class TestHass(unittest.TestCase):
    def test_parse(self):
        text = json.dumps([{"mac": "AC:00:00:00:00:01", "name": "Office Fan", "area": "Office"},
                           {"mac": "ac-00-00-00-00-02", "name": "Deck Fan", "area": None},
                           {"mac": "ac:00:00:00:00:01", "name": "Again", "area": ""},       # the first one wins
                           {"mac": "not a mac", "name": "x"}, {"mac": "ac:00:00:00:00:03", "name": " "}])
        self.assertEqual(hass.parse(text), {"ac:00:00:00:00:01": {"name": "Office Fan", "area": "Office"},
                                            "ac:00:00:00:00:02": {"name": "Deck Fan", "area": ""}})

    def test_names_kept_across_failures_and_restarts(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "hass.json")
            n = hass.Names(path, "https://ha.example:8123", "token")
            got = {"ac:00:00:00:00:01": {"name": "Office Fan", "area": "Office"}}
            orig = hass.fetch
            try:
                hass.fetch = lambda *a, **k: got
                n.refresh(100)
                self.assertEqual(n.get("ac:00:00:00:00:01")["name"], "Office Fan")

                def down(*a, **k):
                    raise OSError("connection refused")
                hass.fetch = down
                st = n.refresh(200)
                self.assertEqual((st["error"], st["fetched"]), ("connection refused", 100))
                self.assertEqual(n.get("ac:00:00:00:00:01")["name"], "Office Fan")      # last good names stay
            finally:
                hass.fetch = orig
            self.assertEqual(hass.Names(path, "https://ha.example:8123", "token").get("ac:00:00:00:00:01")["area"], "Office")
            self.assertIsNone(hass.Names(path, "https://other.example", "token").get("ac:00:00:00:00:01"))   # another HA
            self.assertIsNone(hass.Names(path, "", "").get("ac:00:00:00:00:01"))                             # turned off


if __name__ == "__main__":
    unittest.main()
