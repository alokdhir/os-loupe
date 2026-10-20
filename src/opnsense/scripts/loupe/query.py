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
  query.py sites HOURS [TEXT]
  query.py lookup TEXT HOURS [exact]
  query.py widget
"""
import collections
import ipaddress
import json
import re
import sqlite3
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from lib import netif  # noqa: E402
from lib.devid import is_private, loose, rules, set_user_rules  # noqa: E402
from lib.report import Namer, add, build_day, days, local_day, pieces, recent_rows  # noqa: E402
from lib.services import ServiceMap  # noqa: E402

DB = "/var/db/loupe/loupe.db"
CONF = "/usr/local/etc/loupe.json"
MAX_HOURS = 24 * 3650


class Networks:
    """The networks Loupe watches (loupe.json "networks": interface id, device and description, in the settings'
    order) and which of them an address is in, by the addresses those interfaces have now."""

    def __init__(self, conf):
        nets = [n for n in conf.get("networks", []) if n.get("id")]
        self.list = [{"id": n["id"], "name": n.get("name") or n["id"]} for n in nets]
        ids = {n["device"]: n["id"] for n in nets if n.get("device")}
        try:
            self.subnets = [(i.network, ids[dev]) for dev, i in netif.addresses(set(ids)) if not i.ip.is_link_local]
        except OSError:
            self.subnets = []
        self.order = {n["id"]: i for i, n in enumerate(self.list)}
        self.cache = {}

    def of(self, ip):
        """The id of the network `ip` is in, else ''."""
        if ip not in self.cache:
            try:
                a = ipaddress.ip_address(ip)
                self.cache[ip] = next((nid for net, nid in self.subnets if a.version == net.version and a in net), "")
            except ValueError:
                self.cache[ip] = ""
        return self.cache[ip]

    def sort(self, ids):
        return sorted((i for i in ids if i), key=lambda i: self.order.get(i, len(self.order)))

    def shown(self):
        """Networks to show (a column, a filter): only when there is more than one."""
        return self.list if len(self.list) > 1 else []


nets = Networks({})


def wanted(arg):
    """A filter argument: comma-separated network ids; None (empty) is all of them."""
    return {x for x in (arg or "").split(",") if x} or None


def load():
    global nets
    try:
        with open(CONF) as f:
            conf = json.load(f)
    except (OSError, ValueError):
        conf = {}
    set_user_rules(conf.get("rules", []))          # their icons
    nets = Networks(conf)
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    return db, ServiceMap(conf.get("services")), {k.lower().replace("-", ":"): v for k, v in conf.get("devices", {}).items()}


def window(hours):
    """(table, start, bucket size): minutes up to 3 hours, 5-minute detail up to 2 days, the hourly table
    beyond (longer periods are charted by the hour anyway, and it holds the whole history whatever the
    5-minute retention is set to)."""
    now = time.time()
    start = int(now - hours * 3600)
    table, step = ("flows_1m", 60) if hours <= 3 else ("flows_5m", 300) if hours <= 48 else \
        ("flows_1h", 86400 if hours > 24 * 90 else 3600)
    bucket = 60 if table == "flows_1m" else 300 if table == "flows_5m" else 3600
    return table, start - start % bucket, step      # whole buckets: the first one isn't cut short


def vendor_of(mac, vendor, dtype):
    """(vendor, note). The MAC prefix names the maker unless the MAC is private (randomized) or newer than
    OPNsense's copy of the IEEE list; then a type that only one maker makes (iPhone, PlayStation) still tells us."""
    if vendor:
        return vendor, ""
    why = "private MAC" if is_private(mac) else "MAC prefix not in the vendor list"
    if rules().vendor(dtype):
        return rules().vendor(dtype), f"from device type ({why})"
    return "", why


def icon_for(o, dtype, picked):
    """A device's own pick, else one picked for another device of the same type, else the type's icon."""
    return o.get("icon") or picked.get(loose(dtype or "")) or rules().icon(dtype)


def devices_info(db, overrides, macs=None):
    """Everything known about each device, with overrides applied; only `macs` (a set) if given."""
    out = {}
    picked = {}                         # type -> icon picked on some device of that type
    for o in overrides.values():
        if o.get("type") and o.get("icon"):
            picked.setdefault(loose(o["type"]), o["icon"])
    sql, args = "SELECT mac, ip, name, hostname, vendor, type, type_source, model, first_seen, last_seen FROM devices", ()
    if macs is not None:
        args = tuple(sorted(macs))
        sql += " WHERE mac IN (%s)" % ",".join("?" * len(args))
    for mac, ip, name, hostname, vendor, dtype, src, model, first, last in db.execute(sql, args):
        o = overrides.get(mac, {})
        vendor, vnote = vendor_of(mac, vendor, o.get("type") or dtype)
        out[mac] = {"mac": mac, "ip": ip, "name": o.get("name") or name or hostname or "", "vendor": vendor,
                    "vendor_note": vnote, "icon": icon_for(o, o.get("type") or dtype, picked),
                    "icon_set": o.get("icon") or "",
                    "model": model or "",
                    "type": o.get("type") or dtype or "", "type_source": "set by you" if o.get("type") else (src or ""),
                    "first_seen": first, "last_seen": last, "custom": bool(o)}
    for mac, o in overrides.items():   # named before loupe recorded the device
        if macs is not None and mac not in macs:
            continue
        out.setdefault(mac, {"mac": mac, "ip": "", "name": o.get("name") or "", "vendor": "", "vendor_note": "",
                             "icon": icon_for(o, o.get("type"), picked), "icon_set": o.get("icon") or "",
                             "type": o.get("type") or "",
                             "type_source": "set by you" if o.get("type") else "", "model": "", "first_seen": 0, "last_seen": 0,
                             "custom": True})
    return out


def label(dev, ip):
    return (dev or {}).get("name") or ip


def service_rows(db, svc, hours):
    """The period's traffic per device and service: [(ip, mac, service, up, down, conns, last)]. Up to two days:
    the last `hours`, from loupd's hourly summary and the detail tables; longer periods are whole days (today and
    the days before it) from loupd's daily summary."""
    table, start, _ = window(hours)
    now = time.time()
    if table != "flows_1h":
        return recent_rows(Namer(db, svc), table, start, int(now) + 1)
    first = local_day(now - (max(1, round(hours / 24)) - 1) * 86400)
    oldest = db.execute("SELECT min(bucket) FROM flows_1h").fetchone()[0]
    try:
        rows = db.execute("SELECT ip, mac, service, sum(up), sum(down), sum(conns), max(last) FROM services_1d "
                          "WHERE day >= ? GROUP BY ip, mac, service", (first,)).fetchall()
        built = {d for (d,) in db.execute("SELECT day FROM summary_days WHERE day >= ?", (first,))}
    except sqlite3.OperationalError:        # loupd hasn't created the summary yet (just updated)
        rows, built = [], set()
    # days loupd hasn't summarized yet (just installed or upgraded): from the hourly table, slower
    missing = [d for d in days(max(first, local_day(oldest or now)), local_day(now)) if d not in built]
    if not missing:
        return rows
    out = {}
    for ip, mac, service, up, down, conns, last in rows:
        add(out, (ip, mac, service), up, down, conns, last)
    for d in missing:
        for k, v in build_day(db, svc, d)[0].items():
            add(out, k, *v)
    return [(*k, *v) for k, v in out.items()]


def cmd_devices(hours, want=None):
    """Devices with traffic in the period, biggest first; only traffic on the networks in `want` if given."""
    db, svc, ov = load()
    info = devices_info(db, ov)
    rows = {}
    tops = collections.defaultdict(collections.Counter)
    on = collections.defaultdict(set)
    for ip, mac, service, up, down, conns, last in service_rows(db, svc, hours):
        net = nets.of(ip)
        if want and net not in want:
            continue
        key = mac or ip
        on[key].add(net)
        r = rows.get(key)
        if r is None:
            d = info.get(mac, {})
            r = rows[key] = {**{k: d.get(k, "") for k in ("name", "vendor", "vendor_note", "icon", "icon_set", "type", "type_source", "model")},
                             "custom": d.get("custom", False),
                             "mac": mac, "ip": ip, "down": 0, "up": 0, "conns": 0, "last": 0}
        r["down"] += down
        r["up"] += up
        r["conns"] += conns
        if last > r["last"]:
            r["last"], r["ip"] = last, ip       # the address it used most recently
        tops[key][service or "Other"] += up + down
    for key, r in rows.items():
        r["top"] = [s for s, _ in tops[key].most_common(3)]     # "Other" too: an unnamed stream can be the biggest
        r["networks"] = nets.sort(on[key])
        # traffic is filed under its 5-minute bucket; loupd also stamps each device it saw (every minute)
        r["last"] = max(r["last"], (info.get(r["mac"]) or {}).get("last_seen") or 0)
        if not r["name"]:
            r["name"] = r["ip"]
    return {"hours": hours, "networks": nets.shown(), "rows": sorted(rows.values(), key=lambda r: -(r["down"] + r["up"]))}


def named_flows(db, svc, hours, first=False):
    """The period's traffic per device, server, port and name, a day at a time: (Labels for that day, row) with
    rows as flow_totals() gives them (plus the first bucket before `last` when `first`)."""
    table, start, _ = window(hours)
    namer = Namer(db, svc)
    cols = "sum(up), sum(down), sum(conns), " + ("min(bucket), " if first else "") + "max(max(bucket), max(last))"
    for day, a, b in pieces(start, int(time.time()) + 1):
        flows = db.execute(f"SELECT ip, mac, name, server, port, proto, {cols} FROM {table} WHERE bucket >= ? AND bucket < ? "
                           f"GROUP BY ip, mac, name, server, port, proto", (a, b)).fetchall()
        if flows:
            lab = namer.labels(day, table, a, b, flows)
            for f in flows:
                yield lab, f


def device_filter(arg):
    """A device is chosen by MAC (stable) or IP."""
    return ("mac", arg.lower()) if arg.count(":") == 5 and len(arg) == 17 else ("ip", str(ipaddress.ip_address(arg)))


def cmd_device(arg, hours):
    db, svc, ov = load()
    col, val = device_filter(arg)
    table, start, step = window(hours)
    info = devices_info(db, ov)
    namer = Namer(db, svc)
    dev = None
    if col == "mac":
        dev = info.get(val)
    else:
        dev = next((d for d in info.values() if d["ip"] == val), None)
    if dev and dev["mac"]:              # the name it sent in DHCP, when it has a domain (a work laptop's says whose)
        row = db.execute("SELECT info FROM devices WHERE mac = ?", (dev["mac"],)).fetchone()
        fqdn = (json.loads(row[0] or "{}").get("dhcp_fqdn") or "").rstrip(".") if row else ""
        dev = {**dev, "fqdn": fqdn if "." in fqdn else ""}
    services = collections.defaultdict(lambda: {"down": 0, "up": 0, "conns": 0, "names": collections.Counter(), "on": set()})
    sites = collections.defaultdict(lambda: {"down": 0, "up": 0, "conns": 0, "last": 0, "service": "", "inferred": False,
                                             "servers": set(), "on": set()})
    ports = collections.defaultdict(lambda: {"down": 0, "up": 0, "conns": 0})
    timeline = collections.defaultdict(lambda: [0, 0])
    on = set()
    for day, a, b in pieces(start, int(time.time()) + 1):       # each day's traffic named with that day's evidence
        rows = db.execute(f"SELECT bucket, last, ip, mac, name, server, port, proto, inbound, up, down, conns FROM {table} "
                          f"WHERE {col} = ? AND bucket >= ? AND bucket < ?", (val, a, b)).fetchall()
        if not rows:
            continue
        keys = tuple(sorted({r[2] for r in rows} | {r[3] for r in rows if r[3]}))
        lab = namer.labels(day, table, a, b, [r[2:8] for r in rows], keys)
        for bucket, last, ip, mac, name, server, port, proto, inbound, up, down, conns in rows:
            net = nets.of(ip)
            on.add(net)
            s, inferred = lab.named(ip, name, server, port, proto, mac)
            s = s or "Other"
            sv = services[s]
            sv["down"] += down
            sv["up"] += up
            sv["conns"] += conns
            sv["names"][name or server] += up + down
            sv["on"].add(net)
            site = sites[name or server]
            site["down"] += down
            site["up"] += up
            site["conns"] += conns
            site["last"] = max(site["last"], bucket, last)
            site["service"] = s
            site["inferred"] = inferred
            site["servers"].add(server)
            site["on"].add(net)
            p = ports[f"{proto}/{port}" + (" in" if inbound else "")]
            p["down"] += down
            p["up"] += up
            p["conns"] += conns
            tl = timeline[bucket - bucket % step]
            tl[0] += down
            tl[1] += up
    # names it looked up or connected to, by MAC (rows from before the MAC was known: by its address)
    names = collections.defaultdict(lambda: {"count": 0, "first": None, "last": 0, "sources": set(), "on": set()})
    cond, args = ("(mac = ? OR (mac = '' AND ip = ?))", (val, (dev or {}).get("ip") or "")) if col == "mac" else ("ip = ?", (val,))
    for name, ip, source, count, first_seen, last_seen in db.execute(
            f"SELECT name, ip, source, sum(count), min(first_seen), max(last_seen) FROM lookups "
            f"WHERE {cond} AND day >= ? AND last_seen >= ? GROUP BY name, ip, source", (*args, start - 86400, start)):
        n = names[name]
        n["on"].add(nets.of(ip))
        n["count"] += count
        n["first"] = first_seen if n["first"] is None else min(n["first"], first_seen)
        n["last"] = max(n["last"], last_seen)
        n["sources"].add(source)
    now = int(time.time())
    first = start - start % step
    return {
        "hours": hours, "device": dev or {"ip": val if col == "ip" else "", "mac": val if col == "mac" else ""},
        "networks": [n for n in nets.shown() if n["id"] in on],
        "services": sorted(({"service": k, "down": v["down"], "up": v["up"], "conns": v["conns"],
                             "names": [n for n, _ in v["names"].most_common(5)], "networks": nets.sort(v["on"])}
                            for k, v in services.items()),
                           key=lambda r: -(r["down"] + r["up"])),
        "sites": sorted(({"name": k, **{x: v[x] for x in ("down", "up", "conns", "last", "service", "inferred")},
                          "servers": sorted(v["servers"])[:5], "networks": nets.sort(v["on"])} for k, v in sites.items()),
                        key=lambda r: -(r["down"] + r["up"]))[:500],
        "ports": sorted(({"port": k, **v} for k, v in ports.items()), key=lambda r: -(r["down"] + r["up"]))[:50],
        "names": sorted(({"name": k, "service": svc.by_name(k) or "", "sources": ", ".join(sorted(v["sources"])),
                          "networks": nets.sort(v["on"]), **{x: v[x] for x in ("count", "first", "last")}}
                         for k, v in names.items()),
                        key=lambda r: -r["last"])[:2000],
        "step": step,
        "timeline": [[b, *timeline.get(b, [0, 0])] for b in range(first, now + 1, step)],
    }


def matches(text, *fields):
    return any(text in (f or "").lower() for f in fields)


def cmd_sites(hours, text="", want=None):
    """Every named service the house used in the period, biggest first, with the devices that used it.

    With TEXT, only services where the service, a site name or an address matches; traffic with no
    service that matches is listed under its site name or address. Only traffic on the networks in `want`
    if given."""
    db, svc, ov = load()
    info = devices_info(db, ov)
    rows = {}
    if text:
        flows = ((ip, mac, name, server, lab.service(ip, name, server, port, proto, mac), up, down, conns, last)
                 for lab, (ip, mac, name, server, port, proto, up, down, conns, last) in named_flows(db, svc, hours))
    else:       # the whole list: per service is enough
        flows = ((ip, mac, "", "", s, up, down, conns, last) for ip, mac, s, up, down, conns, last in service_rows(db, svc, hours))
    for ip, mac, name, server, s, up, down, conns, last in flows:
        if want and nets.of(ip) not in want:
            continue
        s = s or None
        hit = bool(text) and (matches(text, name, s) or server == text)
        if not s:
            if not hit:
                continue
            s = name or server
        r = rows.setdefault(s, {"service": s, "down": 0, "up": 0, "conns": 0, "last": 0, "by": collections.Counter(),
                                "hit": False})
        r["hit"] = r["hit"] or hit
        r["down"] += down
        r["up"] += up
        r["conns"] += conns
        r["last"] = max(r["last"], last)
        r["by"][label(info.get(mac) or next((d for d in info.values() if d["ip"] == ip), None), ip)] += up + down
    out = []
    for r in rows.values():
        by, hit = r.pop("by"), r.pop("hit")
        if text and not hit:
            continue
        out.append({**r, "devices": len(by), "names": [n for n, _ in by.most_common(3)]})
    return {"hours": hours, "rows": sorted(out, key=lambda r: -(r["down"] + r["up"]))}


def cmd_lookup(text, hours, exact=False, want=None):
    """Traffic and lookups matching a site name, a service name (YouTube also finds googlevideo.com) or an address.

    exact: one row of the services list - its service, or for nameless traffic its site name or address."""
    db, svc, ov = load()
    _table, start, _ = window(hours)
    info = devices_info(db, ov)
    text = text.strip().lower()
    rows = collections.defaultdict(lambda: {"down": 0, "up": 0, "conns": 0, "first": None, "last": 0, "servers": set()})
    for lab, (ip, mac, name, server, port, proto, up, down, conns, first, last) in named_flows(db, svc, hours, first=True):
        if want and nets.of(ip) not in want:
            continue
        s, inferred = lab.named(ip, name, server, port, proto, mac)
        s = s or ""
        if exact:
            if (s or name or server).lower() != text:
                continue
        elif not (matches(text, name, s) or server == text):
            continue
        r = rows[(mac or ip, name or (s and "\0" + s) or server)]     # nameless traffic: one row per service
        r.update(ip=ip, mac=mac, name=name, service=s, inferred=bool(s) and inferred)
        r["down"] += down
        r["up"] += up
        r["conns"] += conns
        r["first"] = first if r["first"] is None else min(r["first"], first)
        r["last"] = max(r["last"], last)
        r["servers"].add(server)
    looked = []
    day0 = start - 86400
    for ip, mac, name, source, count, first, last in db.execute(
            "SELECT ip, mac, name, source, sum(count), min(first_seen), max(last_seen) FROM lookups "
            "WHERE day >= ? AND last_seen >= ? GROUP BY ip, mac, name, source", (day0, start)):
        if want and nets.of(ip) not in want:
            continue
        if exact:
            if (svc.by_name(name) or name).lower() != text:
                continue
        elif not matches(text, name, svc.by_name(name)):
            continue
        dev = info.get(mac) or next((d for d in info.values() if d["ip"] == ip), None)
        looked.append({"ip": ip, "mac": mac, "device": label(dev, ip), "type": (dev or {}).get("type", ""), "name": name,
                       "source": source, "count": count, "first": first, "last": last})
    out = []
    for (key, _name), r in rows.items():
        dev = info.get(r["mac"]) or next((d for d in info.values() if d["ip"] == r["ip"]), None)
        out.append({**{k: r[k] for k in ("ip", "mac", "name", "service", "inferred", "down", "up", "conns", "first", "last")},
                    "device": label(dev, r["ip"]), "type": (dev or {}).get("type", ""),
                    "servers": sorted(r["servers"])[:5]})
    return {"hours": hours, "query": text, "traffic": sorted(out, key=lambda r: -(r["down"] + r["up"])),
            "lookups": sorted(looked, key=lambda r: -r["last"])[:2000]}


def cmd_networks():
    """The networks to offer as a filter, in the settings' order (none when Loupe watches just one)."""
    load()[0].close()
    return {"networks": nets.shown()}


def cmd_icons():
    """The icon each overridden device shows, for the Overrides grid."""
    db, _svc, ov = load()
    try:
        info = devices_info(db, ov)
    finally:
        db.close()
    return {mac: info[mac]["icon"] for mac in ov}


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


IDENTITY_FIELDS = ("name", "type", "icon", "vendor", "model")
MAX_MACS = 1024
MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def cmd_identities(arg):
    """For other plugins (os-eero): what Loupe calls each of these devices. `arg` is a comma-separated MAC list.
    A stable contract: {"version": 1, "devices": {mac: {name, type, icon, vendor, model}}}, only devices Loupe
    knows; fields may be empty strings. Icons are Font Awesome classes."""
    macs = {m.strip().lower().replace("-", ":") for m in (arg or "").split(",") if m.strip()}
    if not macs or len(macs) > MAX_MACS or not all(MAC.match(m) for m in macs):
        raise ValueError("expected a comma-separated list of MAC addresses (at most %d)" % MAX_MACS)
    db, _, overrides = load()
    try:
        info = devices_info(db, overrides, macs)
    finally:
        db.close()
    return {"version": 1, "devices": {mac: {k: d.get(k) or "" for k in IDENTITY_FIELDS} for mac, d in info.items()}}


def hours_arg(v):
    h = float(v)
    if not 0 < h <= MAX_HOURS:
        raise ValueError("hours out of range")
    return h


def main(argv):
    try:
        cmd = argv[1] if len(argv) > 1 else ""
        arg = (lambda i: argv[i] if len(argv) > i else "")
        if cmd == "devices":
            out = cmd_devices(hours_arg(argv[2]), wanted(arg(3)))
        elif cmd == "device":
            out = cmd_device(argv[2], hours_arg(argv[3]))
        elif cmd == "sites":
            out = cmd_sites(hours_arg(argv[2]), arg(3).strip().lower(), wanted(arg(4)))
        elif cmd == "lookup":
            out = cmd_lookup(argv[2], hours_arg(argv[3]), arg(4) == "exact", wanted(arg(5)))
        elif cmd == "networks":
            out = cmd_networks()
        elif cmd == "icons":
            out = cmd_icons()
        elif cmd == "identities":
            out = cmd_identities(argv[2] if len(argv) > 2 else "")
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
