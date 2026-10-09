"""TLS ClientHello parsing: SNI, ALPN, and whether Encrypted Client Hello is in use."""
import struct
from typing import NamedTuple, Optional

EXT_SNI = 0x0000
EXT_ALPN = 0x0010
EXT_ECH = 0xFE0D


class Hello(NamedTuple):
    sni: Optional[str]
    alpn: tuple
    ech: bool        # SNI is then the outer "public name", not the real site
    complete: bool   # False: the hello was cut off before SNI could be found


def record_payload(data):
    """TLS record(s) from the first TCP segment -> handshake bytes (may be truncated)."""
    out = bytearray()
    off = 0
    while off + 5 <= len(data) and data[off] == 0x16:
        rlen = struct.unpack_from("!H", data, off + 3)[0]
        out += data[off + 5:off + 5 + rlen]
        off += 5 + rlen
    return bytes(out)


def parse_handshake(hs) -> Optional[Hello]:
    """Parse a handshake message that should be a ClientHello. Tolerates truncation."""
    if len(hs) < 4 or hs[0] != 0x01:
        return None
    body_len = int.from_bytes(hs[1:4], "big")
    body = hs[4:4 + body_len]
    truncated = len(body) < body_len
    try:
        off = 2 + 32                       # legacy_version, random
        off += 1 + body[off]               # session id
        off += 2 + struct.unpack_from("!H", body, off)[0]  # cipher suites
        off += 1 + body[off]               # compression methods
        ext_total = struct.unpack_from("!H", body, off)[0]
        off += 2
        end = min(len(body), off + ext_total)
    except (IndexError, struct.error):
        return Hello(None, (), False, False)

    sni, alpn, ech = None, (), False
    while off + 4 <= end:
        etype, elen = struct.unpack_from("!HH", body, off)
        ext = body[off + 4:off + 4 + elen]
        off += 4 + elen
        if len(ext) < elen:
            truncated = True
            break
        if etype == EXT_SNI and len(ext) >= 5:
            nlen = struct.unpack_from("!H", ext, 3)[0]
            if ext[2] == 0:
                sni = ext[5:5 + nlen].decode("ascii", "replace").lower()
        elif etype == EXT_ALPN and len(ext) >= 2:
            names, p = [], 2
            while p < len(ext):
                n = ext[p]
                names.append(ext[p + 1:p + 1 + n].decode("ascii", "replace"))
                p += 1 + n
            alpn = tuple(names)
        elif etype == EXT_ECH:
            ech = True
    return Hello(sni, alpn, ech, sni is not None or not truncated)


MAX_RECORD = 16384 + 256


def looks_like_hello(payload):
    """Cheap sanity checks so random bulk data that happens to start 16 03 .. .. .. 01 is rejected."""
    if len(payload) < 9 or payload[0] != 0x16 or payload[1] != 0x03 or payload[2] > 0x04 or payload[5] != 0x01:
        return False
    rlen = struct.unpack_from("!H", payload, 3)[0]
    hlen = int.from_bytes(payload[6:9], "big")
    return 4 <= rlen <= MAX_RECORD and 38 <= hlen <= 0xFFFF and payload[9:10] in (b"\x03", b"")


def parse_tcp_payload(payload) -> Optional[Hello]:
    if not looks_like_hello(payload):
        return None
    return parse_handshake(record_payload(payload))


def record_length(payload):
    """Total bytes of the first TLS record (header included), from its header."""
    return 5 + struct.unpack_from("!H", payload, 3)[0]
