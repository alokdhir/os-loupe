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

"""The shipped rule files: well formed, and every rule's example classifies as that rule's type."""
import json
import re
import unittest

import helpers  # noqa: F401
from lib import devid, services

FIELDS = {"mac_prefix": str, "mdns_model": str, "hostname": str, "mdns_service": str, "dhcp_vendor": str,
          "dhcp_params": str, "talks_to": str, "vendor": str, "private_mac": bool}
RULE_KEYS = {"when", "all", "tier", "type", "vendor", "note", "example"}
EXAMPLE_KEYS = {"mac", "hostname", "vendor", "info", "names"}
REGEX_FIELDS = ("mdns_model", "hostname", "dhcp_vendor")


def run_example(ex):
    mac = ex.get("mac", "ac:00:00:00:00:01")
    oui = {mac.replace(":", "").upper()[:6]: ex["vendor"]} if ex.get("vendor") else {}
    return devid.classify(mac, ex.get("hostname"), ex.get("info"), ex.get("names", ()), oui)


class TestDeviceRules(unittest.TestCase):
    def setUp(self):
        with open(devid.RULES_JSON, encoding="utf-8") as f:
            self.data = json.load(f)

    def test_well_formed(self):
        for r in self.data["rules"]:
            with self.subTest(rule=r):
                self.assertLessEqual(set(r), RULE_KEYS)
                self.assertTrue(("when" in r) != ("all" in r), "exactly one of when/all")
                self.assertIsInstance(r["type"], str)
                self.assertIn(r["type"], self.data["types"], "every type needs an entry in 'types' (icon)")
                if "tier" in r:
                    self.assertIn(r["tier"], devid.TIERS)
                for cond in (r["all"] if "all" in r else [r["when"]]):
                    self.assertTrue(cond)
                    for field, want in cond.items():
                        self.assertIn(field, FIELDS)
                        for w in (want if isinstance(want, list) else [want]):
                            self.assertIsInstance(w, FIELDS[field])
                            if field in REGEX_FIELDS:
                                re.compile(w)
                self.assertIn("example", r, "every rule carries synthetic evidence")
                self.assertLessEqual(set(r["example"]), EXAMPLE_KEYS)

    def test_types(self):
        for t, v in self.data["types"].items():
            with self.subTest(type=t):
                self.assertLessEqual(set(v), {"icon", "vendor"})
                self.assertRegex(v.get("icon", ""), r"^(fa-(brands|solid|regular) fa-[a-z0-9-]+)?$")

    def test_examples(self):
        """Each example lands on its own rule's type: catches typos and rules that steal others' devices."""
        for r in self.data["rules"]:
            with self.subTest(rule=r):
                self.assertEqual(run_example(r["example"])[0], r["type"])

    def test_apple_models(self):
        for ident, name in self.data["apple_models"].items():
            self.assertRegex(ident, r"^[A-Za-z]+\d+,\d+$")
            self.assertTrue(name)


class TestServiceData(unittest.TestCase):
    def test_well_formed(self):
        with open(services.DATA_JSON, encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data["suffixes"].items():
            self.assertEqual(k, k.lower().strip("."), k)
            self.assertTrue(v)
        services.ServiceMap()                     # ranges parse
        for proto, port, name in data["ports"]:
            self.assertIn(proto, ("tcp", "udp"))
            self.assertTrue(0 < port < 65536 and name)
        for k, v in data["vpn_providers"].items():
            self.assertEqual(k, k.lower())


if __name__ == "__main__":
    unittest.main()
