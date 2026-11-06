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


"""The SNMP client on synthetic packets, against a fake switch on loopback, and the port map on synthetic tables."""
import json
import os
import socket
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "opnsense", "scripts", "loupe"))
from lib import netif, snmp, switches  # noqa: E402

ROUTER = "02:00:00:00:00:01"
SW1, SW2 = "02:00:00:00:00:a1", "02:00:00:00:00:a2"
PC, TV, PHONE, RADIO = "ac:00:00:00:00:01", "ac:00:00:00:00:02", "ac:00:00:00:00:03", "ac:00:00:00:05:04"


def tlv(tag, body):
    return snmp._tlv(tag, body)


def reply(request_id, binds, status=0, community=b"public"):
    """An SNMPv2c response: binds are (oid, BER-encoded value)."""
    body = b"".join(tlv(snmp.SEQUENCE, snmp._encode_oid(o) + v) for o, v in binds)
    pdu = snmp._integer(request_id) + snmp._integer(status) + snmp._integer(0) + tlv(snmp.SEQUENCE, body)
    return tlv(snmp.SEQUENCE, snmp._integer(1) + tlv(snmp.OCTETS, community) + tlv(snmp.RESPONSE, pdu))


def read_request(packet):
    """(kind, request id, repetitions, first OID), by the same reader the client uses on replies."""
    a, stop = snmp._expect(packet, 0, len(packet), snmp.SEQUENCE)
    _, b = snmp._expect(packet, a, stop, snmp.INTEGER)
    _, b = snmp._expect(packet, b, stop, snmp.OCTETS)
    kind, a, stop = snmp._read(packet, b, stop)
    numbers = []
    for _ in range(3):
        c, a = snmp._expect(packet, a, stop, snmp.INTEGER)
        numbers.append(int.from_bytes(packet[c:a], "big", signed=True))
    c, d = snmp._expect(packet, a, stop, snmp.SEQUENCE)
    e, f = snmp._expect(packet, c, d, snmp.SEQUENCE)
    g, h = snmp._expect(packet, e, f, snmp.OID)
    return kind, numbers[0], numbers[2], snmp._decode_oid(packet[g:h])


class FakeSwitch(threading.Thread):
    """Answers GET and GETBULK from a sorted table {oid: encoded value}, on a loopback port."""

    def __init__(self, table, answer=None):
        super().__init__(daemon=True)
        self.table = sorted(table.items())
        self.answer = answer
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.requests = 0

    def run(self):
        while True:
            try:
                packet, peer = self.sock.recvfrom(65535)
            except OSError:
                return
            self.requests += 1
            kind, request_id, repetitions, oid = read_request(packet)
            if self.answer:
                out = self.answer(kind, request_id, repetitions, oid)
            elif kind == snmp.GET:
                value = dict(self.table).get(oid, tlv(0x80, b""))
                out = reply(request_id, [(oid, value)])
            else:
                rows = [r for r in self.table if r[0] > oid][:repetitions]
                out = reply(request_id, rows or [(oid, tlv(0x82, b""))])
            if out:
                self.sock.sendto(out, peer)

    def client(self, **kw):
        c = snmp.Client("127.0.0.1", "public", **kw)
        c.sock.connect(("127.0.0.1", self.port))
        return c

    def stop(self):
        self.sock.close()


def integer(n):
    return snmp._integer(n)


class TestBer(unittest.TestCase):
    def test_oid_round_trip(self):
        oid = snmp.oid_tuple("1.3.6.1.2.1.17.7.1.2.2.1.2.1.172.0.200.300")
        encoded = snmp._encode_oid(oid)
        self.assertEqual(snmp._decode_oid(encoded[2:]), oid)

    def test_long_lengths(self):
        self.assertEqual(snmp._length(5), b"\x05")
        self.assertEqual(snmp._length(200), b"\x81\xc8")
        self.assertEqual(snmp._length(300), b"\x82\x01\x2c")

    def test_integers(self):
        self.assertEqual(integer(127)[2:], b"\x7f")
        self.assertEqual(integer(128)[2:], b"\x00\x80")     # would read as negative without the zero
        self.assertEqual(integer(0)[2:], b"\x00")

    def test_parse_values(self):
        oid = (1, 3, 6, 1)
        data = reply(7, [(oid + (1,), integer(5)), (oid + (2,), tlv(snmp.OCTETS, b"hi")), (oid + (3,), tlv(0x46, b"\xff\xff")),
                         (oid + (4,), tlv(0x81, b""))])
        request_id, status, binds = snmp.parse(data)
        self.assertEqual((request_id, status), (7, 0))
        self.assertEqual([v for _, v in binds], [5, b"hi", 65535, None])

    def test_bad_replies_raise_only_snmp_errors(self):
        good = reply(7, [((1, 3, 6, 1), integer(5))])
        for cut in range(len(good)):
            with self.assertRaises(snmp.SnmpError):
                snmp.parse(good[:cut])
        for bad in (b"", b"\x30", b"\x30\x84\xff\xff\xff\xff", b"\x30\x85\x01\x01\x01\x01\x01", b"\x02\x01\x01", b"\xff" * 40):
            with self.assertRaises(snmp.SnmpError):
                snmp.parse(bad)

    def test_flipped_bytes_never_crash(self):
        good = reply(7, [((1, 3, 6, 1, 2), tlv(snmp.OCTETS, b"switch")), ((1, 3, 6, 1, 3), integer(300))])
        for i in range(len(good)):
            for value in (0, 0x7f, 0x80, 0x84, 0xff):
                try:
                    snmp.parse(good[:i] + bytes([value]) + good[i + 1:])
                except snmp.SnmpError:
                    pass


class TestClient(unittest.TestCase):
    def start(self, table=None, answer=None):
        fake = FakeSwitch(table or {}, answer)
        fake.start()
        self.addCleanup(fake.stop)
        client = fake.client(timeout=0.1, retries=1)
        self.addCleanup(client.close)
        return fake, client

    def test_walk_pages_through_a_table(self):
        base = (1, 3, 6, 1, 9)
        table = {base + (i,): integer(i * 10) for i in range(1, 61)}
        table[(1, 3, 6, 1, 10, 1)] = integer(1)        # the next table: not part of the walk
        fake, client = self.start(table)
        rows = client.walk(base)
        self.assertEqual(len(rows), 60)
        self.assertEqual(rows[(60,)], 600)
        self.assertGreater(fake.requests, 1)

    def test_get(self):
        _, client = self.start({(1, 3, 6, 1): tlv(snmp.OCTETS, b"model")})
        self.assertEqual(client.get((1, 3, 6, 1)), b"model")
        self.assertIsNone(client.get((1, 3, 6, 2)))

    def test_silence_is_no_answer(self):
        _, client = self.start(answer=lambda *a: None)
        with self.assertRaisesRegex(snmp.SnmpError, "no answer"):
            client.get((1, 3, 6, 1))

    def test_other_request_ids_and_garbage_are_ignored(self):
        def answer(kind, request_id, repetitions, oid):
            return reply(request_id + 1, [(oid, integer(1))])
        fake, client = self.start(answer=answer)
        with self.assertRaises(snmp.SnmpError):
            client.get((1, 3, 6, 1))
        self.assertEqual(fake.requests, 2)

    def test_a_switch_that_repeats_itself_ends(self):
        base = (1, 3, 6, 1, 9)

        def answer(kind, request_id, repetitions, oid):
            return reply(request_id, [(base + (1,), integer(1))])
        _, client = self.start(answer=answer)
        self.assertEqual(client.walk(base), {(1,): 1})

    def test_too_big_asks_for_less(self):
        base = (1, 3, 6, 1, 9)
        asked = []

        def answer(kind, request_id, repetitions, oid):
            asked.append(repetitions)
            if repetitions > 5:
                return reply(request_id, [], status=snmp.TOO_BIG)
            return reply(request_id, [((1, 3, 6, 1, 10), integer(1))])
        _, client = self.start(answer=answer)
        client.walk(base)
        self.assertEqual(asked, [25, 12, 6, 3])

    def test_deadline(self):
        _, client = self.start(answer=lambda *a: None)
        client.deadline = 0
        with self.assertRaisesRegex(snmp.SnmpError, "too long"):
            client.get((1, 3, 6, 1))


def tables(mac, ports, descr="SW-1, firmware 1"):
    """Poll output: ports are (name, up, speed, [MACs])."""
    out = {"descr": descr, "mac": mac, "ports": {}, "fdb": {}, "ignored": 0, "fetched": 100}
    for i, (name, up, speed, macs) in enumerate(ports, 1):
        out["ports"][str(i)] = {"name": name, "up": up, "speed": speed}
        if macs:
            out["fdb"][str(i)] = macs
    return out


def device(mac, name, ip="", dtype="", last=1000):
    return {"mac": mac, "name": name, "ip": ip, "type": dtype, "icon": "", "model": "", "model_in_name": False, "area": "", "last_seen": last}


class TestReadTables(unittest.TestCase):
    def fake(self, fdb):
        table = {switches.SYS_DESCR: tlv(snmp.OCTETS, b"SW-1, firmware 1"), switches.SYS_NAME: tlv(snmp.OCTETS, b"rack-switch"), switches.BRIDGE_ADDR: tlv(snmp.OCTETS, bytes.fromhex("0200000000a1"))}
        ports = {1: (b"TenGigabitEthernet1", 6, 1, 10000), 2: (b"0/2", 6, 1, 1000), 3: (b"0/3", 6, 2, 0),
                 4: (b"vlan1", 53, 1, 0), 5: (b"CPU Interface", 6, 1, 0), 6: (b"0/6", 6, 6, 0)}
        for i, (name, kind, oper, speed) in ports.items():
            table[switches.IF_NAME + (i,)] = tlv(snmp.OCTETS, name)
            table[switches.IF_TYPE + (i,)] = integer(kind)
            table[switches.IF_OPER + (i,)] = integer(oper)
            table[switches.IF_SPEED + (i,)] = tlv(0x42, speed.to_bytes(2, "big"))
            table[switches.BASE_PORT_IF + (i,)] = integer(i)
        table.update(fdb)
        fake = FakeSwitch(table)
        fake.start()
        self.addCleanup(fake.stop)
        client = fake.client(timeout=0.2)
        self.addCleanup(client.close)
        return switches.read_tables(client)

    def test_standard_and_length_prefixed_indexes(self):
        mac = (0xac, 0, 0, 0, 0, 1)
        other = (0xac, 0, 0, 0, 0, 2)
        out = self.fake({switches.FDB_QBRIDGE + (1,) + mac: integer(1), switches.FDB_QBRIDGE + (1, 6) + other: integer(2)})
        self.assertEqual(out["fdb"], {"1": [PC], "2": [TV]})
        self.assertEqual(out["mac"], SW1)
        self.assertEqual(out["sysname"], "rack-switch")
        self.assertEqual(out["ports"]["1"]["name"], "TE1")
        self.assertEqual(out["ports"]["1"]["speed"], 10000)

    def test_ports_that_are_not_ports_are_left_out(self):
        out = self.fake({})
        self.assertEqual(sorted(out["ports"]), ["1", "2", "3"])     # not the VLAN, the CPU or the absent one

    def test_macs_on_down_ports_are_ignored(self):
        out = self.fake({switches.FDB_QBRIDGE + (1, 0xac, 0, 0, 0, 0, 1): integer(3)})
        self.assertEqual(out["fdb"], {})
        self.assertEqual(out["ignored"], 1)

    def test_older_table_when_there_is_no_vlan_one(self):
        out = self.fake({switches.FDB_BRIDGE + (0xac, 0, 0, 0, 0, 1): integer(1)})
        self.assertEqual(out["fdb"], {"1": [PC]})

    def test_port_zero_is_the_switch_itself(self):
        out = self.fake({switches.FDB_QBRIDGE + (1, 0xac, 0, 0, 0, 0, 1): integer(0)})
        self.assertEqual(out["fdb"], {})


class TestLocate(unittest.TestCase):
    def setUp(self):
        self.polls = {
            "a": tables(SW1, [("0/1", True, 1000, [ROUTER]), ("0/2", True, 1000, [PC]), ("0/3", True, 10000, [SW2, TV, PC])]),
            "b": tables(SW2, [("TE1", True, 10000, [ROUTER, SW1, PC]), ("TE2", True, 1000, [TV])]),
        }

    def test_uplink_links_and_devices(self):
        uplinks, links, where = switches.locate(self.polls, [ROUTER])
        self.assertEqual(uplinks, {"a": "1", "b": "1"})
        self.assertEqual(links["a"], {"3": "b"})
        self.assertEqual(where[PC], ("a", "2"))
        self.assertEqual(where[TV], ("b", "2"))
        self.assertNotIn(ROUTER, where)
        self.assertNotIn(SW2, where)

    def test_an_unpolled_switch_between_goes_to_the_quieter_port(self):
        polls = {"a": tables(SW1, [("1", True, 1000, [ROUTER]), ("2", True, 1000, [PC, TV, PHONE])]),
                 "b": tables(SW2, [("1", True, 1000, [ROUTER]), ("2", True, 1000, [PC])])}
        self.assertEqual(switches.locate(polls, [ROUTER])[2][PC], ("b", "2"))


class TestDescribe(unittest.TestCase):
    CONF = [{"id": "a", "name": "", "address": "192.0.2.3", "enabled": True}, {"id": "b", "name": "Desk", "address": "192.0.2.4", "enabled": True},
            {"id": "c", "name": "Off", "address": "192.0.2.5", "enabled": False}]
    INFO = {PC: device(PC, "Laptop", "192.0.2.10", "Computer"), TV: device(TV, "TV", "192.0.2.11", "TV"),
            RADIO: device(RADIO, "", "192.0.2.12", "Wi-Fi access point")}

    def state(self, eero=None):
        return {"polls": {
            "a": tables(SW1, [("0/1", True, 1000, [ROUTER]), ("0/2", True, 1000, [PC]), ("0/3", True, 10000, [SW2, TV]), ("0/4", False, 0, [])]),
            "b": tables(SW2, [("TE1", True, 10000, [ROUTER, SW1]), ("TE2", True, 1000, [TV])], descr="SW-2, firmware 2"),
        }, "eero": eero or {}}

    def test_rows(self):
        out, found, _ = switches.describe(self.CONF, self.state(), self.INFO, [ROUTER], {PC: (500, 100)})
        self.assertEqual(list(out), ["a", "b"])
        a = out["a"]
        self.assertEqual(a["name"], "SW-1")
        self.assertEqual((a["up"], a["total"], a["devices"], a["down"], a["uplink"]), (3, 4, 1, "0/4", "Router"))
        self.assertEqual([(r["port"], r["speed"], r["kind"], r["name"]) for r in a["rows"]],
                         [("0/1", "1G", "uplink", "Router"), ("0/2", "1G", "device", "Laptop"), ("0/3", "10G", "switch", "Desk")])
        self.assertEqual(a["rows"][1]["down"], 500)
        self.assertEqual(out["b"]["uplink"], "SW-1 0/3")
        self.assertEqual(out["b"]["uplink_id"], "a")
        self.assertEqual(found[TV], {"switch": "b", "name": "Desk", "port": "TE2"})

    def test_no_community_or_settings_leak_into_the_answer(self):
        conf = [dict(s, community="s3cret") for s in self.CONF]
        out, _, _ = switches.describe(conf, self.state(), self.INFO, [ROUTER])
        self.assertNotIn("s3cret", json.dumps(out))

    def test_a_meshed_eero_with_no_radio_named_still_shows(self):
        eero = switches.Eero({"units": [{"name": "loft", "ip": "192.0.2.12"}, {"name": "loft two", "ip": "192.0.2.14", "backhaul": "Wireless via loft two (5 GHz)"},
                                        {"name": "attic", "ip": "192.0.2.13", "backhaul": "Wireless via loft"},
                                        {"name": "den", "ip": "192.0.2.15", "backhaul": "Wireless via loft (5 GHz)"}], "clients": []})
        self.assertEqual(sorted(u["name"] for u in eero.under({"name": "loft"})), ["attic", "den"])

    def test_a_port_with_nothing_known_still_has_a_row(self):
        state = {"polls": {"a": tables(SW1, [("1", True, 1000, [])])}, "eero": {}}
        out, _, _ = switches.describe(self.CONF[:1], state, {}, [ROUTER])
        self.assertEqual([(r["port"], r["kind"]) for r in out["a"]["rows"]], [("1", "none")])

    def test_without_eero_an_access_points_clients_are_listed_flat_on_its_port(self):
        state = {"polls": {"a": tables(SW1, [("1", True, 1000, [RADIO, PC, TV])])}, "eero": {}}
        out, found, _ = switches.describe(self.CONF[:1], state, self.INFO, [ROUTER])
        rows = out["a"]["rows"]
        self.assertEqual({r["depth"] for r in rows}, {0})
        self.assertEqual(len(rows), 3)
        self.assertEqual(out["a"]["devices"], 3)
        self.assertIn("may be on its Wi-Fi or wired", rows[0]["port_note"])
        self.assertEqual(found[PC]["port"], "1")

    def test_a_router_without_a_bridge_keeps_its_section_with_the_wan_and_the_downlink(self):
        ports = {"bridges": [], "ports": {"ix0": {"name": "ix0", "up": True, "speed": 10000},
                                          "ix1": {"name": "ix1", "up": True, "speed": 10000, "wan": True}},
                 "fdb": {"ix0": [SW1, PC]}}
        state = {"polls": {"a": tables(SW1, [("1", True, 1000, [ROUTER]), ("2", True, 1000, [PC])])}, "eero": {}}
        out, found, _ = switches.describe(self.CONF[:1], state, self.INFO, [ROUTER], {}, ports)
        rows = out[switches.ROUTER_ID]["rows"]
        self.assertEqual([(r["port"], r["kind"], r["name"]) for r in rows], [("ix0", "switch", "SW-1"), ("ix1", "wan", "WAN")])
        self.assertEqual(out["a"]["uplink"], "Router ix0")
        self.assertEqual(found[PC]["switch"], "a")

    def bridged(self):
        return {"bridges": ["bridge0"], "ports": {"igc1": {"name": "igc1", "up": True, "speed": 2500},
                                                  "ix0": {"name": "ix0", "up": True, "speed": 10000},
                                                  "igc2": {"name": "igc2", "up": False, "speed": 0}},
                "fdb": {"igc1": [PC], "ix0": [SW1, SW2, TV]}}

    def test_the_router_bridge_is_the_top_of_the_tree(self):
        state = {"polls": {
            "a": tables(SW1, [("0/1", True, 1000, [ROUTER]), ("0/3", True, 10000, [SW2, TV])]),
            "b": tables(SW2, [("TE1", True, 10000, [ROUTER, SW1]), ("TE2", True, 1000, [TV])]),
        }, "eero": {}}
        out, found, _ = switches.describe(self.CONF, state, self.INFO, [ROUTER], {}, self.bridged())
        router = out[switches.ROUTER_ID]
        self.assertEqual((router["name"], router["total"], router["up"], router["down"]), ("Router", 3, 2, "igc2"))
        self.assertEqual(found[PC], {"switch": "router", "name": "Router", "port": "igc1"})
        self.assertEqual(out["a"]["uplink"], "Router ix0")
        self.assertEqual(out["a"]["uplink_id"], "router")
        self.assertEqual(out["b"]["uplink"], "SW-1 0/3")     # a chain: the router's port lists both, and links to the first
        self.assertEqual(found[TV]["switch"], "b")

    def test_the_router_is_not_shown_without_a_listed_switch(self):
        out, _, _ = switches.describe([], {"polls": {}, "eero": {}}, {}, [ROUTER], {}, self.bridged())
        self.assertEqual(out, {})

    def test_where_a_device_ties_the_switch_wins_over_the_router(self):
        state = {"polls": {"a": tables(SW1, [("1", True, 1000, [ROUTER]), ("2", True, 1000, [PC])])}, "eero": {}}
        _, found, _ = switches.describe(self.CONF[:1], state, self.INFO, [ROUTER], {}, self.bridged())
        self.assertEqual(found[PC]["switch"], "a")

    def quiet_tv(self, seen=1000, port_up=True, table=None):
        state = {"polls": {"a": tables(SW1, [("1", True, 1000, [ROUTER]), ("2", port_up, 1000, table or [])])}, "eero": {},
                 "last_ports": {TV: {"switch": "a", "port": "2", "seen": seen}}}
        return state

    def test_a_quiet_device_stays_on_its_last_port(self):
        out, found, _ = switches.describe(self.CONF[:1], self.quiet_tv(), self.INFO, [ROUTER])
        row = out["a"]["rows"][1]
        self.assertEqual((row["port"], row["name"], row["last_here"]), ("2", "TV", 1000))
        self.assertEqual(found[TV]["last_here"], 1000)
        self.assertEqual(out["a"]["devices"], 1)

    def test_a_port_that_is_down_shows_if_a_device_was_last_on_it(self):
        out, _, _ = switches.describe(self.CONF[:1], self.quiet_tv(port_up=False), self.INFO, [ROUTER])
        row = out["a"]["rows"][1]
        self.assertEqual((row["port"], row["speed"], row["name"]), ("2", "down", "TV"))

    def test_where_it_is_now_beats_where_it_was(self):
        state = self.quiet_tv()
        state["polls"]["a"]["ports"]["3"] = {"name": "3", "up": True, "speed": 1000}
        state["polls"]["a"]["fdb"]["3"] = [TV]
        out, found, _ = switches.describe(self.CONF[:1], state, self.INFO, [ROUTER])
        self.assertEqual(found[TV]["port"], "3")
        self.assertNotIn("last_here", found[TV])
        self.assertEqual([(r["port"], r["kind"]) for r in out["a"]["rows"]], [("1", "uplink"), ("2", "none"), ("3", "device")])

    def test_on_wifi_now_means_not_on_its_old_port(self):
        state = self.quiet_tv()
        state["eero"] = {"units": [], "clients": [{"mac": TV, "name": "TV", "unit": "x", "connected": True}]}
        _, found, _ = switches.describe(self.CONF[:1], state, self.INFO, [ROUTER])
        self.assertNotIn(TV, found)

    def test_a_device_not_seen_in_the_period_is_not_shown(self):
        _, found, _ = switches.describe(self.CONF[:1], self.quiet_tv(), self.INFO, [ROUTER], since=2000)
        self.assertNotIn(TV, found)

    def test_a_port_that_became_a_link_holds_no_remembered_devices(self):
        state = self.quiet_tv()
        state["polls"]["b"] = tables(SW2, [("TE1", True, 1000, [ROUTER, SW1])])
        state["polls"]["a"]["fdb"]["2"] = [SW2]
        _, found, _ = switches.describe(self.CONF[:2], state, self.INFO, [ROUTER])
        self.assertNotIn(TV, found)

    def test_unknown_mac_shows_as_itself(self):
        state = {"polls": {"a": tables(SW1, [("1", True, 1000, [PHONE])])}, "eero": {}}
        out, _, _ = switches.describe(self.CONF[:1], state, {}, [ROUTER])
        self.assertEqual(out["a"]["rows"][0]["name"], PHONE)
        self.assertEqual(out["a"]["rows"][0]["id"], "")

    def test_access_point_port_says_clients_may_be_wifi(self):
        state = {"polls": {"a": tables(SW1, [("1", True, 1000, [RADIO, PC])])}, "eero": {}}
        out, _, _ = switches.describe(self.CONF[:1], state, self.INFO, [ROUTER])
        self.assertIn("may be on its Wi-Fi", out["a"]["rows"][0]["port_note"])

    def test_eero_clients_go_under_their_unit(self):
        eero = {"units": [{"name": "loft", "ip": "192.0.2.12", "backhaul": "Wired"},
                          {"name": "attic", "id": "42", "ip": "192.0.2.13", "backhaul": "Wireless via loft (5 GHz)"}],
                "clients": [{"mac": PC, "name": "x", "unit": "loft", "connected": True, "band": "5 GHz"},
                            {"mac": PHONE, "name": "Phone", "ip": "192.0.2.14", "unit": "attic", "connected": True, "band": "2.4 GHz"},
                            {"mac": TV, "name": "TV", "unit": "loft", "connected": False}]}
        state = {"polls": {"a": tables(SW1, [("1", True, 1000, [RADIO, RADIO[:14] + "ff", PC, PHONE, TV])])}, "eero": eero}
        out, found, _ = switches.describe(self.CONF[:1], state, self.INFO, [ROUTER])
        rows = out["a"]["rows"]
        self.assertEqual([(r["kind"], r["name"], r["depth"]) for r in rows],
                         [("ap", "eero loft", 0), ("client", "Laptop", 1), ("ap", "eero attic", 1), ("client", "Phone", 2),
                          ("device", "TV", 0)])
        self.assertIn("last seen on Wi-Fi", rows[-1]["detail"])
        self.assertEqual(found[PHONE]["via"], "eero attic")
        self.assertEqual((found[PHONE]["via_unit"], found[PHONE]["via_id"]), ("attic", "42"))
        self.assertEqual(found[PC]["via_id"], "")       # no id, so the page shows its name without a link
        self.assertEqual(found[PC]["port"], "1")

    def test_elsewhere_lists_only_what_no_port_accounts_for(self):
        info = dict(self.INFO, **{PHONE: device(PHONE, "Phone"), "ac:00:00:00:00:09": device("ac:00:00:00:00:09", "Old", last=1),
                                  SW2: device(SW2, "Desk switch", "192.0.2.4"), ROUTER: device(ROUTER, "router")})
        described, found, eero = switches.describe(self.CONF, self.state(), info, [ROUTER])
        rows = switches.elsewhere(described, found, eero, info, [ROUTER], since=500)
        self.assertEqual({r["name"] for r in rows}, {"Phone", "192.0.2.12"})


class TestRefresh(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "switches.json")

    def test_a_failure_keeps_the_last_good_tables(self):
        conf = [{"id": "a", "address": "192.0.2.3", "community": "c", "enabled": True}]
        switches.refresh(self.path, conf, "all", {}, polling=lambda a, c: tables(SW1, [("1", True, 1, [PC])]))
        errors = switches.refresh(self.path, conf, "all", {}, polling=lambda a, c: {"error": "no answer"})
        self.assertEqual(errors, {"a": "no answer"})
        kept = switches.load_state(self.path)["polls"]["a"]
        self.assertEqual(kept["error"], "no answer")
        self.assertEqual(kept["fdb"], {"1": [PC]})
        switches.refresh(self.path, conf, "all", {}, polling=lambda a, c: tables(SW1, []))
        self.assertNotIn("error", switches.load_state(self.path)["polls"]["a"])

    def test_one_switch_leaves_the_others(self):
        conf = [{"id": "a", "address": "192.0.2.3", "community": "c", "enabled": True}, {"id": "b", "address": "192.0.2.4", "community": "c", "enabled": True}]
        calls = []

        def polling(address, community):
            calls.append(address)
            return tables(SW1, [])
        switches.refresh(self.path, conf, "all", {}, polling=polling)
        calls.clear()
        switches.refresh(self.path, conf, "b", {}, polling=polling)
        self.assertEqual(calls, ["192.0.2.4"])
        self.assertEqual(sorted(switches.load_state(self.path)["polls"]), ["a", "b"])

    def test_removed_and_disabled_switches(self):
        conf = [{"id": "a", "address": "192.0.2.3", "community": "c", "enabled": True}]
        switches.refresh(self.path, conf, "all", {}, polling=lambda a, c: tables(SW1, []))
        calls = []
        switches.refresh(self.path, [], "all", {}, polling=lambda a, c: calls.append(a))
        self.assertEqual(switches.load_state(self.path)["polls"], {})
        switches.refresh(self.path, [dict(conf[0], enabled=False)], "all", {}, polling=lambda a, c: calls.append(a))
        self.assertEqual(calls, [])

    def test_community_is_not_stored(self):
        conf = [{"id": "a", "address": "192.0.2.3", "community": "s3cret", "enabled": True}]
        switches.refresh(self.path, conf, "all", {}, polling=lambda a, c: tables(SW1, []))
        with open(self.path, encoding="utf-8") as f:
            self.assertNotIn("s3cret", f.read())

    def test_bad_state_file(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(switches.load_state(self.path)["polls"], {})


class TestRemember(unittest.TestCase):
    def polls(self, fetched=100):
        return {"a": dict(tables(SW1, [("1", True, 1000, [ROUTER]), ("2", True, 1000, [PC])]), fetched=fetched)}

    def test_a_device_found_is_remembered_and_stays_when_it_goes_quiet(self):
        memory = switches.remember({}, self.polls(), [ROUTER], 100)
        self.assertEqual(memory[PC], {"switch": "a", "port": "2", "seen": 100})
        empty = {"a": tables(SW1, [("1", True, 1000, [ROUTER])])}
        self.assertEqual(switches.remember(memory, empty, [ROUTER], 200)[PC]["port"], "2")

    def test_tables_kept_from_a_failed_read_dont_overrule_a_newer_finding(self):
        memory = {PC: {"switch": "a", "port": "9", "seen": 500}}
        self.assertEqual(switches.remember(memory, self.polls(fetched=100), [ROUTER], 600)[PC]["port"], "9")

    def test_old_finds_and_unlisted_switches_are_dropped(self):
        now = 100 + switches.REMEMBER_DAYS * 86400
        self.assertEqual(switches.remember({PC: {"switch": "a", "port": "2", "seen": 100}}, {"a": tables(SW1, [])}, [ROUTER], now), {})
        self.assertEqual(switches.remember({PC: {"switch": "gone", "port": "2", "seen": 100}}, {"a": tables(SW1, [])}, [ROUTER], 200), {})

    def test_refresh_keeps_them_in_the_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.json")
            conf = [{"id": "a", "address": "192.0.2.1", "community": "public", "enabled": True}]
            switches.refresh(path, conf, "all", {}, polling=lambda a, c: self.polls()["a"], router_macs=[ROUTER])
            self.assertEqual(switches.load_state(path)["last_ports"][PC]["port"], "2")


class TestNames(unittest.TestCase):
    def test_every_name_has_a_source(self):
        polled = {"sysname": "rack-switch", "descr": "SW-1, firmware 1"}
        name = switches.display_name
        self.assertEqual(name({"name": "Mine", "address": "192.0.2.3"}, polled), "Mine")
        self.assertEqual(name({"name": "", "address": "192.0.2.3"}, polled), "rack-switch")
        self.assertEqual(name({"name": "", "address": "192.0.2.3"}, {"descr": "SW-1, firmware 1"}), "SW-1")
        self.assertEqual(name({"name": "", "address": "192.0.2.3"}, {}), "192.0.2.3")

    def test_disabled_switches_have_names_too(self):
        conf = [{"id": "a", "name": "", "address": "192.0.2.3", "enabled": False}]
        state = {"polls": {"a": {"sysname": "rack-switch"}}}
        self.assertEqual(switches.all_names(conf, state), {"a": "rack-switch"})


class TestBridgeParsing(unittest.TestCase):
    def test_members(self):
        raw = b"igc1".ljust(netif.IFBREQ_SIZE, b"\0") + b"ix0".ljust(netif.IFBREQ_SIZE, b"\0")
        self.assertEqual(netif.parse_bridge_members(raw), ["igc1", "ix0"])

    def test_addresses_learned_only_and_a_short_tail_ignored(self):
        def entry(name, flags, mac):
            return name.ljust(16, b"\0") + bytes(8) + bytes([flags]) + mac + bytes(9)
        raw = entry(b"igc1", 0, bytes.fromhex("020000000010")) + entry(b"ix0", 1, bytes.fromhex("020000000011")) + b"short"
        self.assertEqual(netif.parse_bridge_addresses(raw), [("igc1", "02:00:00:00:00:10")])


class TestText(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(switches.ranges(["0/4", "0/5", "0/6", "0/11", "0/17", "0/21", "0/22"]), "0/4-0/6, 0/11, 0/17, 0/21-0/22")
        self.assertEqual(switches.ranges(["TE2", "TE3", "lag"]), "TE2-TE3, lag")
        self.assertEqual(switches.ranges([]), "")

    def test_speed(self):
        self.assertEqual([switches.speed_text(s) for s in (0, 100, 1000, 2500, 10000)], ["", "100M", "1G", "2.5G", "10G"])


if __name__ == "__main__":
    unittest.main()
