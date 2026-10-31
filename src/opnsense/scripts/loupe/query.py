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
  query.py summary
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
from lib.report import (Namer, add, add_site, build_day, days, local_day, next_day, pieces, recent_rows,  # noqa: E402
                        site_rows)
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


def period(hours):
    """(table, start) of a whole-house report: up to two days the last `hours`; longer periods are whole local days
    (today and the days before it), as loupd's daily summary has them."""
    table, start, _ = window(hours)
    if table == "flows_1h":
        start = local_day(time.time() - (max(1, round(hours / 24)) - 1) * 86400)
    return table, start


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
    table, first = period(hours)
    now = time.time()
    if table != "flows_1h":
        return recent_rows(Namer(db, svc), table, first, int(now) + 1)
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


SITE_COLS = "ip, mac, site, named, service, inferred, up, down, conns, first, last, servers"


def live_sites(db, namer, table, day, a, b):
    """The traffic in [a, b) of `day` per device, site and service, named here: site rows as report.site_rows()."""
    flows = db.execute(f"SELECT ip, mac, name, server, port, proto, sum(up), sum(down), sum(conns), min(bucket), "
                       f"max(max(bucket), max(last)) FROM {table} WHERE bucket >= ? AND bucket < ? "
                       f"GROUP BY ip, mac, name, server, port, proto", (a, b)).fetchall()
    if not flows:
        return []
    lab = namer.labels(day, table, a, b, flows)
    sites = {}
    for ip, mac, name, server, port, proto, up, down, conns, first, last in flows:
        s, inferred = lab.named(ip, name, server, port, proto, mac)
        s = s or ""
        add_site(sites, ip, mac, name, server, s, bool(s) and inferred, up, down, conns, first, last)
    return site_rows(sites)


LISTED = "lower(CASE WHEN service != '' THEN service ELSE site END)"     # as the sites_1d_listed index has it
DAYS_IN = "day IN (SELECT value FROM json_each(?))"


def split_period(namer, table, start, end):
    """[start, end) as ([days with a site summary], [(day, a, b)]: the traffic to name here, on days without one
    and on today since its summary). Up to two days, all of it is named here (the 5-minute and 1-minute detail)."""
    stored, live = [], []
    for day, a, b in pieces(start, end):
        cut = min(b, namer.sites_until(day)) if table == "flows_1h" and a == day else a
        if cut > a:
            stored.append(day)
        if cut < b:
            live.append((day, cut, b))
    return stored, live


def site_candidates(db, first, text):
    """What the names table holds since day `first` that can match `text`: (named sites whose name contains it,
    sites that were on the address `text`, services whose name contains it)."""
    by_name, by_address, services = set(), set(), set()
    for site, named, s, server in db.execute(
            "SELECT site, named, service, server FROM site_names WHERE last_day >= ? AND "
            "(instr(lower(site), ?) > 0 OR instr(lower(service), ?) > 0 OR server = ?)", (first, text, text, text)):
        if named and text in site.lower():
            by_name.add(site)
        if server == text:
            by_address.add(site)
        if text in s.lower():
            services.add(s)
    return by_name, by_address, services


def stored_sites(db, days, text, exact, by_service=True):
    """loupd's site rows on `days` that can match `text` (site_matches() tells), as report.site_rows(), in the
    order loupd wrote them. Not exact: the rows of the sites the names table matches, and with `by_service` the
    rows of the services it matches."""
    if not days:
        return []
    if exact:
        cond, args = f"{LISTED} = ?", (text,)
    else:
        by_name, by_address, services = site_candidates(db, min(days), text)
        sites = by_name | by_address
        cond, args = "site IN (SELECT value FROM json_each(?))", (json.dumps(sorted(sites)),)
        if by_service and services:
            cond = f"({cond} OR {LISTED} IN (SELECT value FROM json_each(?)))"
            args += (json.dumps(sorted(s.lower() for s in services)),)
        elif not sites:
            return []
    return [(*r[:11], r[11].split() if r[3] else (r[2],))
            for r in db.execute(f"SELECT {SITE_COLS} FROM sites_1d WHERE {cond} AND {DAYS_IN} ORDER BY day, rowid",
                                (*args, json.dumps(days)))]


def site_matches(text, exact, site, named, s, servers):
    """exact: the service is `text`, or for traffic without one its site name or address. Else `text` is in the
    service or site name, or is one of the site's addresses."""
    if exact:
        return (s or site).lower() == text
    return (named and text in site.lower()) or text in s.lower() or text in servers


def matching_sites(db, svc, hours, text, exact):
    """The period's traffic per device, site and service that matches `text` (site_matches()): from loupd's site
    summary where it has it, the rest named here a day at a time."""
    table, start = period(hours)
    namer = Namer(db, svc)
    stored, live = split_period(namer, table, start, int(time.time()) + 1)
    rows = stored_sites(db, stored, text, exact)
    for day, a, b in live:
        rows += live_sites(db, namer, table, day, a, b)
    return [r for r in rows if site_matches(text, exact, r[2], r[3], r[4], r[11])]


def search_rows(db, svc, hours, text):
    """The services search's traffic: (ip, mac, listed as, up, down, conns, last, matched). A service is listed with
    all its traffic when its name or one of its sites matches; traffic without a service only when its site matches,
    listed as the site. Where loupd's summaries have it: the services' traffic from the daily summary (from the site
    summary for today's summarized hours), the matching sites from the site summary, added up there."""
    table, start = period(hours)
    namer = Namer(db, svc)
    stored, live = split_period(namer, table, start, int(time.time()) + 1)
    whole = [d for d in stored if namer.sites_until(d) == next_day(d)]
    part = [d for d in stored if d not in whole]
    matched, services = [], set()
    if stored:
        by_name, by_address, services = site_candidates(db, min(stored), text)
        in_days, names = json.dumps(stored), json.dumps(sorted(by_name))
        # a site whose name matches: all its traffic. With a service, it only marks the service as matched (its
        # traffic is the service's rows below); without one, it is listed as the site.
        for ip, mac, s in db.execute("SELECT DISTINCT ip, mac, service FROM sites_1d WHERE named = 1 AND service != '' "
                                     f"AND site IN (SELECT value FROM json_each(?)) AND {DAYS_IN}", (names, in_days)):
            matched.append((ip, mac, s, 0, 0, 0, 0, True))
            services.add(s)
        matched += [(*r, True) for r in db.execute(
            "SELECT ip, mac, site, sum(up), sum(down), sum(conns), max(last) FROM sites_1d WHERE named = 1 AND service = '' "
            f"AND site IN (SELECT value FROM json_each(?)) AND {DAYS_IN} GROUP BY ip, mac, site "
            "ORDER BY min(day), ip, mac, site", (names, in_days))]
        # a site on the address: the days it was there
        for ip, mac, site, named, s, _inf, up, down, conns, _first, last, servers in db.execute(
                f"SELECT {SITE_COLS} FROM sites_1d WHERE site IN (SELECT value FROM json_each(?)) AND {DAYS_IN} "
                "ORDER BY day, rowid", (json.dumps(sorted(by_address)), in_days)):
            if named and site in by_name:
                continue
            if site_matches(text, False, site, named, s, servers.split() if named else (site,)):
                matched.append((ip, mac, s, 0, 0, 0, 0, True) if s else (ip, mac, site, up, down, conns, last, True))
                if s:
                    services.add(s)
    wanted_services = json.dumps(sorted(services))
    # per device and service, each first seen where the day-by-day rows would have it (the devices listed with a
    # service are the biggest, in that order when they tie)
    for sql, days in (("SELECT ip, mac, service, sum(up), sum(down), sum(conns), max(last) FROM services_1d WHERE service IN "
                       f"(SELECT value FROM json_each(?)) AND {DAYS_IN} GROUP BY ip, mac, service "
                       "ORDER BY min(day), ip, mac, service", whole),
                      ("SELECT ip, mac, service, sum(up), sum(down), sum(conns), max(last) FROM sites_1d WHERE service IN "
                       f"(SELECT value FROM json_each(?)) AND {DAYS_IN} GROUP BY ip, mac, service", part)):
        if days and services:
            for ip, mac, s, up, down, conns, last in db.execute(sql, (wanted_services, json.dumps(days))):
                yield ip, mac, s, up, down, conns, last, text in s.lower()
    yield from matched
    for day, a, b in live:
        for ip, mac, site, named, s, _inf, up, down, conns, _first, last, servers in live_sites(db, namer, table, day, a, b):
            hit = site_matches(text, False, site, named, s, servers)
            if s or hit:
                yield ip, mac, s or site, up, down, conns, last, hit


def device_filter(arg):
    """A device is chosen by MAC (stable) or IP."""
    return ("mac", arg.lower()) if arg.count(":") == 5 and len(arg) == 17 else ("ip", str(ipaddress.ip_address(arg)))


WEB_PORTS = {"tcp/443", "udp/443", "tcp/80"}


def traffic_tree(services, sites):
    """A device's traffic as the device page lists it: each service, biggest first, with its sites under it
    (`_children`, the grid's tree), each with its percent of the device's traffic. A site's ports are listed when
    they aren't the web's: SSH, a game, a VPN."""
    total = sum(v["down"] + v["up"] for v in services.values()) or 1

    def pct(v):
        return round(100 * (v["down"] + v["up"]) / total, 1)

    kids = collections.defaultdict(list)
    for (s, name), v in sites.items():
        kids[s].append({"id": f"{s}\0{name}", "label": name, "service": s, "inferred": v["inferred"], "pct": pct(v),
                        **{x: v[x] for x in ("down", "up", "conns", "first", "last")},
                        "servers": sorted(v["servers"])[:5], "networks": nets.sort(v["on"]),
                        "ports": [p for p, _ in v["ports"].most_common() if p not in WEB_PORTS][:3]})
    rows = []
    for s, v in services.items():
        children = sorted(kids[s], key=lambda r: -(r["down"] + r["up"]))[:200]
        rows.append({"id": s, "label": s, "service": s, "down": v["down"], "up": v["up"], "conns": v["conns"], "pct": pct(v),
                     "first": min((c["first"] for c in children), default=0),
                     "last": max((c["last"] for c in children), default=0), "networks": nets.sort(v["on"]),
                     "search": " ".join(c["label"] for c in children), "_children": children})
    return sorted(rows, key=lambda r: -(r["down"] + r["up"]))


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
    services = collections.defaultdict(lambda: {"down": 0, "up": 0, "conns": 0, "on": set()})
    sites = collections.defaultdict(lambda: {"down": 0, "up": 0, "conns": 0, "first": None, "last": 0, "service": "", "inferred": False,
                                             "servers": set(), "on": set(), "ports": collections.Counter()})
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
            sv["on"].add(net)
            site = sites[(s, name or server)]
            site["down"] += down
            site["up"] += up
            site["conns"] += conns
            site["last"] = max(site["last"], bucket, last)
            site["first"] = bucket if site["first"] is None else min(site["first"], bucket)
            site["service"] = s
            site["inferred"] = inferred
            site["servers"].add(server)
            site["on"].add(net)
            site["ports"][f"{proto}/{port}" + (" in" if inbound else "")] += up + down
            tl = timeline[bucket - bucket % step]
            tl[0] += down
            tl[1] += up
    now = int(time.time())
    first = start - start % step
    return {
        "hours": hours, "device": dev or {"ip": val if col == "ip" else "", "mac": val if col == "mac" else ""},
        "networks": [n for n in nets.shown() if n["id"] in on],
        "traffic": traffic_tree(services, sites),
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
        flows = search_rows(db, svc, hours, text)
    else:       # the whole list: per service is enough
        flows = ((ip, mac, s, up, down, conns, last, False) for ip, mac, s, up, down, conns, last in service_rows(db, svc, hours))
    for ip, mac, s, up, down, conns, last, hit in flows:
        if not s or (want and nets.of(ip) not in want):
            continue
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


def chart_timeline(db, table, spans, start, step):
    """The service chart: each device's traffic to its matched sites (or, without a name, servers), only when they
    counted for the service (a server's nameless traffic can belong to another service on another day). One read
    per device, by the (ip, bucket) index."""
    size = 60 if table == "flows_1m" else 300 if table == "flows_5m" else 3600
    by_ip = collections.defaultdict(dict)       # ip -> {(name, server or None): [(from, to)]}
    for (ip, name, server), times in spans.items():
        by_ip[ip][(name, server)] = [(a - a % size, b) for a, b in times]
    timeline = collections.defaultdict(lambda: [0, 0])
    for ip, wanted in by_ip.items():
        lo = max(start, min(a for t in wanted.values() for a, _ in t))
        hi = max(b for t in wanted.values() for _, b in t)
        for bucket, name, server, down, up in db.execute(
                f"SELECT bucket, name, server, down, up FROM {table} WHERE ip = ? AND bucket BETWEEN ? AND ?", (ip, lo, hi)):
            times = wanted.get((name, None)) if name else wanted.get(("", server))
            if times and any(a <= bucket <= b for a, b in times):
                tl = timeline[bucket - bucket % step]
                tl[0] += down
                tl[1] += up
    return timeline


def cmd_lookup(text, hours, exact=False, want=None, chart=False):
    """Traffic matching a site name, a service name (YouTube also finds googlevideo.com) or an address.

    exact: one row of the services list - its service, or for nameless traffic its site name or address.
    chart: its chart instead (a separate request, so the list doesn't wait for it): up to a week from the flows,
    longer by the day from the same rows as the list. The flows are those of the list's sites; a few a DNS guess named
    were listed under another site, so a service like Apple can chart a few percent more than it lists."""
    db, svc, ov = load()
    table, start = period(hours)
    info = devices_info(db, ov)
    text = text.strip().lower()
    rows = collections.defaultdict(lambda: {"down": 0, "up": 0, "conns": 0, "first": None, "last": 0, "servers": set()})
    spans = collections.defaultdict(list)      # the chart's traffic: (ip, site name or "", server or None) -> times
    daily = collections.defaultdict(lambda: [0, 0])
    for ip, mac, site, named, s, inferred, up, down, conns, first, last, servers in matching_sites(db, svc, hours, text, exact):
        if want and nets.of(ip) not in want:
            continue
        if chart:
            if hours > 168:
                day = daily[local_day(first)]
                day[0] += down
                day[1] += up
            else:
                for key in [(ip, site, None)] if named and not inferred else [(ip, "", server) for server in servers]:
                    spans[key].append((first, last))
            continue
        name = site if named else ""
        r = rows[(mac or ip, name or (s and "\0" + s) or site)]     # nameless traffic: one row per service
        if r["first"] is None or last >= r["last"]:     # the address, and service, it had most recently
            r.update(ip=ip, mac=mac, name=name, service=s)
        r["inferred"] = r.get("inferred", True) and bool(inferred)
        r["down"] += down
        r["up"] += up
        r["conns"] += conns
        r["first"] = first if r["first"] is None else min(r["first"], first)
        r["last"] = max(r["last"], last)
        r["servers"].update(servers)
    out = []
    for (key, _name), r in rows.items():
        dev = info.get(r["mac"]) or next((d for d in info.values() if d["ip"] == r["ip"]), None)
        out.append({**{k: r[k] for k in ("ip", "mac", "name", "service", "inferred", "down", "up", "conns", "first", "last")},
                    "device": label(dev, r["ip"]), "type": (dev or {}).get("type", ""),
                    "icon": (dev or {}).get("icon") or "", "servers": sorted(r["servers"])[:5]})
    if not chart:
        total = sum(r["down"] + r["up"] for r in out) or 1
        for r in out:
            r["pct"] = round(100 * (r["down"] + r["up"]) / total, 1)
        return {"hours": hours, "query": text, "traffic": sorted(out, key=lambda r: -(r["down"] + r["up"]))}
    now = int(time.time())
    if hours > 168:
        return {"hours": hours, "query": text, "step": 86400,
                "timeline": [[d, *daily.get(d, [0, 0])] for d in days(start, local_day(now))]}
    step = window(hours)[2]
    timeline = chart_timeline(db, table, spans, start, step)
    return {"hours": hours, "query": text, "step": step,
            "timeline": [[b, *timeline.get(b, [0, 0])] for b in range(start - start % step, now + 1, step)]}


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


def cmd_summary():
    """How far loupd's summaries are: the days of traffic, how many are built with the current service map (the
    rest are rebuilt newest first, and read the slow way until then), and the database file's size."""
    db, svc, _ov = load()
    try:
        namer = Namer(db, svc)
        oldest = db.execute("SELECT min(bucket) FROM flows_1h").fetchone()[0]
        all_days = list(days(local_day(oldest), local_day(time.time()))) if oldest else []
        built = [d for d in all_days if namer.built(d)]
        waiting = [d for d in all_days if not namer.built(d)]
        pages, size = (db.execute(f"PRAGMA {p}").fetchone()[0] for p in ("page_count", "page_size"))
        out = {"days": len(all_days), "built": len(built), "waiting": len(waiting),
               "newest_waiting": time.strftime("%Y-%m-%d", time.localtime(waiting[-1])) if waiting else "",
               "mb": round(pages * size / 1e6, 1)}
        return out
    finally:
        db.close()


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
        elif cmd == "servicechart":     # one row of the services list, charted
            out = cmd_lookup(argv[2], hours_arg(argv[3]), True, wanted(arg(4)), chart=True)
        elif cmd == "networks":
            out = cmd_networks()
        elif cmd == "icons":
            out = cmd_icons()
        elif cmd == "identities":
            out = cmd_identities(argv[2] if len(argv) > 2 else "")
        elif cmd == "widget":
            out = cmd_widget()
        elif cmd == "summary":
            out = cmd_summary()
        else:
            out = {"error": "unknown command"}
    except (IndexError, ValueError) as e:
        out = {"error": str(e)}
    except sqlite3.Error as e:
        out = {"error": f"database: {e}"}
    print(json.dumps(out, default=list))


if __name__ == "__main__":
    main(sys.argv)
