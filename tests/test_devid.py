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

import struct
import unittest

import helpers  # noqa: F401
from lib import devid, mdns

OUI = {"AABBCC": "Sony Interactive Entertainment Inc.", "DDEEFF": "Apple, Inc.", "112233": "Tuya Smart Inc."}


class TestDevid(unittest.TestCase):
    def test_icons(self):
        r = devid.Rules({"types": {"AV receiver": {"icon": "fa-solid fa-volume-high"}}},
                        [{"when": {"vendor": "Tuya"}, "type": "Window shade", "icon": "fa-solid fa-table-columns"}])
        self.assertEqual(r.icon("AV receiver"), "fa-solid fa-volume-high")
        self.assertEqual(r.icon("A/V Receiver"), "fa-solid fa-volume-high")       # typed differently
        self.assertEqual(r.icon("Window shade"), "fa-solid fa-table-columns")     # from the user's rule
        self.assertEqual(r.icon("Dishwasher"), "")
        self.assertEqual(r.icon(None), "")

    def test_mdns_model_wins(self):
        t = devid.classify("dd:ee:ff:00:00:01", "Family-Room", {"mdns_models": ["model=AppleTV14,1"]}, (), OUI)
        self.assertEqual(t, ("Apple TV", "mdns model AppleTV14,1", "Apple, Inc."))

    def test_hostname(self):
        self.assertEqual(devid.classify("dd:ee:ff:00:00:02", "Sams-iPhone", {}, (), OUI)[0], "iPhone")

    def test_traffic_hint(self):
        t = devid.classify("ac:00:00:00:00:01", None, {}, ["auth.np.ac.playstation.net"], OUI)
        self.assertEqual(t[:2], ("PlayStation", "talks to playstation.net"))

    def test_traffic_hint_not_for_computers(self):
        # a laptop or phone that opened a vendor's website is not that vendor's device
        laptop = devid.classify("02:00:00:00:00:01", None, {}, ["www.roku.com", "www.ring.com"], OUI)
        self.assertEqual(laptop[:2], ("Phone/tablet/laptop", "private MAC"))
        self.assertNotEqual(devid.classify("dd:ee:ff:00:00:01", None, {}, ["www.ring.com"], OUI)[0], "Ring device")
        # the user's own talks_to rules still apply to any device
        mine = devid.Rules({"rules": [], "computer_vendors": ["Apple"]},
                           [{"when": {"talks_to": "vpn.example.com"}, "type": "Work laptop"}])
        self.assertEqual(devid.classify("02:00:00:00:00:01", None, {}, ["vpn.example.com"], OUI, mine)[0], "Work laptop")

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


    def test_generic_fingerprint_is_last_resort(self):
        info = {"dhcp_vendor_class": "udhcp 1.31.1", "dhcp_params": "1,3,6,12,15,28,42"}
        self.assertEqual(devid.classify("ac:00:00:00:00:21", info=info, names=["p2p.reolink.com"])[:2],
                         ("Camera", "talks to reolink.com"))
        self.assertEqual(devid.classify("02:00:00:00:00:22", info=info)[0], "Embedded Linux device")
        self.assertEqual(devid.classify("02:00:00:00:00:23", hostname="NVR", info=info, names=["p2p.reolink.com"])[0], "NVR")
        info = {"dhcp_vendor_class": "dhcpcd-10.0.6:Linux-6.6:aarch64"}       # a Linux box running a camera's app stays Linux
        self.assertEqual(devid.classify("02:00:00:00:00:24", info=info, names=["p2p.reolink.com"])[0], "Linux device")

    def test_app_traffic_does_not_beat_fingerprint(self):
        info = {"dhcp_params": "1,121,3,6,15,108,114,119,162,252"}
        self.assertEqual(devid.classify("02:00:00:00:00:20", info=info, names=["a1.tuyaus.com"])[0], "iPhone/iPad/Mac")

    def test_mdns_name_skips_ids_and_relayed(self):
        info = {"mdns_hosts": ["0a1b2c3d4e5f6a7b.local", "lock-plus.local", "den.local"],
                "mdns_names": ["0a1b2c3d4e5f6a7b", "lock plus", "70-35-60-63.1 den", "den"]}
        self.assertEqual(devid.mdns_name(info), "den")
        self.assertEqual(devid.mdns_name({"mdns_hosts": ["0a1b2c3d4e5f.local", "box.local"]}), "box")
        hap = {"mdns_hosts": ["0a1b2c3d4e5f60718293a4b5c6d7e8f9-hap.local", "0a1b2c3d4e5f60718293a4b5c6d7e8f9.local", "desk.local"]}
        self.assertEqual(devid.mdns_name(hap), "desk")                   # long IDs and HomeKit's -hap names too
        self.assertEqual(devid.mdns_name({"mdns_hosts": ["front-yard.local"]}), "front-yard")   # a real name stays


    def test_placeholder_names(self):
        self.assertEqual(devid.display_name("Mac", "Mac", "studio"), "studio")
        self.assertEqual(devid.display_name("", "iPhone", None), "iPhone")          # nothing better
        self.assertEqual(devid.display_name("office-pc", "x", "y"), "office-pc")


def mdns_response(*records):
    """A synthetic mDNS response; records are (name, type, rdata bytes)."""
    def name(n):
        return b"".join(bytes([len(p)]) + p.encode() for p in n.split(".")) + b"\0"
    out = struct.pack("!6H", 0, 0x8400, 0, len(records), 0, 0)
    for n, rtype, rd in records:
        out += name(n) + struct.pack("!HHIH", rtype, 0x8001, 120, len(rd)) + rd
    return out


def txt(**kv):
    return b"".join(bytes([len(e)]) + e for e in (f"{k}={v}".encode() for k, v in kv.items()))


class TestCategories(unittest.TestCase):
    def tearDown(self):
        devid.set_user_rules([])

    def test_codes(self):
        self.assertEqual(devid.category_code("HAP:09"), "hap:9")
        self.assertEqual(devid.category_code("matter:769"), "matter:0x0301")
        self.assertEqual(devid.category_code("matter:0x301"), "matter:0x0301")
        self.assertIsNone(devid.category_code("zigbee:1"))
        self.assertIsNone(devid.category_code("hap:x"))

    def test_homekit_category(self):
        t = devid.classify("ac:00:00:00:00:01", None, {"mdns_categories": ["hap:9"], "mdns_services": ["_hap._tcp"]})
        self.assertEqual(t[:2], ("Thermostat", "HomeKit category 9"))

    def test_matter_subtype(self):
        info = {"mdns_services": ["_matterc._udp", "_t123._sub._matterc._udp"]}
        self.assertEqual(devid.classify("ac:00:00:00:00:01", None, info)[:2], ("Oven", "Matter device type 0x007b"))

    def test_bridge_categories_ignored(self):
        """A bridge announcing what it bridges (several HomeKit categories) is none of them."""
        info = {"mdns_categories": ["hap:9", "hap:5"], "mdns_services": ["_hap._tcp"]}
        self.assertEqual(devid.classify("ac:00:00:00:00:01", None, info)[0], "HomeKit accessory")

    def test_specific_service_beats_category(self):
        info = {"mdns_categories": ["hap:26"], "mdns_services": ["_sonos._tcp", "_hap._tcp"]}
        self.assertEqual(devid.classify("ac:00:00:00:00:01", None, info)[0], "Sonos speaker")

    def test_user_rule(self):
        self.assertEqual(devid.set_user_rules([{"when": {"category": "matter:0x0075, hap:20"}, "type": "Kitchen"}]), [])
        info = {"mdns_categories": ["matter:117"]}
        self.assertEqual(devid.classify("ac:00:00:00:00:01", None, info)[:2], ("Kitchen", "custom rule: Matter device type 0x0075"))
        bad = devid.set_user_rules([{"when": {"category": "oven"}, "type": "X"}])
        self.assertEqual(bad[0][1], "not a category code: oven")

    def test_parse(self):
        msg = mdns_response(("_hap._tcp.local", 12, b""),
                            ("Hall Thermostat._hap._tcp.local", 16, txt(ci=9, md="T1", sf=0)),
                            ("ABCD._matterc._udp.local", 16, txt(DT=123, VP="65521+32769")),
                            ("Other._http._tcp.local", 16, txt(ci=5)),
                            ("Door Lock._hap._udp.local", 16, txt(ci=6)))     # relayed from Thread: not this device
        m = mdns.parse(msg)
        self.assertEqual(m["categories"], {"hap:9", "matter:123"})
        self.assertEqual(devid.categories({"mdns_categories": sorted(m["categories"])}), {"hap:9", "matter:0x007b"})


class TestReplacedInterfaces(unittest.TestCase):
    """One device on two interfaces used at different times: linked by its own name and related MACs."""

    def pairs(self, devices, together=()):
        days = {m: {1, 2, 3} for m in devices}
        for m in together:
            days[m] = {1, 2, 3, 4, 5}
        return [(a, b) for a, b, _ in devid.replaced_interfaces(devices, days.get)]

    def test_adjacent_and_twin(self):
        self.assertEqual(self.pairs({"ac:00:00:00:00:10": {"name": "Den"}, "ac:00:00:00:00:12": {"name": "Den"}}),
                         [])                                        # active on the same days: two devices
        days = {"ac:00:00:00:00:10": {1, 2}, "ac:00:00:00:00:12": {5, 6}}.get
        self.assertEqual(len(devid.replaced_interfaces({"ac:00:00:00:00:10": {"name": "Den"},
                                                        "ac:00:00:00:00:12": {"name": "Den"}}, days)), 1)
        days = {"ac:00:00:00:00:10": {1}, "ae:00:00:00:00:11": {2}}.get
        reason = devid.replaced_interfaces({"ac:00:00:00:00:10": {"hosts": ["den.local"]},
                                            "ae:00:00:00:00:11": {"hosts": ["den.local"]}}, days)[0][2]
        self.assertIn("locally administered twin", reason)

    def test_private_needs_same_type(self):
        days = {"ac:00:00:00:00:10": {1}, "02:00:00:00:00:20": {2}, "06:00:00:00:00:30": {3}}.get
        devs = {"ac:00:00:00:00:10": {"name": "laptop", "type": "Mac"}, "02:00:00:00:00:20": {"name": "laptop", "type": "Mac"}}
        self.assertEqual(len(devid.replaced_interfaces(devs, days)), 1)
        devs["02:00:00:00:00:20"]["type"] = "iPhone"
        self.assertEqual(devid.replaced_interfaces(devs, days), [])

    def test_shared_and_relayed_names(self):
        days = {"ac:00:00:00:00:01": {1}, "ac:00:00:00:00:02": {2}, "ac:00:00:00:00:03": {3}}.get
        three = {m: {"name": "eero"} for m in ("ac:00:00:00:00:01", "ac:00:00:00:00:02", "ac:00:00:00:00:03")}
        self.assertEqual(devid.replaced_interfaces(three, days), [])        # a default name: three devices
        relay = {"ac:00:00:00:00:01": {"name": "Den"}, "ac:00:00:00:00:02": {"name": "Den"},
                 "02:00:00:00:00:09": {"hosts": [f"h{i}.local" for i in range(8)] + ["den.local"]}}
        days = {"ac:00:00:00:00:01": {1}, "ac:00:00:00:00:02": {2}, "02:00:00:00:00:09": {3}}.get
        self.assertEqual(len(devid.replaced_interfaces(relay, days)), 1)    # the relay's copy of the name doesn't count
        self.assertEqual(devid.replaced_interfaces({"ac:00:00:00:00:01": {"name": "Mac"},
                                                    "ac:00:00:00:00:02": {"name": "Mac"}}, days), [])   # placeholder


class TestMdnsName(unittest.TestCase):
    def tearDown(self):
        devid.set_user_rules([])

    def test_rule_on_instance_name(self):
        devid.set_user_rules([{"when": {"mdns_name": "(?i)^acme x1"}, "type": "Oven"}])
        info = {"mdns_names": ["acme x100", "0a1b2c3d4e5f60718293"], "mdns_services": ["_acme._tcp"]}
        self.assertEqual(devid.classify("ac:00:00:00:00:01", None, info)[:2], ("Oven", "custom rule: mdns name acme x100"))

    def test_ids_are_not_names(self):
        devid.set_user_rules([{"when": {"mdns_name": "^[0-9a-f]+$"}, "type": "Oven"}])
        self.assertIsNone(devid.classify("ac:00:00:00:00:01", None, {"mdns_names": ["0a1b2c3d4e5f60718293"]})[0])

    def test_bad_regex_refused(self):
        self.assertIn("bad regular expression", devid.set_user_rules([{"when": {"mdns_name": "("}, "type": "X"}])[0][1])


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
        devid.set_user_rules([{"when": {"vendor": "Tuya"}, "type": "Window shade", "source": "ai"}])    # the AI flow's
        self.assertEqual(devid.classify("ac:00:00:00:00:01", oui=oui)[1], "AI rule: vendor Tuya Smart Inc.")

    def test_comma_list(self):
        devid.set_user_rules([{"when": {"mac_prefix": "ac:00:00:01, ac:00:00:02"}, "type": "Window shade"},
                              {"when": {"hostname": "^a,b$"}, "type": "Comma host"}])       # regex: not split
        self.assertEqual(devid.classify("ac:00:00:02:00:09")[0], "Window shade")
        self.assertNotEqual(devid.classify("ac:00:00:03:00:09")[0], "Window shade")
        self.assertEqual(devid.classify("ac:00:00:04:00:09", hostname="a,b")[0], "Comma host")

    def test_compound_rule(self):
        oui = {"AC0000": "Espressif Inc."}
        devid.set_user_rules([{"when": {"vendor": "Espressif", "hostname": "^shelly"}, "type": "Smart relay"}])
        t, src, _ = devid.classify("ac:00:00:00:00:03", hostname="shelly-1", oui=oui)
        self.assertEqual((t, src), ("Smart relay", "custom rule: vendor Espressif Inc. + hostname shelly-1"))
        self.assertNotEqual(devid.classify("ac:00:00:00:00:03", hostname="plug-1", oui=oui)[0], "Smart relay")
        self.assertNotEqual(devid.classify("ac:00:00:00:00:03", hostname="shelly-1")[0], "Smart relay")

    def test_compound_rule_tried_with_its_strongest_clue(self):
        # the vendor+hostname rule is a host name rule, so it beats a plain vendor rule listed first
        oui = {"AC0000": "Espressif Inc."}
        devid.set_user_rules([{"when": {"vendor": "Espressif"}, "type": "Espressif gadget"},
                              {"when": {"vendor": "Espressif", "hostname": "^shelly"}, "type": "Smart relay"}])
        self.assertEqual(devid.classify("ac:00:00:00:00:03", hostname="shelly-1", oui=oui)[0], "Smart relay")
        self.assertEqual(devid.classify("ac:00:00:00:00:03", hostname="esp-1", oui=oui)[0], "Espressif gadget")

    def test_private_mac_clue(self):
        devid.set_user_rules([{"when": {"hostname": "^tab-", "private_mac": True}, "type": "Tablet"}])
        self.assertEqual(devid.classify("02:00:00:00:00:04", hostname="tab-1")[0], "Tablet")
        self.assertNotEqual(devid.classify("ac:00:00:00:00:04", hostname="tab-1")[0], "Tablet")

    def test_bad_rules_skipped(self):
        bad = devid.set_user_rules([{"when": {"hostname": "(unclosed"}, "type": "X"},
                                    {"when": {"nonsense": "x"}, "type": "Y"},
                                    {"when": {"hostname": "^ok-"}, "type": ""},
                                    {"when": {}, "type": "Empty"},
                                    {"when": {"hostname": "^ok-"}, "type": "Good"}])
        self.assertEqual([p.split(":")[0] for _, p in bad], ["bad regular expression", "unknown field nonsense", "no type",
                                                          "no clues"])
        self.assertEqual(devid.classify("ac:00:00:00:00:02", hostname="ok-1")[0], "Good")


KEA_CSV = """address,hwaddr,client_id,valid_lifetime,expire,subnet_id,fqdn_fwd,fqdn_rev,hostname,state,user_context,pool_id
10.9.1.20,ac:00:00:00:00:01,,3600,1000,1,0,0,old-name.lan,0,,0
10.9.1.20,AC:00:00:00:00:01,,3600,2000,1,0,0,laptop.lan,0,,0
10.9.1.21,ac:00:00:00:00:02,,3600,2000,1,0,0,gone,2,,0
10.9.1.22,ac:00:00:00:00:03,,3600,2000,1,0,0,,0,,0
"""
ISC_LEASES = """# The format of this file is documented in the dhcpd.leases(5) manual page.
lease 10.9.1.30 {
  starts 4 2026/10/08 12:00:00;
  binding state active;
  next binding state free;
  rewind binding state free;
  hardware ethernet ac:00:00:00:00:04;
  client-hostname "printer";
}
lease 10.9.1.31 {
  binding state free;
  hardware ethernet ac:00:00:00:00:05;
  client-hostname "left";
}
"""
KEA_CONF = """// Kea dhcp4
{ "Dhcp4": { "subnet4": [ { "id": 1, "subnet": "10.9.0.0/16",
  # a comment
  "reservations": [ { "hw-address": "ac:00:00:00:00:06", "ip-address": "10.9.10.10", "hostname": "server.lan" } ] } ] } }
"""
ISC_CONF = """host nas {
  hardware ethernet ac:00:00:00:00:07;
  fixed-address 10.9.10.20;
}
host s_lan_0 {
  hardware ethernet ac:00:00:00:00:08;
  fixed-address 10.9.10.21;
  option host-name "printer";
}
host s_lan_1 {
  hardware ethernet ac:00:00:00:00:09;
  fixed-address 10.9.10.22;
}
"""


class TestDhcpServers(unittest.TestCase):
    def test_parsers(self):
        self.assertEqual(devid._kea_leases(KEA_CSV), {"ac:00:00:00:00:01": "laptop"})
        self.assertEqual(devid._isc_leases(ISC_LEASES), {"ac:00:00:00:00:04": "printer"})
        self.assertEqual(devid._kea_reservations(KEA_CONF), {"10.9.10.10": "server"})
        self.assertEqual(devid._isc_hosts(ISC_CONF), {"10.9.10.20": "nas", "10.9.10.21": "printer"})
        self.assertEqual(devid.mac_prefix("52-54-00"), "52:54:00")
        self.assertEqual(devid.mac_prefix("525400"), "52:54:00")

    def test_only_servers_in_use(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            files = {"KEA_CONF": KEA_CONF, "ISC_CONF": ISC_CONF}
            saved = {k: getattr(devid, k) for k in ("DNSMASQ_LEASES", "DNSMASQ_HOSTS", "KEA_LEASES", "KEA_CONF",
                                                     "ISC_LEASES", "ISC_CONF")}
            try:
                for k in saved:
                    setattr(devid, k, os.path.join(d, k))
                for k, text in files.items():
                    with open(os.path.join(d, k), "w") as f:
                        f.write(text)
                self.assertEqual(devid.static_hosts(), {})            # configs alone (e.g. Kea's sample): ignored
                with open(devid.KEA_LEASES, "w") as f:
                    f.write(KEA_CSV)
                self.assertEqual(devid.static_hosts(), {"10.9.10.10": "server"})
                self.assertEqual(devid.leases(), {"ac:00:00:00:00:01": "laptop"})
            finally:
                for k, v in saved.items():
                    setattr(devid, k, v)


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
        self.assertEqual(devid.model({"mdns_models": ["model=Mac99,1"]}), "Mac99,1")     # not in the table: as announced

    def test_announced_model(self):
        model = lambda *m: devid.model({"mdns_models": list(m)})       # noqa: E731
        self.assertEqual(model("md=Acme 4K VH1", "model=AC-55X1", "md=0,1,2"), "AC-55X1")   # model= before md=
        self.assertEqual(model("md=Acme Mini"), "Acme Mini")
        self.assertEqual(model("ty=Acme LaserJet 100", "usb_mdl=Acme LaserJet 100"), "Acme LaserJet 100")
        self.assertEqual(model("am=AcmeTV1,1", "model=J105aAP"), "AcmeTV1,1")              # a board code isn't one
        self.assertIsNone(model("md=AB-1", "md=bridge", "md=hub"))                          # a bridge's accessories
        self.assertIsNone(model("md=0,1,2"))
        self.assertIsNone(model("model=0a1b2c3d4e5f6a7b"))
        self.assertIsNone(devid.model({}))

    def test_current_announcements_outrank_old_ones(self):
        day = 86400
        info = {"mdns_models": ["model=AppleTV5,3", "model=Mac14,14"], "mdns_services": ["_googlecast._tcp", "_airplay._tcp"],
                "seen": {"m|model=Mac14,14": 10 * day, "s|_airplay._tcp": 10 * day}}
        cur = devid.current_mdns(info)
        self.assertEqual(cur["mdns_models"], ["model=Mac14,14"])              # the unstamped old model drops out
        self.assertEqual(cur["mdns_services"], ["_airplay._tcp"])
        self.assertEqual(devid.classify("02:00:00:00:00:70", None, cur)[0], "Mac")
        self.assertEqual(devid.classify("02:00:00:00:00:70", None, info)[0], "Apple TV")     # what it used to say
        self.assertEqual(info["mdns_models"], ["model=AppleTV5,3", "model=Mac14,14"])        # input untouched

    def test_recent_items_all_count_and_old_stamped_ones_drop(self):
        day = 86400
        info = {"mdns_hosts": ["a.local", "b.local", "c.local"],
                "seen": {"h|a.local": 10 * day, "h|b.local": 10 * day - 3600, "h|c.local": 5 * day}}
        self.assertEqual(devid.current_mdns(info)["mdns_hosts"], ["a.local", "b.local"])

    def test_fresh_ids_dont_make_a_real_name_old(self):
        # a phone with a private address: placeholder DHCP name, a real host name from before timestamps, then only
        # fresh UUID hosts and instance names that are never names
        day = 86400
        info = {"mdns_hosts": ["11111111-2222-4333-8444-555555555555.local", "kids-iphone.local"],
                "mdns_names": ["66666666-7777-4888-9999-000000000000", "localnetworkprivacy", "Spotify Group Session [ab12c]"],
                "seen": {"h|11111111-2222-4333-8444-555555555555.local": 10 * day}}
        cur = devid.current_mdns(info)
        self.assertEqual(cur["mdns_hosts"], info["mdns_hosts"])
        self.assertEqual(devid.mdns_name(cur), "kids-iphone")
        self.assertEqual(devid.display_name("iPhone", "iPhone", devid.mdns_name(cur)), "kids-iphone")
        self.assertIsNone(devid.mdns_name({"mdns_names": ["localnetworkprivacy", "Spotify Group Session [ab12c]"]}))

    def test_old_evidence_kept_until_something_newer(self):
        info = {"mdns_models": ["model=AppleTV5,3"], "mdns_hosts": ["den.local"]}
        self.assertEqual(devid.current_mdns(info), info)                      # nothing stamped: all counts
        self.assertEqual(devid.current_mdns(None), {})

    def test_display_name_skips_ids(self):
        self.assertEqual(devid.display_name("0005CD000001", "receiver-den"), "receiver-den")
        self.assertEqual(devid.display_name("0005CD000001"), "0005CD000001")      # nothing better

    def test_mdns_name(self):
        self.assertEqual(devid.mdns_name({"mdns_hosts": ["box.local"], "mdns_names": ["Other"]}), "box")
        self.assertEqual(devid.mdns_name({"mdns_names": ["Living Room"]}), "Living Room")
        self.assertIsNone(devid.mdns_name({}))
