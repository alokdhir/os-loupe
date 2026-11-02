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

"""The AI Rule Generator: the table, the reply parser and the preview (synthetic devices only)."""
import unittest

import helpers  # noqa: F401
from lib import airules, devid

RULE = '{"devices": ["D1"], "when": {"vendor": "Espressif", "talks_to": "acme-garage.example"}, "type": "Garage door opener"}'


class TestParse(unittest.TestCase):
    def test_code_block_and_prose(self):
        rules, unknown, problem = airules.parse('Here you go:\n```json\n{"rules": [' + RULE + '], "unknown": []}\n```\nCheers')
        self.assertEqual((len(rules), problem), (1, None))
        rules, _, problem = airules.parse('{"rules": [' + RULE + ']} and some words after')
        self.assertEqual((len(rules), problem), (1, None))

    def test_invalid_escapes(self):
        """AIs write regexes with \\$ inside JSON strings, which JSON doesn't allow."""
        rules, _, problem = airules.parse(r'{"rules": [{"devices": ["D1"], "when": {"hostname": "^Scale\$"}, "type": "Scale"}]}')
        self.assertIsNone(problem)
        self.assertEqual(rules[0]["when"]["hostname"], "^Scale$")
        mixed = r'{"rules": [{"devices": ["D1"], "when": {"hostname": "^gs_\\d+\$", "mdns_name": "^acme \d"}, "type": "X"}]}'
        when = airules.parse(mixed)[0][0]["when"]
        self.assertEqual((when["hostname"], when["mdns_name"]), (r"^gs_\d+$", r"^acme \d"))  # escaped right, kept; \$ and bare \d fixed

    def test_cut_off(self):
        text = '```json\n{"rules": [' + RULE + ', {"devices": ["D2"], "when": {"hostname": "^Acme"}, "type": "Ov'
        rules, unknown, problem = airules.parse(text)
        self.assertEqual(len(rules), 1)
        self.assertIn("incomplete", problem)

    def test_nothing(self):
        self.assertEqual(airules.parse("sorry, I can't help")[2], "no JSON in the reply")


class TestToRule(unittest.TestCase):
    def test_lists(self):
        rule, fields, problem = airules.to_rule({"when": {"hostname": ["^acme-", "^ac-"], "talks_to": ["a.example", "b.example"]},
                                                 "type": "Oven", "vendor": "Acme"})
        self.assertIsNone(problem)
        self.assertEqual(fields, {"hostname": "(?:^acme-)|(?:^ac-)", "talks_to": "a.example, b.example", "type": "Oven"})
        self.assertEqual((rule["source"], rule["vendor"]), ("ai", "Acme"))

    def test_refused(self):
        self.assertIn("dhcp_params", airules.to_rule({"when": {"dhcp_params": "1,3"}, "type": "X"})[2])
        self.assertIn("comma", airules.to_rule({"when": {"vendor": ["Acme, Inc"]}, "type": "X"})[2])
        self.assertIn("regular expression", airules.to_rule({"when": {"hostname": "("}, "type": "X"})[2])
        self.assertIsNotNone(airules.to_rule({"when": {}, "type": "X"})[2])


class TestTable(unittest.TestCase):
    def test_evidence(self):
        ev = airules.evidence({"vendor": "Espressif Inc.", "type": "IoT device (ESP)", "type_source": "vendor Espressif Inc.",
                               "hostname": "Sam's-Box", "info": {"mdns_names": ["sam's clock"], "mdns_hosts": ["clock.local"]},
                               "names": ["a.acme.example", "b.acme.example", "time.apple.com", "x.cloudfront.net"]})
        self.assertEqual(ev["hostname"], "[name]'s-Box")
        self.assertEqual(ev["bonjour_names"], ["[name]'s clock"])
        self.assertEqual(ev["domains"], ["acme.example"])               # platforms and CDNs removed

    def test_relay_says_nothing(self):
        info = {"mdns_hosts": [f"h{i}.local" for i in range(8)], "mdns_names": ["front door"]}
        ev = airules.evidence({"info": info})
        self.assertEqual((ev["bonjour_hosts"], ev["bonjour_names"]), ([], []))

    def test_rows_group_identical(self):
        rows = airules.rows([("ac:00:00:00:00:01", {"a": 1}), ("ac:00:00:00:00:02", {"a": 2}), ("ac:00:00:00:00:03", {"a": 1})])
        self.assertEqual([(r[0], r[1]) for r in rows], [("D1", ["ac:00:00:00:00:01", "ac:00:00:00:00:03"]),
                                                       ("D2", ["ac:00:00:00:00:02"])])
        text = airules.prompt(airules.rows([("ac:00:00:00:00:01", airules.evidence({"vendor": "Acme"}))]), {"Oven": {}})
        self.assertIn("| D1 | 1 | Acme |", text)
        self.assertNotIn("ac:00:00:00:00:01", text)                    # no MACs in what leaves the router

    def test_needs_help(self):
        self.assertTrue(airules.needs_help(""))
        self.assertTrue(airules.needs_help("IoT device (ESP)"))
        self.assertFalse(airules.needs_help("Thermostat"))


class TestPreview(unittest.TestCase):
    OUI = {"AC0000": "Espressif Inc.", "AC0001": "Acme Corp."}
    # (mac, host name, info, names): two garage controllers, a server that uses the garage cloud, a thermostat
    DEVICES = [("ac:00:00:00:00:01", "espressif", {}, ["api.acme-garage.example"]),
               ("ac:00:00:00:00:02", "espressif", {}, ["api.acme-garage.example"]),
               ("02:00:00:00:00:03", "server", {"mdns_services": ["_ssh._tcp"]}, ["api.acme-garage.example"]),
               ("ac:00:01:00:00:04", "acme-tstat", {}, [])]

    def setUp(self):
        self.oui = {mac.replace(":", "").upper()[:6]: v for mac, *_ in self.DEVICES for k, v in self.OUI.items()
                    if mac.replace(":", "").upper().startswith(k)}

    def check(self, *rules):
        return airules.preview(list(rules), self.DEVICES, {"D1": ["ac:00:00:00:00:01", "ac:00:00:00:00:02"],
                                                           "D2": ["ac:00:01:00:00:04"]}, [], self.oui)

    def test_good_rule(self):
        r, = self.check({"devices": ["D1"], "when": {"vendor": "Espressif", "talks_to": "acme-garage.example"},
                         "type": "Garage door opener"})
        self.assertEqual(r["changes"], [["IoT device (ESP)", "Garage door opener", 2]])
        self.assertEqual((r["spills"], r["pick"]), (0, True))

    def test_bare_domain_skips_the_server(self):
        """An AI rule on a domain alone: the server using that cloud (a private MAC) isn't matched."""
        r, = self.check({"devices": ["D1"], "when": {"talks_to": "acme-garage.example"}, "type": "Garage door opener"})
        self.assertEqual((r["spills"], r["pick"]), (0, True))

    def test_flags(self):
        spill, nothing, vaguer, bad = self.check(
            {"devices": ["D2"], "when": {"vendor": "Espressif"}, "type": "Thermostat"},       # meant for D2, hits D1
            {"devices": ["D1"], "when": {"hostname": "^nothing-here"}, "type": "Garage door opener"},
            {"devices": ["D2"], "when": {"hostname": "^acme-tstat"}, "type": "Smart home device"},
            {"devices": ["D1"], "when": {"dhcp_params": "1,3"}, "type": "X"})
        self.assertEqual((spill["spills"], spill["pick"]), (2, False))
        self.assertEqual((nothing["nothing"], nothing["pick"]), (True, False))
        self.assertFalse(vaguer["vaguer"])          # no type before: a generic type isn't vaguer
        self.assertIn("dhcp_params", bad["invalid"])
        self.assertFalse(bad["pick"])
        devid.set_user_rules([{"when": {"hostname": "^acme-tstat"}, "type": "Thermostat"}])
        try:
            r, = airules.preview([{"devices": ["D2"], "when": {"hostname": "^acme-tstat"}, "type": "Smart home device"}],
                                 self.DEVICES, {"D2": ["ac:00:01:00:00:04"]},
                                 [{"when": {"hostname": "^acme-tstat"}, "type": "Thermostat"}], self.oui)
        finally:
            devid.set_user_rules([])
        self.assertEqual((r["vaguer"], r["pick"]), (True, False))      # Thermostat -> Smart home device


if __name__ == "__main__":
    unittest.main()
