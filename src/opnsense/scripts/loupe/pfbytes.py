#!/usr/local/bin/python3
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

"""Dev tool: poll pf states and print per-device up/down totals.

usage: pfbytes.py [--seconds N] [--interval S] [--json] IFACE [IFACE ...]
"""
import argparse
import collections
import json
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from lib import pfstate  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--interval", type=float, default=10)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("ifaces", nargs="+")
    args = ap.parse_args()
    is_local = pfstate.local_matcher(pfstate.interface_networks(args.ifaces))
    ifaces = set(args.ifaces)
    t = pfstate.Tracker(fresh=args.interval)
    dev = collections.defaultdict(lambda: [0, 0, 0])
    srv = collections.defaultdict(lambda: [0, 0])
    polls, cost, nstates = 0, 0.0, 0
    start = time.time()
    while True:
        t0 = time.process_time(), time.time()
        states = pfstate.snapshot(is_local, ifaces)
        for d in t.update(states):
            v = dev[d.state.local]
            v[0] += d.up
            v[1] += d.down
            v[2] += d.new
            s = srv[(d.state.local, d.state.remote, d.state.rport, d.state.proto)]
            s[0] += d.up
            s[1] += d.down
        polls += 1
        cost += time.time() - t0[1]
        nstates = len(states)
        if time.time() - start >= args.seconds:
            break
        time.sleep(max(0, args.interval - (time.time() - t0[1])))
    out = {"window": [start, time.time()], "polls": polls, "avg_poll_s": round(cost / polls, 4),
           "states": nstates, "devices": dev,
           "top": sorted(([*k, *v] for k, v in srv.items()), key=lambda r: -(r[4] + r[5]))[:10]}
    if args.json:
        print(json.dumps(out))
    else:
        for ip, (up, down, conns) in sorted(dev.items(), key=lambda kv: -kv[1][1]):
            print(f"{ip:40} down {down:>14,} up {up:>14,} new {conns}")
        print({k: out[k] for k in ("polls", "avg_poll_s", "states")})


if __name__ == "__main__":
    main()
