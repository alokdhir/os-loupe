#!/usr/bin/env python3
"""Compare Loupe with what the bench knows it sent (run on the host as root).

check.py [SINCE]   SINCE: unix time (default: the interface baseline in ifbase.txt)

Per client: bytes from its bakwas log (for the media server, also fakenet's viewer log, its streams being the
server's upload) (curl's counts: payload only, so Loupe should be a few % higher) next to
Loupe's per-minute rows for its MAC, the addresses Loupe saw for it and how its .fake names were learnt.
Per interface: the router's byte counters since the baseline next to Loupe's total. The counters also hold
traffic Loupe leaves out on purpose (the router's own DNS/DHCP/ND, traffic between clients).
"""
import datetime
import json
import os
import subprocess
import sys

D = os.path.dirname(os.path.abspath(__file__))
CLIENTS = {"loupe-c1": "den-laptop", "loupe-c2": "kids-tablet", "loupe-c3": "office-pc", "loupe-c4": "plex-server"}
IFACES = ("vtnet0", "vlan0.20", "vlan0.30")

ROUTER = r'''
import json, sqlite3, subprocess, sys
since = int(sys.argv[1]) // 60 * 60
db = sqlite3.connect("file:/var/db/loupe/loupe.db?mode=ro", uri=True)
macs = {}
for mac, ip, up, down, conns in db.execute(
        "select mac, ip, sum(up), sum(down), sum(conns) from flows_1m where bucket >= ? group by mac, ip", (since,)):
    m = macs.setdefault(mac, {"up": 0, "down": 0, "conns": 0, "ips": []})
    m["up"] += up; m["down"] += down; m["conns"] += conns; m["ips"].append(ip)
fake = {}
for mac, name, source, n in db.execute(
        "select mac, name, source, count(*) from flows_1m where bucket >= ? and server like '10.98.0.%' or "
        "bucket >= ? and server like '2001:db8:98::%' group by mac, name, source", (since, since)):
    fake.setdefault(mac, []).append([name or "?", source or "-", n])
counters = {}
for i in sys.argv[2:]:
    line = subprocess.run(["netstat", "-I", i, "-b", "-n", "-W"], capture_output=True, text=True).stdout.splitlines()[1].split()
    counters[i] = [int(line[7]), int(line[10])]
print(json.dumps({"macs": macs, "fake": fake, "counters": counters, "first": db.execute(
    "select min(bucket) from flows_1m").fetchone()[0]}))
'''


def sh(*cmd, stdin=None):
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True).stdout


def baseline():
    lines = open(os.path.join(D, "ifbase.txt")).read().split("\n")
    since, base = int(lines[0]), {}
    for line in lines:
        f = line.split()
        if f and f[0] in IFACES + ("vtnet1",):
            base[f[0]] = [int(f[7]), int(f[10])]
    return since, base


def client_log(ct, since, path="/var/log/bakwas.log", out=None, swap=False):
    """bytes by kind and family from the client's bakwas log, transfers that started at or after `since`
    (swap: a log written by the other end, its download being this client's upload)"""
    out = {} if out is None else out
    for line in sh("incus", "exec", ct, "--", "cat", path).splitlines():
        f = line.split()
        if len(f) < 6 or not f[2].startswith("rc="):
            continue
        if datetime.datetime.fromisoformat(f[0]).replace(tzinfo=datetime.timezone.utc).timestamp() < since:
            continue
        kv = dict(x.split("=", 1) for x in f if "=" in x and x.split("=", 1)[0] in ("down", "up"))
        if swap:
            kv = {"down": kv.get("up", 0), "up": kv.get("down", 0)}
        fam = "v6" if ":" in f[4] else "v4"
        k = out.setdefault(f[1], {"v4": [0, 0, 0], "v6": [0, 0, 0]})[fam]
        k[0] += int(kv.get("down", 0))
        k[1] += int(kv.get("up", 0))
        k[2] += 1
    return out


def mb(n):
    return f"{n / 1e6:9.1f}"


def main():
    since, base = baseline()
    if len(sys.argv) > 1:
        since = int(sys.argv[1])
    r = json.loads(sh(os.path.join(D, "r.sh"), "python3", "-", str(since), *IFACES, stdin=ROUTER))  # root's shell is csh
    macs = {}
    for ct in CLIENTS:
        mac = sh("incus", "config", "device", "get", ct, "eth0", "hwaddr").strip()
        macs[mac] = ct
    print(f"since {since} (Loupe's first minute {r['first']})\n")
    print(f"{'device':<12} {'mac':<18} {'log down':>9} {'log up':>9} {'loupe dn':>9} {'loupe up':>9}  dn %  addresses")
    for mac, ct in macs.items():
        log = client_log(ct, since)
        if CLIENTS[ct] == "plex-server":
            client_log("loupe-fakenet", since, "/var/log/viewers.log", log, swap=True)
        down = sum(v[f][0] for k, v in log.items() if k != "peer" for f in v)
        up = sum(v[f][1] for k, v in log.items() if k != "peer" for f in v)
        lo = r["macs"].get(mac, {"up": 0, "down": 0, "ips": []})
        pct = f"{100 * lo['down'] / down:5.1f}" if down else "    -"
        print(f"{CLIENTS[ct]:<12} {mac:<18} {mb(down)} {mb(up)} {mb(lo['down'])} {mb(lo['up'])} {pct}  {len(lo['ips'])}: "
              + " ".join(sorted(lo["ips"])))
        kinds = ", ".join(f"{k} {f} {v[f][2]}x" for k, v in sorted(log.items()) for f in v if v[f][2])
        print(f"{'':<12} transfers: {kinds}")
        names = sorted({(n, s) for n, s, _ in r["fake"].get(mac, [])})
        print(f"{'':<12} .fake names: " + ", ".join(f"{n} ({s})" for n, s in names))
    other = {m: v for m, v in r["macs"].items() if m not in macs}
    for m, v in other.items():
        print(f"UNEXPECTED device {m or '(no MAC)'}: down {mb(v['down'])} up {mb(v['up'])} " + " ".join(v["ips"]))
    print(f"\n{'interface':<10} {'to LAN':>9} {'from LAN':>9}   (router counters since baseline)")
    for i in IFACES:
        d = [r["counters"][i][k] - base[i][k] for k in (0, 1)]
        print(f"{i:<10} {mb(d[1])} {mb(d[0])}")
    lo_down = sum(v["down"] for v in r["macs"].values())
    lo_up = sum(v["up"] for v in r["macs"].values())
    print(f"{'loupe':<10} {mb(lo_down)} {mb(lo_up)}   (all devices; vtnet0 also carries the VLANs' frames)")


if __name__ == "__main__":
    main()
