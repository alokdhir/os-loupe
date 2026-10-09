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
