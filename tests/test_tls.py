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

from helpers import client_hello, tls_record

from lib import packet, tls
import capture


def pkt(payload, seq, ts=1.0, sport=50000):
    return packet.Packet(ts, "02:00:00:00:00:01", "02:00:00:00:00:02", "10.0.0.5", "203.0.113.9",
                         packet.PROTO_TCP, sport, 443, payload, seq, 0x18)


class TlsTests(unittest.TestCase):
    def test_single_segment(self):
        h = tls.parse_tcp_payload(tls_record(client_hello("example.com")))
        self.assertEqual(h.sni, "example.com")
        self.assertEqual(h.alpn, ("h2", "http/1.1"))

    def test_rejects_random_data(self):
        self.assertIsNone(tls.parse_tcp_payload(b"\x16\x03\x01\xe3\x5e\x01" + b"\x00" * 100))
        self.assertIsNone(tls.parse_tcp_payload(b"\x16\x07\x01\x00\x50\x01" + b"\x00" * 100))

    def test_split_hello_is_joined(self):
        rec = tls_record(client_hello("split.example.org", padding_before=1300))
        first, second = rec[:1388], rec[1388:]
        events = []
        cap = capture.Capture(events.append)
        cap.handle(pkt(first, 1000))
        self.assertEqual(events, [])              # held, waiting for the rest
        cap.handle(pkt(second, 1000 + len(first)))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["sni"], "split.example.org")
        self.assertTrue(events[0]["joined"])

    def test_split_hello_expires_without_continuation(self):
        rec = tls_record(client_hello("lost.example.org", padding_before=1300))
        events = []
        cap = capture.Capture(events.append)
        cap.handle(pkt(rec[:1388], 5, ts=10.0))
        cap.expire_partial(10.0 + capture.PARTIAL_TTL + 1)
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]["sni"])

    def test_wrong_seq_ignored(self):
        rec = tls_record(client_hello("seq.example.org", padding_before=1300))
        events = []
        cap = capture.Capture(events.append)
        cap.handle(pkt(rec[:1388], 1000))
        cap.handle(pkt(rec[1388:], 9999))
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
