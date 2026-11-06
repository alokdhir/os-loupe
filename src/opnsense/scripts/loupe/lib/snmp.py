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



"""A small read-only SNMPv2c client: GET and walk over UDP, stdlib only. What comes back is from the network, so every
length is checked and a reply that doesn't parse counts as no reply."""
import os
import socket
import time

TIMEOUT = 2.0           # seconds to wait for one reply
RETRIES = 1
REPETITIONS = 25        # rows asked for in one GETBULK
MAX_ROWS = 50000        # a walk stops here, whatever a switch keeps sending
GET, GETNEXT, RESPONSE, GETBULK = 0xa0, 0xa1, 0xa2, 0xa5
INTEGER, OCTETS, NULL, OID, SEQUENCE = 0x02, 0x04, 0x05, 0x06, 0x30
NUMBERS = {INTEGER, 0x41, 0x42, 0x43, 0x46}     # integer, counter, gauge, time ticks, counter64
NO_VALUE = {0x80, 0x81, 0x82}                   # noSuchObject, noSuchInstance, endOfMibView
TOO_BIG = 1
MAX_SIZE = 65535


class SnmpError(Exception):
    """No usable answer."""


def oid_tuple(text):
    return tuple(int(p) for p in text.strip(".").split("."))


def _length(n):
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _tlv(tag, body):
    return bytes([tag]) + _length(len(body)) + body


def _integer(n):
    return _tlv(INTEGER, n.to_bytes(n.bit_length() // 8 + 1, "big", signed=True))


def _base128(n):
    out = [n & 0x7f]
    while n > 0x7f:
        n >>= 7
        out.insert(0, 0x80 | (n & 0x7f))
    return bytes(out)


def _encode_oid(oid):
    if len(oid) < 2:
        raise ValueError("an OID has at least two parts")
    return _tlv(OID, _base128(oid[0] * 40 + oid[1]) + b"".join(_base128(p) for p in oid[2:]))


def request(kind, request_id, community, oids, repetitions=0):
    """An SNMPv2c request. GETBULK's two integers sit where the others have error status and index."""
    binds = b"".join(_tlv(SEQUENCE, _encode_oid(o) + _tlv(NULL, b"")) for o in oids)
    pdu = _integer(request_id) + _integer(0) + _integer(repetitions) + _tlv(SEQUENCE, binds)
    return _tlv(SEQUENCE, _integer(1) + _tlv(OCTETS, community.encode()) + _tlv(kind, pdu))


def _read(data, pos, end):
    """One TLV in data[pos:end]: (tag, start of its value, end of its value)."""
    if pos + 2 > end:
        raise SnmpError("reply cut short")
    tag, n = data[pos], data[pos + 1]
    pos += 2
    if n & 0x80:
        size = n & 0x7f
        if not 1 <= size <= 4 or pos + size > end:
            raise SnmpError("bad length in reply")
        n = int.from_bytes(data[pos:pos + size], "big")
        pos += size
    if pos + n > end:
        raise SnmpError("reply cut short")
    return tag, pos, pos + n


def _expect(data, pos, end, tag):
    got, start, stop = _read(data, pos, end)
    if got != tag:
        raise SnmpError("unexpected field in reply")
    return start, stop


def _decode_oid(body):
    if not body:
        raise SnmpError("empty OID in reply")
    parts = []
    n = 0
    for i, b in enumerate(body):
        n = (n << 7) | (b & 0x7f)
        if not b & 0x80:
            parts.append(n)
            n = 0
        elif i == len(body) - 1 or n >= 1 << 32:
            raise SnmpError("bad OID in reply")
    first = parts[0]
    head = (min(first // 40, 2), first - 40 * min(first // 40, 2))
    return head + tuple(parts[1:])


def parse(data):
    """A reply: (request id, error status, [(oid, value)]). Numbers are ints, strings bytes, OIDs tuples; a value that
    isn't there (noSuchObject, end of the MIB) is None."""
    start, stop = _expect(data, 0, len(data), SEQUENCE)
    a, b = _expect(data, start, stop, INTEGER)
    a, b = _expect(data, b, stop, OCTETS)
    tag, a, stop = _read(data, b, stop)
    if tag != RESPONSE:
        raise SnmpError("not a response")
    numbers = []
    pos = a
    for _ in range(3):
        a, pos = _expect(data, pos, stop, INTEGER)
        numbers.append(int.from_bytes(data[a:pos], "big", signed=True))
    a, end = _expect(data, pos, stop, SEQUENCE)
    binds = []
    while a < end:
        b, a = _expect(data, a, end, SEQUENCE)
        c, d = _expect(data, b, a, OID)
        oid = _decode_oid(data[c:d])
        tag, c, d = _read(data, d, a)
        if tag in NUMBERS:
            binds.append((oid, int.from_bytes(data[c:d], "big", signed=tag == INTEGER)))
        elif tag == OID:
            binds.append((oid, _decode_oid(data[c:d])))
        elif tag in NO_VALUE or tag == NULL:
            binds.append((oid, None))
        else:
            binds.append((oid, bytes(data[c:d])))
    return numbers[0], numbers[1], binds


class Client:
    """One switch. `deadline` (time.monotonic) ends everything, however many small answers a slow switch gives."""

    def __init__(self, host, community, deadline=None, timeout=TIMEOUT, retries=RETRIES):
        self.community = community
        self.deadline = deadline
        self.timeout = timeout
        self.retries = retries
        family, kind, proto, _, addr = socket.getaddrinfo(host, 161, type=socket.SOCK_DGRAM, flags=socket.AI_NUMERICHOST)[0]
        self.sock = socket.socket(family, kind, proto)
        self.sock.connect(addr)         # only this address's replies reach the socket

    def close(self):
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _ask(self, kind, oids, repetitions=0):
        request_id = int.from_bytes(os.urandom(3), "big")
        packet = request(kind, request_id, self.community, oids, repetitions)
        for _ in range(self.retries + 1):
            try:
                self.sock.send(packet)
            except OSError as e:
                raise SnmpError(e.strerror or str(e)) from None
            wait_until = time.monotonic() + self.timeout
            while True:
                left = wait_until - time.monotonic()
                if self.deadline is not None:
                    if time.monotonic() >= self.deadline:
                        raise SnmpError("took too long")
                    left = min(left, self.deadline - time.monotonic())
                if left <= 0:
                    break
                self.sock.settimeout(left)
                try:
                    reply = self.sock.recv(MAX_SIZE)
                except socket.timeout:
                    break
                except OSError as e:           # ICMP unreachable, on a connected socket
                    raise SnmpError(e.strerror or str(e)) from None
                try:
                    got, status, binds = parse(reply)
                except (SnmpError, ValueError, IndexError):
                    continue                    # not an answer: keep waiting for one
                if got == request_id:
                    return status, binds
        raise SnmpError("no answer")

    def get(self, oid):
        """The value at an OID, or None if the switch doesn't have it."""
        status, binds = self._ask(GET, [oid])
        if status or not binds:
            return None
        return binds[0][1]

    def walk(self, base):
        """Every row under an OID: {index after base, as a tuple: value}. Follows the table for as long as the OIDs
        increase, since a switch that repeats itself would otherwise never finish."""
        rows = {}
        at = base
        size = len(base)
        repetitions = REPETITIONS
        while len(rows) < MAX_ROWS:
            status, binds = self._ask(GETBULK, [at], repetitions)
            if status == TOO_BIG and repetitions > 1:
                repetitions //= 2
                continue
            if status or not binds:
                return rows
            for oid, value in binds:
                if oid[:size] != base or value is None or oid <= at:
                    return rows
                rows[oid[size:]] = value
                at = oid
        return rows
