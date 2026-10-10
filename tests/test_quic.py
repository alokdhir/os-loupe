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

import os
import struct
import unittest

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from helpers import client_hello

from lib import quic


def varint(v):
    if v < 64:
        return bytes([v])
    if v < 16384:
        return struct.pack("!H", 0x4000 | v)
    return struct.pack("!I", 0x80000000 | v)


def crypto_frame(offset, data):
    return b"\x06" + varint(offset) + varint(len(data)) + data


def initial(version, dcid, frames, pn=0):
    """Encrypt one client Initial packet the way a client would (RFC 9001 §5)."""
    aead, iv, hp = quic._client_keys(version, dcid)
    ptype = quic.INITIAL_TYPE[version]
    pn_bytes = struct.pack("!I", pn)  # 4-byte packet number
    payload = frames + b"\x00" * max(0, 1100 - len(frames))  # PADDING
    first = 0xC0 | (ptype << 4) | 0x03
    hdr = (bytes([first]) + struct.pack("!I", version) + bytes([len(dcid)]) + dcid + b"\x00"
           + varint(0) + struct.pack("!H", 0x4000 | (len(pn_bytes) + len(payload) + 16)))
    nonce = bytearray(iv)
    for i, b in enumerate(pn_bytes.rjust(12, b"\x00")):
        nonce[i] ^= b
    ct = aead.encrypt(bytes(nonce), payload, hdr + pn_bytes)
    pn_off = len(hdr)
    pkt = bytearray(hdr + pn_bytes + ct)
    sample = bytes(pkt[pn_off + 4:pn_off + 20])
    enc = Cipher(algorithms.AES(hp), modes.ECB()).encryptor()
    mask = enc.update(sample) + enc.finalize()
    pkt[0] ^= mask[0] & 0x0F
    for i in range(4):
        pkt[pn_off + i] ^= mask[1 + i]
    return bytes(pkt)


class QuicTests(unittest.TestCase):
    def test_v1_single_packet(self):
        dcid = os.urandom(8)
        ch = client_hello("www.example.com")
        r = quic.Reassembler()
        h = r.feed("10.0.0.5", initial(quic.V1, dcid, crypto_frame(0, ch)), now=1.0)
        self.assertEqual(h.sni, "www.example.com")

    def test_v2(self):
        dcid = os.urandom(8)
        h = quic.Reassembler().feed("10.0.0.5", initial(quic.V2, dcid, crypto_frame(0, client_hello("v2.example.net"))), now=1.0)
        self.assertEqual(h.sni, "v2.example.net")

    def test_out_of_order_frames_across_two_packets(self):
        # Chrome-style: big hello, CRYPTO frames shuffled and split over two Initials
        dcid = os.urandom(8)
        ch = client_hello("split.example.org", padding_before=1300)
        a, b, c = ch[:700], ch[700:1200], ch[1200:]
        p1 = initial(quic.V1, dcid, crypto_frame(700, b) + crypto_frame(0, a), pn=0)
        p2 = initial(quic.V1, dcid, crypto_frame(1200, c), pn=1)
        r = quic.Reassembler()
        self.assertIsNone(r.feed("10.0.0.5", p1, now=1.0))
        h = r.feed("10.0.0.5", p2, now=1.1)
        self.assertEqual(h.sni, "split.example.org")
        self.assertIsNone(r.feed("10.0.0.5", p2, now=1.2))  # retransmit after completion ignored

    def test_garbage(self):
        self.assertIsNone(quic.Reassembler().feed("10.0.0.5", b"\xc0\x00\x00\x00\x01" + os.urandom(1200), now=1.0))


if __name__ == "__main__":
    unittest.main()
