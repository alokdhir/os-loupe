#!/usr/local/bin/python3
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
