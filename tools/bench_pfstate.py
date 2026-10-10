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

"""Time pfstate.parse on a synthetic `pfctl -ss -vv` dump (documentation addresses only).

usage: tools/bench_pfstate.py [CONNECTIONS]     (each connection = a LAN-side and a WAN-side state)
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "opnsense", "scripts", "loupe"))
from lib import pfstate  # noqa: E402


def synthetic(n):
    out = ["No ALTQ support in kernel"]
    for i in range(n):
        dev = f"10.9.{i % 200}.{i % 250 + 1}"
        srv = f"203.0.113.{i % 254 + 1}"
        port = 1024 + i % 60000
        sid = 2 * i
        if i % 3 == 2:   # some UDP over IPv6
            out += [f"all udp 2001:db8::{i % 9999:x}[443] <- 2001:db8:1::{i % 9999:x}[{port}]       MULTIPLE:MULTIPLE",
                    f"   age 00:01:{i % 60:02d}, expires in 00:00:30, {i % 97}:{i % 89} pkts, {i * 7}:{i * 13} bytes, rule 90",
                    f"   id: {sid:016x} creatorid: aaaa0001",
                    "   origif: bridge0"]
            continue
        out += [f"all tcp {srv}:443 <- {dev}:{port}       ESTABLISHED:ESTABLISHED",
                "   [1 + 2] wscale 7  [3 + 4] wscale 6",
                f"   age 00:{i % 60:02d}:05, expires in 23:59:55, {i % 97}:{i % 89} pkts, {i * 7}:{i * 13} bytes, rule 83",
                f"   id: {sid:016x} creatorid: aaaa0001",
                "   origif: bridge0",
                f"all tcp 198.51.100.7:{port} ({dev}:{port}) -> {srv}:443       ESTABLISHED:ESTABLISHED",
                "   [3 + 4] wscale 6  [1 + 2] wscale 7",
                f"   age 00:{i % 60:02d}:05, expires in 23:59:55, {i % 97}:{i % 89} pkts, {i * 7}:{i * 13} bytes, rule 79",
                f"   id: {sid + 1:016x} creatorid: aaaa0001",
                "   origif: ix1"]
    return "\n".join(out) + "\n"


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 25000
    text = synthetic(n)
    is_local = pfstate.local_matcher(["10.9.0.0/16", "2001:db8:1::/64"])
    best = None
    for _ in range(3):
        t = time.perf_counter()
        states = pfstate.parse(text, is_local, {"bridge0"})
        best = min(best or 1e9, time.perf_counter() - t)
    lines = text.count("\n")
    print(f"{n} connections, {lines} lines, {len(text) / 1e6:.1f} MB: {best * 1000:.0f} ms, "
          f"{best / lines * 4 * 1e6:.1f} us/state, {len(states)} kept")


if __name__ == "__main__":
    main()
