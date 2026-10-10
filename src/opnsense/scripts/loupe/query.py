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

"""Report queries for the GUI (called through configd). Prints JSON.

usage:
  query.py devices HOURS
  query.py device IP HOURS
  query.py lookup TEXT HOURS
  query.py widget
"""
import collections
import ipaddress
import json
import sqlite3
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from lib.devid import is_private  # noqa: E402
from lib.services import PORTS, ServiceMap, vpn_provider  # noqa: E402

DB = "/var/db/loupe/loupe.db"
CONF = "/usr/local/etc/loupe.json"
MAX_HOURS = 24 * 3650


def load():
    try:
        with open(CONF) as f:
            conf = json.load(f)
    except (OSError, ValueError):
        conf = {}
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    return db, ServiceMap(conf.get("services")), {k.lower(): v for k, v in conf.get("devices", {}).items()}


def window(hours):
    """(table, start, bucket size): 5-minute detail while it is kept, hourly beyond."""
    now = time.time()
    start = int(now - hours * 3600)
    if hours <= 48:
        return "flows_5m", start, 300
    return ("flows_5m", start, 3600) if hours <= 24 * 30 else ("flows_1h", start, 86400 if hours > 24 * 90 else 3600)


APPLE_TYPES = {"iPhone", "iPad", "Mac", "Watch", "Apple TV", "HomePod"}


def vendor_of(mac, vendor, dtype):
    """(vendor, note). The MAC prefix names the maker unless the MAC is private (randomized) or newer than
    OPNsense's copy of the IEEE list; then an Apple device type still tells us."""
    if vendor:
        return vendor, ""
    why = "private MAC" if is_private(mac) else "MAC prefix not in the vendor list"
    if dtype in APPLE_TYPES:
        return "Apple", f"from device type ({why})"
    return "", why


def devices_info(db, overrides):
    out = {}
    for mac, ip, name, hostname, vendor, dtype, src, model, first, last in db.execute(
            "SELECT mac, ip, name, hostname, vendor, type, type_source, model, first_seen, last_seen FROM devices"):
        o = overrides.get(mac, {})
        vendor, vnote = vendor_of(mac, vendor, o.get("type") or dtype)
        out[mac] = {"mac": mac, "ip": ip, "name": o.get("name") or name or hostname or "", "vendor": vendor,
                    "vendor_note": vnote,
                    "model": model or "",
                    "type": o.get("type") or dtype or "", "type_source": "set by you" if o.get("type") else (src or ""),
                    "first_seen": first, "last_seen": last, "custom": bool(o)}
    for mac, o in overrides.items():   # named before loupe recorded the device
        out.setdefault(mac, {"mac": mac, "ip": "", "name": o.get("name") or "", "vendor": "", "vendor_note": "", "type": o.get("type") or "",
                             "type_source": "set by you" if o.get("type") else "", "model": "", "first_seen": 0, "last_seen": 0,
                             "custom": True})
    return out


VPN_PORTS = [(proto, port) for (proto, port), s in PORTS.items() if s.startswith("VPN (")]


class Labels:
    """Service names for one report window, with VPN tunnels told apart.

    A device that looked up a VPN provider's domain gets "VPN (NordVPN)" instead of "VPN (WireGuard)", and other
    nameless traffic to a server it uses as a VPN (e.g. a TCP fallback) is counted as that VPN too.
    """

    def __init__(self, db, svc, table, start):
        self.svc = svc
        self.provider = {}
        counts = collections.defaultdict(collections.Counter)
        for ip, name, n in db.execute("SELECT ip, name, sum(count) FROM lookups WHERE day >= ? AND last_seen >= ? "
                                      "GROUP BY ip, name", (start - 86400, start - 86400)):
            p = vpn_provider(name)
            if p:
                counts[ip][p] += n
        self.provider = {ip: c.most_common(1)[0][0] for ip, c in counts.items()}
        self.tunnels = {}
        cond = " OR ".join("(proto = ? AND port = ?)" for _ in VPN_PORTS)
        for ip, server, proto, port in db.execute(
                f"SELECT DISTINCT ip, server, proto, port FROM {table} WHERE bucket >= ? AND name = '' AND ({cond})",
                (start, *[x for pp in VPN_PORTS for x in pp])):
            self.tunnels[(ip, server)] = PORTS[(proto, port)]

    def service(self, ip, name, server, port, proto):
        s = self.svc.by_name(name)
        if s:
            return s
        s = self.tunnels.get((ip, server)) or self.svc.service(name, server, port, proto)
        if s and s.startswith("VPN (") and ip in self.provider:
            return f"VPN ({self.provider[ip]})"
        return s


def label(dev, ip):
    return (dev or {}).get("name") or ip


def cmd_devices(hours):
    db, svc, ov = load()
    table, start, _ = window(hours)
    info = devices_info(db, ov)
    lab = Labels(db, svc, table, start)
    rows = {}
    tops = collections.defaultdict(collections.Counter)
    for ip, mac, name, server, port, proto, up, down, conns, last in db.execute(
            f"SELECT ip, mac, name, server, port, proto, sum(up), sum(down), sum(conns), max(bucket) FROM {table} "
            f"WHERE bucket >= ? GROUP BY ip, mac, name, server, port, proto", (start,)):
        key = mac or ip
        r = rows.get(key)
        if r is None:
            d = info.get(mac, {})
            r = rows[key] = {**{k: d.get(k, "") for k in ("name", "vendor", "vendor_note", "type", "type_source", "model")},
                             "custom": d.get("custom", False),
                             "mac": mac, "ip": ip, "down": 0, "up": 0, "conns": 0, "last": 0}
        r["down"] += down
        r["up"] += up
        r["conns"] += conns
        r["last"] = max(r["last"], last)
        tops[key][lab.service(ip, name, server, port, proto) or ""] += up + down   # "" = no name: shown as a grey dash
    for key, r in rows.items():
        r["top"] = [s for s, _ in tops[key].most_common(3)]
        if not r["name"]:
            r["name"] = r["ip"]
    return {"hours": hours, "rows": sorted(rows.values(), key=lambda r: -(r["down"] + r["up"]))}


def device_filter(arg):
    """A device is chosen by MAC (stable) or IP."""
    return ("mac", arg.lower()) if arg.count(":") == 5 and len(arg) == 17 else ("ip", str(ipaddress.ip_address(arg)))


def cmd_device(arg, hours):
    db, svc, ov = load()
    col, val = device_filter(arg)
    table, start, step = window(hours)
    info = devices_info(db, ov)
    lab = Labels(db, svc, table, start)
    dev = None
    if col == "mac":
        dev = info.get(val)
    else:
        dev = next((d for d in info.values() if d["ip"] == val), None)
    services = collections.defaultdict(lambda: {"down": 0, "up": 0, "conns": 0, "names": collections.Counter()})
    sites = collections.defaultdict(lambda: {"down": 0, "up": 0, "conns": 0, "last": 0, "service": "", "servers": set()})
    ports = collections.defaultdict(lambda: {"down": 0, "up": 0, "conns": 0})
    timeline = collections.defaultdict(lambda: [0, 0])
    for bucket, ip, name, server, port, proto, inbound, up, down, conns in db.execute(
            f"SELECT bucket, ip, name, server, port, proto, inbound, up, down, conns FROM {table} "
            f"WHERE {col} = ? AND bucket >= ?", (val, start)):
        s = lab.service(ip, name, server, port, proto) or "Other"
        sv = services[s]
        sv["down"] += down
        sv["up"] += up
        sv["conns"] += conns
        sv["names"][name or server] += up + down
        site = sites[name or server]
        site["down"] += down
        site["up"] += up
        site["conns"] += conns
        site["last"] = max(site["last"], bucket)
        site["service"] = s
        site["servers"].add(server)
        p = ports[f"{proto}/{port}" + (" in" if inbound else "")]
        p["down"] += down
        p["up"] += up
        p["conns"] += conns
        tl = timeline[bucket - bucket % step]
        tl[0] += down
        tl[1] += up
    now = int(time.time())
    first = start - start % step
    return {
        "hours": hours, "device": dev or {"ip": val if col == "ip" else "", "mac": val if col == "mac" else ""},
        "services": sorted(({"service": k, "down": v["down"], "up": v["up"], "conns": v["conns"],
                             "names": [n for n, _ in v["names"].most_common(5)]} for k, v in services.items()),
                           key=lambda r: -(r["down"] + r["up"])),
        "sites": sorted(({"name": k, **{x: v[x] for x in ("down", "up", "conns", "last", "service")},
                          "servers": sorted(v["servers"])[:5]} for k, v in sites.items()),
                        key=lambda r: -(r["down"] + r["up"]))[:500],
        "ports": sorted(({"port": k, **v} for k, v in ports.items()), key=lambda r: -(r["down"] + r["up"]))[:50],
        "step": step,
        "timeline": [[b, *timeline.get(b, [0, 0])] for b in range(first, now + 1, step)],
    }


def cmd_lookup(text, hours):
    db, svc, ov = load()
    table, start, _ = window(hours)
    info = devices_info(db, ov)
    lab = Labels(db, svc, table, start)
    text = text.strip().lower()
    try:
        ipaddress.ip_address(text)
        where, arg = "server = ?", text
        lwhere = None
    except ValueError:
        where, arg = "name LIKE ? ESCAPE '\\'", "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        lwhere = arg
    rows = collections.defaultdict(lambda: {"down": 0, "up": 0, "conns": 0, "first": None, "last": 0, "servers": set()})
    for ip, mac, name, server, port, proto, up, down, conns, first, last in db.execute(
            f"SELECT ip, mac, name, server, port, proto, sum(up), sum(down), sum(conns), min(bucket), max(bucket) "
            f"FROM {table} WHERE bucket >= ? AND {where} GROUP BY ip, mac, name, server, port, proto LIMIT 20000",
            (start, arg)):
        r = rows[(mac or ip, name)]
        r.update(ip=ip, mac=mac, name=name, service=lab.service(ip, name, server, port, proto) or "")
        r["down"] += down
        r["up"] += up
        r["conns"] += conns
        r["first"] = first if r["first"] is None else min(r["first"], first)
        r["last"] = max(r["last"], last)
        r["servers"].add(server)
    looked = []
    if lwhere:
        day0 = start - 86400
        for ip, name, source, count, first, last in db.execute(
                "SELECT ip, name, source, sum(count), min(first_seen), max(last_seen) FROM lookups "
                "WHERE day >= ? AND last_seen >= ? AND name LIKE ? ESCAPE '\\' GROUP BY ip, name, source "
                "ORDER BY 6 DESC LIMIT 2000", (day0, start, lwhere)):
            dev = next((d for d in info.values() if d["ip"] == ip), None)
            looked.append({"ip": ip, "device": label(dev, ip), "type": (dev or {}).get("type", ""), "name": name,
                           "source": source, "count": count, "first": first, "last": last})
    out = []
    for (key, _name), r in rows.items():
        dev = info.get(r["mac"]) or next((d for d in info.values() if d["ip"] == r["ip"]), None)
        out.append({**{k: r[k] for k in ("ip", "mac", "name", "service", "down", "up", "conns", "first", "last")},
                    "device": label(dev, r["ip"]), "type": (dev or {}).get("type", ""),
                    "servers": sorted(r["servers"])[:5]})
    return {"hours": hours, "query": text, "traffic": sorted(out, key=lambda r: -(r["down"] + r["up"])),
            "lookups": looked}


def cmd_widget():
    db, _svc, _ov = load()
    now = int(time.time())
    spark = collections.defaultdict(lambda: [0, 0])
    for bucket, up, down in db.execute(
            "SELECT bucket, sum(up), sum(down) FROM flows_1h WHERE bucket >= ? GROUP BY bucket", (now - 86400,)):
        spark[bucket] = [down, up]
    first = (now - 86400) // 3600 * 3600
    hours = [[b, *spark.get(b, [0, 0])] for b in range(first, now + 1, 3600)]
    top = cmd_devices(24)["rows"][:8]
    return {"hours": hours, "down": sum(h[1] for h in hours), "up": sum(h[2] for h in hours), "top": top}


def hours_arg(v):
    h = float(v)
    if not 0 < h <= MAX_HOURS:
        raise ValueError("hours out of range")
    return h


def main(argv):
    try:
        cmd = argv[1] if len(argv) > 1 else ""
        if cmd == "devices":
            out = cmd_devices(hours_arg(argv[2]))
        elif cmd == "device":
            out = cmd_device(argv[2], hours_arg(argv[3]))
        elif cmd == "lookup":
            out = cmd_lookup(argv[2], hours_arg(argv[3]))
        elif cmd == "widget":
            out = cmd_widget()
        else:
            out = {"error": "unknown command"}
    except (IndexError, ValueError) as e:
        out = {"error": str(e)}
    except sqlite3.Error as e:
        out = {"error": f"database: {e}"}
    print(json.dumps(out, default=list))


if __name__ == "__main__":
    main(sys.argv)
