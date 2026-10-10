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

"""QUIC Initial packets -> TLS ClientHello -> SNI.

A client's Initial packets are encrypted, but with keys derived from the Destination
Connection ID that's in the clear (RFC 9001 §5.2, RFC 9369 for v2), so any observer
can decrypt them. Browsers split the ClientHello over several CRYPTO frames — out of
order, and across more than one packet when post-quantum key shares make it big —
so we reassemble per (client, DCID) by CRYPTO offset.
"""
import hashlib
import hmac
import struct
import time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import tls

V1 = 0x00000001
V2 = 0x6B3343CF
SALTS = {
    V1: bytes.fromhex("38762cf7f55934b34d179ae6a4c80cadccbb7f0a"),
    V2: bytes.fromhex("0dede3def700a6db819381be6e269dcbf9bd2ed9"),
}
LABELS = {V1: ("quic key", "quic iv", "quic hp"), V2: ("quicv2 key", "quicv2 iv", "quicv2 hp")}
INITIAL_TYPE = {V1: 0, V2: 1}

MAX_PENDING = 4096
PENDING_TTL = 10.0
MAX_CRYPTO = 16384


def _expand_label(secret, label, length):
    full = b"tls13 " + label.encode()
    info = struct.pack("!HB", length, len(full)) + full + b"\x00"
    out, block, i = b"", b"", 1
    while len(out) < length:
        block = hmac.new(secret, block + info + bytes([i]), hashlib.sha256).digest()
        out += block
        i += 1
    return out[:length]


_key_cache = {}


def _client_keys(version, dcid):
    k = (version, dcid)
    keys = _key_cache.get(k)
    if keys is None:
        initial = hmac.new(SALTS[version], dcid, hashlib.sha256).digest()
        client = _expand_label(initial, "client in", 32)
        lk, liv, lhp = LABELS[version]
        keys = (AESGCM(_expand_label(client, lk, 16)), _expand_label(client, liv, 12), _expand_label(client, lhp, 16))
        if len(_key_cache) > 1024:
            _key_cache.clear()
        _key_cache[k] = keys
    return keys


def _varint(b, off):
    first = b[off]
    n = 1 << (first >> 6)
    v = first & 0x3F
    for i in range(1, n):
        v = (v << 8) | b[off + i]
    return v, off + n


def _decrypt_initial(data):
    """Decrypt the first (Initial) packet in a datagram. Returns (dcid, plaintext) or None."""
    if len(data) < 7 or not data[0] & 0x80:
        return None
    version = struct.unpack_from("!I", data, 1)[0]
    if version not in SALTS or ((data[0] & 0x30) >> 4) != INITIAL_TYPE[version]:
        return None
    off = 5
    dlen = data[off]
    dcid = data[off + 1:off + 1 + dlen]
    off += 1 + dlen
    off += 1 + data[off]                     # SCID
    tlen, off = _varint(data, off)           # token
    off += tlen
    length, off = _varint(data, off)
    pn_off = off
    if pn_off + 20 > len(data) or pn_off + length > len(data):
        return None
    aead, iv, hp = _client_keys(version, dcid)
    sample = data[pn_off + 4:pn_off + 20]
    enc = Cipher(algorithms.AES(hp), modes.ECB()).encryptor()
    mask = enc.update(sample) + enc.finalize()
    first = data[0] ^ (mask[0] & 0x0F)
    pn_len = (first & 0x03) + 1
    pn = bytes(data[pn_off + i] ^ mask[1 + i] for i in range(pn_len))
    header = bytes([first]) + data[1:pn_off] + pn
    nonce = bytearray(iv)
    for i, b in enumerate(pn.rjust(12, b"\x00")):
        nonce[i] ^= b
    try:
        plain = aead.decrypt(bytes(nonce), data[pn_off + pn_len:pn_off + length], header)
    except Exception:
        return None
    return dcid, plain


def _crypto_frames(plain):
    """Yield (offset, data) for CRYPTO frames; skip PADDING/PING/ACK; stop at anything else."""
    off = 0
    n = len(plain)
    while off < n:
        ftype = plain[off]
        if ftype == 0x00 or ftype == 0x01:
            off += 1
        elif ftype in (0x02, 0x03):
            off += 1
            _, off = _varint(plain, off)          # largest acknowledged
            _, off = _varint(plain, off)          # delay
            count, off = _varint(plain, off)
            _, off = _varint(plain, off)          # first range
            for _ in range(count):
                _, off = _varint(plain, off)
                _, off = _varint(plain, off)
            if ftype == 0x03:
                for _ in range(3):
                    _, off = _varint(plain, off)
        elif ftype == 0x06:
            off += 1
            coff, off = _varint(plain, off)
            clen, off = _varint(plain, off)
            yield coff, plain[off:off + clen]
            off += clen
        else:
            return


class Reassembler:
    """Collects CRYPTO data per (client, dcid) until a ClientHello parses."""

    def __init__(self):
        self.pending = {}   # key -> [first_seen, {offset: bytes}]
        self.done = {}      # key -> time, so later Initials (retransmits) are ignored

    def feed(self, client, payload, now=None):
        """Feed one UDP payload from a client. Returns tls.Hello once complete, else None."""
        now = now or time.time()
        try:
            res = _decrypt_initial(payload)
        except (IndexError, struct.error):
            return None
        if res is None:
            return None
        dcid, plain = res
        key = (client, dcid)
        if key in self.done:
            return None
        entry = self.pending.get(key)
        if entry is None:
            if len(self.pending) >= MAX_PENDING:
                self._expire(now, force=True)
            entry = self.pending[key] = [now, {}]
        try:
            for coff, cdata in _crypto_frames(plain):
                if coff < MAX_CRYPTO:
                    entry[1][coff] = cdata
        except (IndexError, struct.error):
            pass
        stream = self._contiguous(entry[1])
        if len(stream) >= 4:
            need = 4 + int.from_bytes(stream[1:4], "big")
            hello = tls.parse_handshake(stream)
            if hello and (hello.sni or len(stream) >= need):
                del self.pending[key]
                self.done[key] = now
                return hello
        self._expire(now)
        return None

    @staticmethod
    def _contiguous(chunks):
        out = bytearray()
        while True:
            # chunks may overlap (retransmits); take any chunk that covers the current end
            nxt = None
            for o, d in chunks.items():
                if o <= len(out) < o + len(d):
                    nxt = d[len(out) - o:]
                    break
            if nxt is None:
                return bytes(out)
            out += nxt

    def _expire(self, now, force=False):
        if not force and len(self.pending) < 256 and len(self.done) < 4096:
            return
        for k in [k for k, v in self.pending.items() if now - v[0] > PENDING_TTL or force]:
            del self.pending[k]
        for k in [k for k, t in self.done.items() if now - t > 60]:
            del self.done[k]
