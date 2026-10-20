#!/usr/bin/env python3
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

"""Time decoding pf's states (netlink replies -> Loupe's view) on a synthetic table (documentation
addresses only). message() builds one GETSTATES reply; the tests use it too.

usage: tools/bench_pfstate.py [CONNECTIONS]     (each connection = a LAN-side and a WAN-side state)
"""
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "opnsense", "scripts", "loupe"))
from lib import pfnl, pfstate  # noqa: E402

PROTO = {"tcp": 6, "udp": 17, "ipv6-icmp": 58}


def message(sid, origif, inbound, left, right, proto="tcp", packets=(0, 0), bytes_=(0, 0), age=0):
    """One state as the kernel sends it. left/right: (addr, port) as pfctl prints the state
    ("left <- right" when inbound) - the stack key's address 1 and address 0."""
    A = pfnl._attr
    family = socket.AF_INET6 if ":" in left[0] else socket.AF_INET
    key = (A(pfnl.STK_ADDR0, socket.inet_pton(family, right[0])) + A(pfnl.STK_ADDR1, socket.inet_pton(family, left[0]))
           + A(pfnl.STK_PORT0, struct.pack("!H", right[1])) + A(pfnl.STK_PORT1, struct.pack("!H", left[1])))
    return (A(pfnl.ST_ID, struct.pack("Q", sid)) + A(pfnl.ST_CREATORID, struct.pack("I", 0xaaaa0001))
            + A(3, b"all\0") + A(pfnl.ST_ORIG_IFNAME, origif.encode() + b"\0") + A(pfnl.ST_KEY_STACK, key)
            + A(pfnl.ST_CREATION, struct.pack("I", age))
            + A(pfnl.ST_PACKETS0, struct.pack("Q", packets[0])) + A(pfnl.ST_PACKETS1, struct.pack("Q", packets[1]))
            + A(pfnl.ST_BYTES0, struct.pack("Q", bytes_[0])) + A(pfnl.ST_BYTES1, struct.pack("Q", bytes_[1]))
            + A(pfnl.ST_PROTO, bytes([PROTO[proto]])) + A(pfnl.ST_DIRECTION, bytes([pfnl.PF_IN if inbound else 2]))
            + A(40, b"rule label\0"))


def synthetic(n):
    """n connections: for each a LAN-side state on bridge0 and its NAT'd twin on ix1 (some UDP over IPv6)."""
    out = []
    for i in range(n):
        dev, srv, port, sid = f"10.9.{i % 200}.{i % 250 + 1}", f"203.0.113.{i % 254 + 1}", 1024 + i % 60000, 2 * i
        counts = dict(packets=(i % 97, i % 89), bytes_=(i * 7, i * 13), age=i % 3600)
        if i % 3 == 2:
            out.append(message(sid, "bridge0", True, (f"2001:db8::{i % 9999:x}", 443),
                               (f"2001:db8:1::{i % 9999:x}", port), "udp", **counts))
            continue
        out.append(message(sid, "bridge0", True, (srv, 443), (dev, port), **counts))
        out.append(message(sid + 1, "ix1", False, (dev, port), (srv, 443), **counts))
    return out


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 25000
    msgs = synthetic(n)
    is_local = pfstate.local_matcher(["10.9.0.0/16", "2001:db8:1::/64"])
    best = None
    for _ in range(3):
        t = time.perf_counter()
        states = pfstate.from_netlink(pfnl.parse_states(msgs, {"bridge0"}), is_local, {"bridge0"})
        best = min(best or 1e9, time.perf_counter() - t)
    print(f"{n} connections, {len(msgs)} states: {best * 1000:.0f} ms, {best / len(msgs) * 1e6:.1f} us/state, "
          f"{len(states)} kept")


if __name__ == "__main__":
    main()
