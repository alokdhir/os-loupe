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

"""Ethernet / IPv4 / IPv6 / TCP / UDP decoding — just enough for loupe's parsers."""
import ipaddress
import struct
from typing import NamedTuple, Optional

ETH_IP4 = 0x0800
ETH_IP6 = 0x86DD
ETH_VLAN = (0x8100, 0x88A8)
PROTO_TCP = 6
PROTO_UDP = 17
IP6_EXT = (0, 43, 60)  # hop-by-hop, routing, destination options (fragments are skipped)


class Packet(NamedTuple):
    ts: float
    src_mac: str
    dst_mac: str
    src: str
    dst: str
    proto: int
    sport: int
    dport: int
    payload: bytes
    seq: int = 0
    flags: int = 0


def mac_str(b):
    return ":".join(f"{x:02x}" for x in b)


def decode(ts, frame) -> Optional[Packet]:
    if len(frame) < 14:
        return None
    dst_mac, src_mac = frame[0:6], frame[6:12]
    etype = struct.unpack_from("!H", frame, 12)[0]
    off = 14
    while etype in ETH_VLAN and len(frame) >= off + 4:
        etype = struct.unpack_from("!H", frame, off + 2)[0]
        off += 4

    if etype == ETH_IP4:
        if len(frame) < off + 20:
            return None
        vihl = frame[off]
        ihl = (vihl & 0x0F) * 4
        total = struct.unpack_from("!H", frame, off + 2)[0]
        frag = struct.unpack_from("!H", frame, off + 6)[0]
        if frag & 0x1FFF:  # non-first fragment: no transport header
            return None
        proto = frame[off + 9]
        src = str(ipaddress.IPv4Address(frame[off + 12:off + 16]))
        dst = str(ipaddress.IPv4Address(frame[off + 16:off + 20]))
        end = min(len(frame), off + total) if total else len(frame)
        off += ihl
    elif etype == ETH_IP6:
        if len(frame) < off + 40:
            return None
        plen = struct.unpack_from("!H", frame, off + 4)[0]
        proto = frame[off + 6]
        src = str(ipaddress.IPv6Address(frame[off + 8:off + 24]))
        dst = str(ipaddress.IPv6Address(frame[off + 24:off + 40]))
        end = min(len(frame), off + 40 + plen)
        off += 40
        while proto in IP6_EXT and off + 2 <= end:
            proto, hlen = frame[off], (frame[off + 1] + 1) * 8
            off += hlen
    else:
        return None

    if proto == PROTO_TCP:
        if end < off + 20:
            return None
        sport, dport, seq = struct.unpack_from("!HHI", frame, off)
        doff = (frame[off + 12] >> 4) * 4
        flags = frame[off + 13]
        payload = frame[off + doff:end]
        return Packet(ts, mac_str(src_mac), mac_str(dst_mac), src, dst, proto, sport, dport, bytes(payload), seq, flags)
    elif proto == PROTO_UDP:
        if end < off + 8:
            return None
        sport, dport = struct.unpack_from("!HH", frame, off)
        payload = frame[off + 8:end]
    else:
        return None
    return Packet(ts, mac_str(src_mac), mac_str(dst_mac), src, dst, proto, sport, dport, bytes(payload))
