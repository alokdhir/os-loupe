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

"""Build synthetic TLS ClientHellos and QUIC Initials for tests (no captured traffic in this repo)."""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "opnsense", "scripts", "loupe"))


def ext(etype, body):
    return struct.pack("!HH", etype, len(body)) + body


def sni_ext(name):
    n = name.encode()
    entry = b"\x00" + struct.pack("!H", len(n)) + n
    return ext(0, struct.pack("!H", len(entry)) + entry)


def alpn_ext(*protos):
    lst = b"".join(bytes([len(p)]) + p.encode() for p in protos)
    return ext(16, struct.pack("!H", len(lst)) + lst)


def client_hello(name, padding_before=0, extra=()):
    """Handshake message (type 1). padding_before puts a big extension ahead of SNI,
    like a post-quantum key share in a randomized extension order."""
    exts = b""
    if padding_before:
        exts += ext(0x0033, os.urandom(padding_before))
    exts += sni_ext(name) + alpn_ext("h2", "http/1.1") + b"".join(extra)
    body = (b"\x03\x03" + os.urandom(32) + b"\x20" + os.urandom(32)
            + struct.pack("!H", 4) + b"\x13\x01\x13\x02" + b"\x01\x00"
            + struct.pack("!H", len(exts)) + exts)
    return b"\x01" + len(body).to_bytes(3, "big") + body


def tls_record(hs):
    return b"\x16\x03\x01" + struct.pack("!H", len(hs)) + hs
