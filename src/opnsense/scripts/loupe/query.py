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
  query.py sites HOURS [TEXT] [NETWORKS]
  query.py service TEXT HOURS [NETWORKS]
  query.py servicechart TEXT HOURS [NETWORKS]
  query.py widget
  query.py summary
  query.py switchmap
  query.py switch ID
  query.py switchrefresh FILE|- all|ID
"""
import collections
import ipaddress
import json
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from lib import airules, devid, netif, privs, switches  # noqa: E402
from lib.devid import is_private, loose, rules, set_user_rules  # noqa: E402
from lib.report import (WEB_PORTS, Namer, add, add_site, build_day, days, local_day, next_day, pieces, port_key,  # noqa: E402
                        recent_rows, site_rows)
from lib.services import HOSTING, ServiceMap  # noqa: E402

DB = "/var/db/loupe/loupe.db"
CONF = "/usr/local/etc/loupe.json"
MAX_HOURS = 24 * 3650
_conf = None        # the settings, once read as root


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


def read_conf():
    """The settings: read as root before dropping it (main), since only root can read the file."""
    if _conf is not None:
        return _conf
    try:
        with open(CONF) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def start_as_user(cmd, path):
    """configd runs this as root, but what it reads comes from packets: read the settings, hand over a reply file the
    GUI left for this command, and drop root before touching the database."""
    global _conf
    if os.getuid() != 0:
        return
    _conf = read_conf()
    try:
        os.chmod(CONF, 0o600)           # it may hold the Home Assistant token (loupd does the same)
    except OSError:
        pass
    devid.read_root_files()
    pw = privs.user()
    if pw and cmd in ("aipreview", "aimodelpreview", "switchrefresh") and AI_REPLY.match(path or ""):
        try:
            os.chown(path, pw.pw_uid, pw.pw_gid)
        except OSError:
            pass
    privs.drop(os.path.dirname(DB))


def load():
    global nets
    conf = read_conf()
    set_user_rules(conf.get("rules", []))          # their icons
    devid.set_model_names(conf.get("model_names", {}))
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
    bucket = bucket_size(table)
    return table, start - start % bucket, step      # whole buckets: the first one isn't cut short


def bucket_size(table):
    return 60 if table == "flows_1m" else 300 if table == "flows_5m" else 3600


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


def device_groups(db):
    """{mac: sorted MACs of its device} for MACs loupd linked as one device (lib/store.py device_links); MACs
    on their own aren't in it."""
    parent = {}

    def root(m):
        while parent.setdefault(m, m) != m:
            parent[m] = parent[parent[m]]
            m = parent[m]
        return m
    try:
        for a, b in db.execute("SELECT a, b FROM device_links"):
            parent[root(a)] = root(b)
    except sqlite3.OperationalError:        # loupd hasn't created the table yet (just updated)
        return {}
    members = collections.defaultdict(list)
    for m in parent:
        members[root(m)].append(m)
    return {m: sorted(ms) for ms in members.values() for m in ms}


# how much a type's evidence says, for picking a linked device's type from its MACs (weakest last)
WEAK_SOURCES = ("vendor ", "private MAC", "dhcp fingerprint")


def merge_group(devs):
    """One device from the records of its MACs: the address and MAC it used last, your name and type set on
    any of them, else the best-identified MAC's type and the most recent name."""
    devs = sorted(devs, key=lambda d: -(d["last_seen"] or 0))
    main = devs[0]
    typed = ([d for d in devs if d["type_source"] == "set by you"]
             or [d for d in devs if d["type"] and not d["type_source"].startswith(WEAK_SOURCES)]
             or [d for d in devs if d["type"]] or [main])[0]
    named = next((d for d in devs if d["custom"] and d["name"]), None) or next((d for d in devs if d["name"]), main)
    return {**main, "name": named["name"],
            **{k: typed[k] for k in ("type", "type_source", "vendor", "vendor_note", "icon", "icon_set", "model")},
            "model_in_name": named.get("model_in_name", False) and named["model"] == typed["model"],
            "name_source": named.get("name_source", ""), "area": named.get("area", ""),
            "first_seen": min(d["first_seen"] or 0 for d in devs), "custom": any(d["custom"] for d in devs),
            "macs": [d["mac"] for d in devs]}


def devices_info(db, overrides, macs=None):
    """Everything known about each device, with overrides applied; only `macs` (a set) if given. MACs linked as
    one device all map to the same merged record, whose "mac" is the one it used last and "macs" all of them."""
    groups = device_groups(db)
    if macs is not None:
        macs = set(macs) | {m for mac in macs for m in groups.get(mac, ())}
    out = {}
    picked = {}                         # type -> icon picked on some device of that type
    for o in overrides.values():
        if o.get("type") and o.get("icon"):
            picked.setdefault(loose(o["type"]), o["icon"])
    sql, args = "SELECT mac, ip, name, name_source, area, hostname, vendor, type, type_source, model, first_seen, last_seen FROM devices", ()
    if macs is not None:
        args = tuple(sorted(macs))
        sql += " WHERE mac IN (%s)" % ",".join("?" * len(args))
    for mac, ip, name, name_src, area, hostname, vendor, dtype, src, model, first, last in db.execute(sql, args):
        o = overrides.get(mac, {})
        vendor, vnote = vendor_of(mac, vendor, o.get("type") or dtype)
        out[mac] = {"mac": mac, "ip": ip, "name": o.get("name") or name or hostname or "", "vendor": vendor,
                    "vendor_note": vnote, "icon": icon_for(o, o.get("type") or dtype, picked),
                    "icon_set": o.get("icon") or "",
                    "model": model or "",
                    # named after its model by loupd (not by you): the Type column needn't repeat the model
                    "model_in_name": not o.get("name") and name_src == "model",
                    # where the name comes from when it isn't yours: Home Assistant, the model, else the device
                    "name_source": "" if o.get("name") else (name_src or ""), "area": area or "",
                    "type": o.get("type") or dtype or "", "type_source": "set by you" if o.get("type") else (src or ""),
                    "first_seen": first, "last_seen": last, "custom": bool(o)}
    for mac, o in overrides.items():   # named before loupe recorded the device
        if macs is not None and mac not in macs:
            continue
        out.setdefault(mac, {"mac": mac, "ip": "", "name": o.get("name") or "", "vendor": "", "vendor_note": "",
                             "icon": icon_for(o, o.get("type"), picked), "icon_set": o.get("icon") or "",
                             "type": o.get("type") or "",
                             "type_source": "set by you" if o.get("type") else "", "model": "", "model_in_name": False,
                             "name_source": "", "area": "",
                             "first_seen": 0, "last_seen": 0,
                             "custom": True})
    for mac in [m for m in out if m in groups and m == groups[m][0]]:       # each group once
        merged = merge_group([out[m] for m in groups[mac] if m in out])
        for m in merged["macs"]:
            out[m] = merged
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
    """Devices with traffic in the period, biggest first, then those seen in it without traffic; only traffic
    on the networks in `want` if given."""
    db, svc, ov = load()
    info = devices_info(db, ov)
    rows = {}
    tops = collections.defaultdict(collections.Counter)
    on = collections.defaultdict(set)
    for ip, mac, service, up, down, conns, last in service_rows(db, svc, hours):
        net = nets.of(ip)
        if want and net not in want:
            continue
        d = info.get(mac, {})
        key = d.get("mac") or mac or ip           # MACs linked as one device: one row
        on[key].add(net)
        r = rows.get(key)
        if r is None:
            r = rows[key] = {**{k: d.get(k, "") for k in ("name", "vendor", "vendor_note", "icon", "icon_set", "type", "type_source", "model", "model_in_name", "name_source", "area")},
                             "custom": d.get("custom", False),
                             "mac": d.get("mac") or mac, "ip": ip, "down": 0, "up": 0, "conns": 0, "last": 0}
        r["down"] += down
        r["up"] += up
        r["conns"] += conns
        if last > r["last"]:
            r["last"], r["ip"] = last, ip       # the address it used most recently
        tops[key][service or "Other"] += up + down
    # devices seen in the period without traffic in it are listed too, with none: a device that is on the network
    # but silent (stuck, or idle) is worth seeing, and "last seen 2 minutes ago" mustn't hide from the last hour
    # (not a second MAC of a busy address: a host whose traffic goes out under a bridge's or VM's MAC also shows
    # its own NIC's MAC on the network)
    start = period(hours)[1]
    busy = {r["ip"] for r in rows.values()} | set(rows)
    for d in {id(d): d for d in info.values()}.values():         # a linked device once
        mac = d["mac"]
        if mac in rows or d["ip"] in busy or (d.get("last_seen") or 0) < start:
            continue
        net = nets.of(d["ip"]) if d["ip"] else None
        if want and net not in want:
            continue
        on[mac].add(net)
        rows[mac] = {**{k: d.get(k, "") for k in ("name", "vendor", "vendor_note", "icon", "icon_set", "type", "type_source", "model", "model_in_name", "name_source", "area")},
                     "custom": d.get("custom", False), "mac": mac, "ip": d["ip"], "down": 0, "up": 0, "conns": 0, "last": 0}
    for key, r in rows.items():
        r["top"] = [s for s, _ in tops[key].most_common(3)]     # "Other" too: an unnamed stream can be the biggest
        r["networks"] = nets.sort(on[key])
        # traffic is filed under its 5-minute bucket; loupd also stamps each device it saw (every minute)
        r["last"] = max(r["last"], (info.get(r["mac"]) or {}).get("last_seen") or 0)
        if not r["name"]:
            r["name"] = r["ip"] or r["mac"]
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


MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
LISTED = "lower(CASE WHEN service != '' THEN service ELSE site END)"     # as the sites_1d_listed index has it
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def fold(s):
    """Lower case as SQLite's lower() does it (ASCII letters only), so text matched in SQL and here agrees: a
    service named "ÉLYSÉE" is "Élysée" in both."""
    return (s or "").translate(_ASCII_LOWER)


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
        if named and text in fold(site):
            by_name.add(site)
        if server == text:
            by_address.add(site)
        if text in fold(s):
            services.add(s)
    return by_name, by_address, services


def stored_sites(db, days, text, exact, by_service=True):
    """loupd's site rows on `days` that can match `text` (site_matches() tells), as report.site_rows(), in the
    order loupd wrote them. Not exact: the rows of the sites the names table matches, and with `by_service` the
    rows of the services it matches."""
    if not days:
        return []
    if exact:      # the service (or a nameless row's site), or a named site: a device page links its sites here
        cond, args = f"({LISTED} = ? OR (site = ? AND named = 1))", (text, text)
    else:
        by_name, by_address, services = site_candidates(db, min(days), text)
        sites = by_name | by_address
        cond, args = "site IN (SELECT value FROM json_each(?))", (json.dumps(sorted(sites)),)
        if by_service and services:
            cond = f"({cond} OR {LISTED} IN (SELECT value FROM json_each(?)))"
            args += (json.dumps(sorted(fold(s) for s in services)),)
        elif not sites:
            return []
    return [(*r[:11], r[11].split() if r[3] else (r[2],))
            for r in db.execute(f"SELECT {SITE_COLS} FROM sites_1d WHERE {cond} AND {DAYS_IN} ORDER BY day, rowid",
                                (*args, json.dumps(days)))]


def site_matches(text, exact, site, named, s, servers):
    """exact: the service is `text`, or for traffic without one its site name or address, or it is the site's
    name. Else `text` is in the service or site name, or is one of the site's addresses."""
    if exact:
        return fold(s or site) == text or (bool(named) and fold(site) == text)
    return (named and text in fold(site)) or text in fold(s) or text in servers


def matching_sites(db, svc, hours, text, exact, span=None):
    """The period's traffic per device, site and service that matches `text` (site_matches()): from loupd's site
    summary where it has it, the rest named here a day at a time. With `span` (start, end), only that time (a
    zoomed chart), named here from the finest detail that covers it."""
    table, start = period(hours)
    namer = Namer(db, svc)
    if span:
        table = span_table(db, span[0])
        stored, live = [], list(pieces(span[0] - span[0] % bucket_size(table), span[1]))
    else:
        stored, live = split_period(namer, table, start, int(time.time()) + 1)
    rows = stored_sites(db, stored, text, exact)
    for day, a, b in live:
        rows += live_sites(db, namer, table, day, a, b)
    return [r for r in rows if site_matches(text, exact, r[2], r[3], r[4], r[11])]


def search_rows(db, svc, hours, text):
    """The services search's traffic: (ip, mac, listed as, up, down, conns, last, matched: True, or the site whose
    name or address matched when that's all that matched for a service). A service whose name
    matches is listed with all its traffic; otherwise only the traffic of its sites that match (a 70-byte CDN host
    whose name happens to contain the text doesn't list the whole CDN), and traffic without a service as its site.
    Where loupd's summaries have it: services from the daily summary (from the site summary for today's summarized
    hours), sites from the site summary."""
    table, start = period(hours)
    namer = Namer(db, svc)
    stored, live = split_period(namer, table, start, int(time.time()) + 1)
    whole = [d for d in stored if namer.sites_until(d) == next_day(d)]
    part = [d for d in stored if d not in whole]
    matched, services = [], set()
    if stored:
        by_name, by_address, services = site_candidates(db, min(stored), text)
        in_days, names = json.dumps(stored), json.dumps(sorted(by_name))
        # a site whose name matches: its traffic, under its service (the site itself without one) - unless the
        # service matched by name, whose traffic is all listed below
        for ip, mac, site, s, up, down, conns, last in db.execute(
                "SELECT ip, mac, site, service, sum(up), sum(down), sum(conns), max(last) FROM sites_1d WHERE named = 1 "
                f"AND site IN (SELECT value FROM json_each(?)) AND {DAYS_IN} GROUP BY ip, mac, site, service "
                "ORDER BY min(day), ip, mac, site", (names, in_days)):
            if s not in services:
                matched.append((ip, mac, s or site, up, down, conns, last, site if s else True))
        # a site on the address: the days it was there
        for ip, mac, site, named, s, _inf, up, down, conns, _first, last, servers in db.execute(
                f"SELECT {SITE_COLS} FROM sites_1d WHERE site IN (SELECT value FROM json_each(?)) AND {DAYS_IN} "
                "ORDER BY day, rowid", (json.dumps(sorted(by_address)), in_days)):
            if (named and site in by_name) or s in services:
                continue
            if site_matches(text, False, site, named, s, servers.split() if named else (site,)):
                matched.append((ip, mac, s or site, up, down, conns, last, site if s else True))
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
                yield ip, mac, s, up, down, conns, last, True
    yield from matched
    for day, a, b in live:
        for ip, mac, site, named, s, _inf, up, down, conns, _first, last, servers in live_sites(db, namer, table, day, a, b):
            if s and text in fold(s):
                yield ip, mac, s, up, down, conns, last, True
            elif site_matches(text, False, site, named, s, servers):
                yield ip, mac, s or site, up, down, conns, last, site if s else True


def device_filter(arg):
    """A device is chosen by MAC (stable) or IP. A MAC by its shape: an IPv6 address can have 5 colons and 17
    characters too (fd00::12:2:3:4567)."""
    return ("mac", arg.lower()) if MAC.match(arg.lower()) else ("ip", str(ipaddress.ip_address(arg)))


def traffic_tree(services, sites):
    """A device's traffic as the device page lists it: each service, biggest first, with its sites under it
    (`_children`, the grid's tree), each with its percent of the device's traffic. A site's ports are listed when
    they aren't the web's: SSH, a game, a VPN."""
    total = sum(v["down"] + v["up"] for v in services.values()) or 1

    def pct(v):
        return round(100 * (v["down"] + v["up"]) / total, 3)

    kids = collections.defaultdict(list)
    for (s, name), v in sites.items():
        kids[s].append({"id": f"{s}\0{name}", "label": name, "service": s, "inferred": v["inferred"], "pct": pct(v),
                        **{x: v[x] for x in ("down", "up", "conns", "first", "last")},
                        "servers": sorted(v["servers"])[:5], "networks": nets.sort(v["on"]),
                        "ports": [p for p, _ in sorted(v["ports"].items(), key=lambda kv: (-kv[1], kv[0]))
                                  if p not in WEB_PORTS][:3]})
    rows = []
    for s, v in services.items():
        children = sorted(kids[s], key=lambda r: -(r["down"] + r["up"]))[:200]
        rows.append({"id": s, "label": s, "service": s, "down": v["down"], "up": v["up"], "conns": v["conns"], "pct": pct(v),
                     "first": v["first"] or 0, "last": v["last"], "networks": nets.sort(v["on"]),
                     "search": " ".join(c["label"] for c in children), "_children": children})
    return sorted(rows, key=lambda r: -(r["down"] + r["up"]))


def span_table(db, start):
    """The finest detail table that still covers `start` (a zoomed chart's range): minutes, 5 minutes, hours."""
    for table in ("flows_1m", "flows_5m"):
        oldest = db.execute(f"SELECT min(bucket) FROM {table}").fetchone()[0]
        if oldest is not None and oldest <= start:
            return table
    return "flows_1h"


def cmd_device(arg, hours, span=None):
    """A device's report for the period; with `span` (start, end), its traffic in that time only (a zoomed chart),
    read from the finest detail that covers it and named with each day's evidence."""
    db, svc, ov = load()
    col, val = device_filter(arg)
    table, start, step = window(hours)
    start = period(hours)[1]        # past 2 days whole local days, as the list row the page was opened from
    if span:
        table = span_table(db, span[0])
        start = span[0] - span[0] % bucket_size(table)
    slot = local_day if step >= 86400 else (lambda b: b - b % step)     # a year's chart: local days, as the lists
    info = devices_info(db, ov)
    namer = Namer(db, svc)
    dev = None
    if col == "mac":
        dev = info.get(val)
    else:
        dev = next((d for d in info.values() if d["ip"] == val), None)
    # its traffic: by address, or by MAC - all of a linked device's MACs
    if col == "mac":
        who, whom = "mac IN (SELECT value FROM json_each(?))", json.dumps((dev or {}).get("macs") or [val])
    else:
        who, whom = "ip = ?", val
    if dev and dev["mac"]:              # the name it sent in DHCP, when it has a domain (a work laptop's says whose)
        row = db.execute("SELECT info FROM devices WHERE mac = ?", (dev["mac"],)).fetchone()
        fqdn = (json.loads(row[0] or "{}").get("dhcp_fqdn") or "").rstrip(".") if row else ""
        dev = {**dev, "fqdn": fqdn if "." in fqdn else ""}
    services = collections.defaultdict(lambda: {"down": 0, "up": 0, "conns": 0, "first": None, "last": 0, "on": set()})
    sites = collections.defaultdict(lambda: {"down": 0, "up": 0, "conns": 0, "first": None, "last": 0, "inferred": None,
                                             "servers": set(), "on": set(), "ports": collections.Counter()})
    timeline = collections.defaultdict(lambda: [0, 0])
    on = set()

    def count(ip, s, site_name, inferred, up, down, conns, first, last, servers, ports):
        net = nets.of(ip)
        on.add(net)
        s = s or "Other"
        sv = services[s]
        sv["down"] += down
        sv["up"] += up
        sv["conns"] += conns
        sv["on"].add(net)
        sv["first"] = first if sv["first"] is None else min(sv["first"], first)
        sv["last"] = max(sv["last"], last)
        site = sites[(s, site_name)]
        site["down"] += down
        site["up"] += up
        site["conns"] += conns
        site["last"] = max(site["last"], last)
        site["first"] = first if site["first"] is None else min(site["first"], first)
        site["inferred"] = inferred if site["inferred"] is None else site["inferred"] and inferred    # as add_site()
        site["servers"].update(servers)
        site["on"].add(net)
        site["ports"].update(ports)

    # past two days the days loupd has summarized come from its site summary (named already); the rest - days not
    # built yet, and today since its last build - is named here from the device's own rows, a day at a time
    stored, live = [], []
    for day, a, b in pieces(start, span[1] if span else int(time.time()) + 1):
        if span:                    # a zoomed range: named here from the detail rows, never the whole-day summary
            live.append((day, a, b))
            continue
        cut = min(b, namer.sites_until(day)) if table == "flows_1h" and a == day else a
        if cut > a:
            stored.append((day, cut))
        if cut < b:
            live.append((day, cut, b))
    addresses = set()
    if stored:
        for day, ip, s, site_name, named, inferred, up, down, conns, first, last, servers, ports in db.execute(
                f"SELECT day, ip, service, site, named, inferred, up, down, conns, first, last, servers, ports FROM sites_1d "
                f"WHERE {who} AND {DAYS_IN}", (whom, json.dumps([d for d, _ in stored]))):
            addresses.add(ip)
            count(ip, s, site_name, bool(inferred), up, down, conns, first, last,
                  servers.split() if named else [site_name], json.loads(ports) if ports else {})
            if step >= 86400:
                tl = timeline[day]
                tl[0] += down
                tl[1] += up
        if step < 86400 and addresses:      # the chart by the hour: just bytes, no names needed
            for day, cut in stored:
                for bucket, down, up in db.execute(
                        f"SELECT bucket, sum(down), sum(up) FROM {table} WHERE ip IN (SELECT value FROM json_each(?)) "
                        f"AND bucket >= ? AND bucket < ? AND {who} GROUP BY bucket",
                        (json.dumps(sorted(addresses)), day, cut, whom)):
                    tl = timeline[slot(bucket)]
                    tl[0] += down
                    tl[1] += up
    for day, a, b in live:       # each day's traffic named with that day's evidence
        rows = db.execute(f"SELECT bucket, last, ip, mac, name, server, port, proto, inbound, up, down, conns FROM {table} "
                          f"WHERE {who} AND bucket >= ? AND bucket < ?", (whom, a, b)).fetchall()
        if not rows:
            continue
        keys = tuple(sorted({r[2] for r in rows} | {r[3] for r in rows if r[3]}))
        lab = namer.labels(day, table, a, b, [r[2:8] for r in rows], keys)
        for bucket, last, ip, mac, name, server, port, proto, inbound, up, down, conns in rows:
            s, inferred = lab.named(ip, name, server, port, proto, mac)
            p = port_key(proto, port, inbound)
            count(ip, s, name or server, inferred, up, down, conns, bucket, max(bucket, last), [server],
                  {} if p in WEB_PORTS else {p: up + down})
            tl = timeline[slot(bucket)]
            tl[0] += down
            tl[1] += up
    now = int(time.time())
    slots = list(days(start, local_day(now))) if step >= 86400 else range(start - start % step, now + 1, step)
    if dev and switch_list():
        _, found, _ = switch_map(info)
        dev["switch_port"] = next((found[m] for m in dev.get("macs") or [dev.get("mac")] if m in found), None)
    return {
        "hours": hours, "device": dev or {"ip": val if col == "ip" else "", "mac": val if col == "mac" else ""},
        "networks": [n for n in nets.shown() if n["id"] in on],
        "traffic": traffic_tree(services, sites),
        "step": step,
        "timeline": [[b, *timeline.get(b, [0, 0])] for b in slots],
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
                                "hit": False, "via": collections.Counter()})
        r["hit"] = r["hit"] or bool(hit)
        if isinstance(hit, str):                # matched only through this site: say which
            r["via"][hit] += up + down
        r["down"] += down
        r["up"] += up
        r["conns"] += conns
        r["last"] = max(r["last"], last)
        r["by"][label(info.get(mac) or next((d for d in info.values() if d["ip"] == ip), None), ip)] += up + down
    out = []
    for r in rows.values():
        by, hit, via = r.pop("by"), r.pop("hit"), r.pop("via")
        if text and not hit:
            continue
        out.append({**r, "devices": len(by), "names": [n for n, _ in by.most_common(3)],
                    "sites": len(via), "matched": [n for n, _ in via.most_common(3)]})
    return {"hours": hours, "rows": sorted(out, key=lambda r: -(r["down"] + r["up"]))}


def chart_timeline(db, svc, table, spans, start, step):
    """The service chart: each device's traffic to its matched sites (or, without a name, servers), only when they
    counted for the service (a server's nameless traffic can belong to another service on another day). Nameless
    traffic as the list names it (report.Labels): what its own address or port gives the service; and traffic with
    no service of its own when the service came from the server's other traffic (inferred) or is a VPN (a tunnel's
    server carries only the VPN). One read per device, by the (ip, bucket) index."""
    size = bucket_size(table)
    by_ip = collections.defaultdict(dict)       # ip -> {(name, server or None): ([(from, to)], service, its own too)}
    for (ip, name, server), (times, service, inferred) in spans.items():
        by_ip[ip][(name, server)] = ([(a - a % size, b) for a, b in times], service.lower(),
                                     inferred or service.lower().startswith("vpn"))
    timeline = collections.defaultdict(lambda: [0, 0])
    for ip, wanted in by_ip.items():
        lo = max(start, min(a for t, *_ in wanted.values() for a, _ in t))
        hi = max(b for t, *_ in wanted.values() for _, b in t)
        for bucket, name, server, port, proto, down, up in db.execute(
                f"SELECT bucket, name, server, port, proto, down, up FROM {table} WHERE ip = ? AND bucket BETWEEN ? AND ?",
                (ip, lo, hi)):
            times, service, unowned = wanted.get((name, None) if name else ("", server), (None, "", False))
            if not times or not any(a <= bucket <= b for a, b in times):
                continue
            own = "" if name else (svc.service("", server, port, proto) or "")
            if name or own.lower() == service or (unowned and (not own or own in HOSTING)):
                tl = timeline[bucket - bucket % step]
                tl[0] += down
                tl[1] += up
    return timeline


def cmd_service(text, hours, want=None, chart=False, span=None):
    """One row of the services list: the traffic of its service, or for nameless traffic its site name or address.

    chart: its chart instead (a separate request, so the list doesn't wait for it): up to a week from the flows,
    longer by the day from the same rows as the list. The flows are those of the list's sites; a few a DNS guess named
    were listed under another site, so a service like Apple can chart a little more than it lists."""
    db, svc, ov = load()
    table, start = period(hours)
    info = {} if chart else devices_info(db, ov)
    text = fold(text.strip())
    rows = collections.defaultdict(lambda: {"down": 0, "up": 0, "conns": 0, "first": None, "last": 0, "servers": set()})
    spans = {}      # the chart's traffic: (ip, site name or "", server or None) -> ([times], service)
    daily = collections.defaultdict(lambda: [0, 0])
    for ip, mac, site, named, s, inferred, up, down, conns, first, last, servers in matching_sites(db, svc, hours, text, True, span):
        if want and nets.of(ip) not in want:
            continue
        if chart:
            if hours > 168:
                day = daily[local_day(first)]
                day[0] += down
                day[1] += up
            else:
                for key in [(ip, site, None)] if named else [(ip, "", server) for server in servers]:
                    times, _, was = spans.get(key, ([], "", False))
                    spans[key] = (times + [(first, last)], s, was or bool(inferred))
            continue
        name = site if named else ""
        r = rows[((info.get(mac) or {}).get("mac") or mac or ip, name or (s and "\0" + s) or site)]   # nameless: per service
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
            r["pct"] = round(100 * (r["down"] + r["up"]) / total, 3)
        return {"hours": hours, "query": text, "traffic": sorted(out, key=lambda r: -(r["down"] + r["up"]))}
    now = int(time.time())
    if hours > 168:
        return {"hours": hours, "query": text, "step": 86400,
                "timeline": [[d, *daily.get(d, [0, 0])] for d in days(start, local_day(now))]}
    step = window(hours)[2]
    timeline = chart_timeline(db, svc, table, spans, start, step)
    return {"hours": hours, "query": text, "step": step,
            "timeline": [[b, *timeline.get(b, [0, 0])] for b in range(start - start % step, now + 1, step)]}


def cmd_networks():
    """The networks to offer as a filter, in the settings' order (none when Loupe watches just one)."""
    load()[0].close()
    return {"networks": nets.shown()}


def cmd_local_networks():
    """What the selected interfaces' addresses give as local now, and whether private addresses stand in for an
    interface with none (what loupd does; the settings page shows it). Applied settings, not the form's."""
    conf = read_conf()
    ifaces = set(conf.get("interfaces", []))
    found, addressed = [], set()
    try:
        for dev, i in netif.addresses(ifaces):
            if not i.ip.is_link_local:
                addressed.add(dev)
                if str(i.network) not in found:
                    found.append(str(i.network))
    except OSError:
        return {"error": "can't read the interfaces' addresses"}
    return {"networks": found, "private": bool(ifaces - addressed) and not conf.get("local_networks")}


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
    first = (now - 86400) // 3600 * 3600        # whole hours: the first one is in the chart, so in the sums too
    for bucket, up, down in db.execute(
            "SELECT bucket, sum(up), sum(down) FROM flows_1h WHERE bucket >= ? GROUP BY bucket", (first,)):
        spark[bucket] = [down, up]
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
    return {"version": 1, "devices": {mac: {k: d.get(k) or "" for k in IDENTITY_FIELDS} for mac, d in info.items()
                                      if mac in macs}}       # not the other MACs of a linked device


def hours_arg(v):
    h = float(v)
    if not 0 < h <= MAX_HOURS:
        raise ValueError("hours out of range")
    return h


# ---- AI Rule Generator (lib/airules.py) ---------------------------------------------------------------------------
AI_REPLY = re.compile(r"^/tmp/loupe-ai-[A-Za-z0-9]{6,32}\.json$")      # where the GUI leaves a pasted reply


def ai_devices(db, ov):
    """The devices seen in the last week as loupd classifies them: [{mac, name, type, type_source, vendor, model,
    hostname, private, custom, info, names, for_rules: (mac, host name, info, names)}]; a linked device once."""
    since = time.time() - 7 * 86400
    seen = {}
    for mac, ip, name in db.execute("SELECT DISTINCT mac, ip, name FROM sites_seen WHERE day >= ?", (since,)):
        seen.setdefault(mac or ip, set()).add(name)
    info = devices_info(db, ov)
    leases, hosts = devid.leases(), devid.static_hosts()
    oui = devid.load_oui()
    out = []
    for mac, ip, hostname, raw in db.execute("SELECT mac, ip, hostname, info FROM devices WHERE last_seen >= ?", (since,)):
        d = info.get(mac) or {}
        if d.get("mac", mac) != mac:            # another MAC of a linked device
            continue
        current = devid.current_mdns(json.loads(raw or "{}"))
        names = sorted(seen.get(mac, set()) | seen.get(ip, set()))
        # the MAC vendor, which rules match - not the maker a type names (a Sub-Zero rule calls its Card Access
        # module's maker Sub-Zero)
        out.append({"mac": mac, "name": d.get("name") or hostname or ip or mac, "type": d.get("type") or "",
                    "type_source": d.get("type_source") or "", "vendor": devid.vendor(mac, oui) or "",
                    "model": d.get("model") or "", "hostname": hostname or "", "private": is_private(mac),
                    "custom": d.get("type_source") == "set by you", "info": current, "names": names,
                    "for_rules": (mac, leases.get(mac) or hosts.get(ip) or hostname,
                                  {**current, "dhcp_hostname": hostname}, names)})
    return sorted(out, key=lambda d: (d["type"], d["name"].lower()))


def cmd_aitable(arg):
    """The AI Rule Generator's table and prompt: the devices Loupe couldn't identify from their own evidence (no
    type, or a generic one; not ones you typed), or with `arg` "all" every device. Also how many each choice has."""
    if arg not in ("", "all"):
        raise ValueError("expected nothing or 'all'")
    db, _, ov = load()
    devs = ai_devices(db, ov)
    unknown = [d for d in devs if airules.needs_help(d["type"]) and not d["custom"]]
    chosen = devs if arg == "all" else unknown
    table = airules.rows([(d["mac"], airules.evidence(d)) for d in chosen])
    asked = sum(len(ms) for _rid, ms, _ev in table)
    by_mac = {d["mac"]: d for d in devs}
    return {"prompt": airules.prompt(table, rules().types) if table else "",
            "rows": [{"id": rid, "macs": ms, "names": [by_mac[m]["name"] for m in ms], "type": by_mac[ms[0]]["type"]}
                     for rid, ms, _ev in table],
            "unidentified": len(unknown), "all": len(devs), "asked": asked, "chosen": len(chosen)}


def cmd_aipreview(path):
    """The AI's reply (a file the GUI wrote: {"reply": text}) checked against every device; the file is removed
    once read. Row ids are fingerprints of the devices' evidence (airules.row_id), matched against every device
    now, so an answer still lands on the right devices when the list changed after the prompt was copied."""
    if not AI_REPLY.match(path or ""):
        raise ValueError("expected a reply file")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    suggested, unknown, problem, _models = airules.parse(data.get("reply") or "")
    user = read_conf().get("rules", [])
    db, _, ov = load()
    devs = ai_devices(db, ov)
    ids = {}
    for d in devs:
        ids.setdefault(airules.row_id(airules.evidence(d)), []).append(d["mac"])
    names = {rid: [next(d["name"] for d in devs if d["mac"] == m) for m in macs] for rid, macs in ids.items()}
    checked = airules.preview(suggested, [d["for_rules"] for d in devs], ids, user, devid.load_oui()) if suggested else []
    by_mac = {d["mac"]: d["name"] for d in devs}
    for r in checked:       # which devices each rule is for, as the page shows them (a row no device has: '?')
        r["for"] = [n for rid in r["devices"] for n in names.get(rid.upper(), ["?"])]
        r["others"] = [by_mac.get(m, m) for m in r["others"]]      # the devices it would change besides its own
        r["icon"] = rules().icon(r["type"])         # the type's icon now ('': a new type, the page offers a choice)
    return {"problem": problem, "rules": checked,
            "unknown": [{"for": [n for rid in u.get("devices") or [] for n in names.get(str(rid).upper(), ["?"])],
                         "note": u.get("note") or ""} for u in unknown]}


def span_arg(start, end):
    """A zoomed chart's time range, (start, end) in epoch seconds, or None."""
    if not (start and end):
        return None
    span = (int(start), int(end))
    if not 0 < span[0] < span[1] <= time.time() + 3600:
        raise ValueError("bad time range")
    return span


def model_codes(db, ov):
    """The model codes devices announce, or a rule of the user's gives them (what they say, not the user's names):
    {code: [device dicts]}; codes with a space are a readable name already (Lutron Processor), not a code - except
    a rule's ("DGC 7785": the user or the AI wrote it as the model to name)."""
    codes = collections.defaultdict(list)
    oui = devid.load_oui()
    for d in ai_devices(db, ov):
        rule_model = (devid.classify(*d["for_rules"], oui)[3] or "").strip()
        code = devid.model(d["info"], names={}, fallback=rule_model)
        if code and code == rule_model:
            codes[code].append(d)
            continue
        if code and " " not in code:
            codes[code].append(d)
    return codes


def cmd_aimodels():
    """Model names' AI prompt: the codes devices announce that have no name yet, with only maker and type."""
    db, _, ov = load()
    info = devices_info(db, ov)
    rows = []
    for code, devs in sorted(model_codes(db, ov).items()):
        if code in devid._model_names:
            continue
        maker = next((info.get(d["mac"], {}).get("vendor") for d in devs if info.get(d["mac"], {}).get("vendor")), "")
        rows.append((code, maker, devs[0]["type"], len(devs)))
    return {"prompt": airules.models_prompt(rows) if rows else "", "codes": len(rows),
            "named": len(devid._model_names)}


def cmd_aimodelpreview(path):
    """The AI's answer to the model-name prompt (a file the GUI wrote, removed once read): proposals for codes
    devices announce, with the devices that have them and the name now."""
    if not AI_REPLY.match(path or ""):
        raise ValueError("expected a reply file")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    names, problem = airules.parse_model_names(data.get("reply") or "")
    db, _, ov = load()
    codes = model_codes(db, ov)
    out, unknown = [], []
    for code, name, confidence in names:
        if code not in codes:
            unknown.append(code)
        elif devid._model_names.get(code) != name:
            out.append({"identifier": code, "name": name, "confidence": confidence,
                        "for": [d["name"] for d in codes[code]], "now": devid._model_names.get(code, "")})
    if unknown:
        note = f"Not announced by any device here, left out: {', '.join(unknown[:5])}{'…' if len(unknown) > 5 else ''}."
        problem = f"{problem} {note}" if problem else note
    return {"problem": problem if (out or problem) else None, "models": out}


def cmd_hass():
    """How Loupe's Home Assistant link is doing (Settings shows it): when loupd last fetched names, how many devices,
    the last error. loupd does the fetching; this only reads what it saved."""
    try:
        with open(os.path.join(os.path.dirname(DB), "hass.json"), encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {"fetched": 0, "devices": 0, "error": None}
    return {"fetched": st.get("fetched") or 0, "tried": st.get("tried") or 0, "devices": len(st.get("names") or {}),
            "error": st.get("error")}


# ---- Switch map (lib/switches.py) -----------------------------------------------------------------------------------
def switch_state_path():
    return os.path.join(os.path.dirname(DB), "switches.json")


def switch_list():
    return (read_conf().get("switch_map") or {}).get("switches") or []


def switch_map(info, traffic=None):
    """(the switches as described, {MAC: where}, os-eero's view), from the last refresh: no switch is asked anything."""
    return switches.describe(switch_list(), switches.load_state(switch_state_path()), info, netif.link_macs(), traffic)


def cmd_switchrefresh(path, target):
    """Read the switches now (all, or one) and keep what they say. `path`: a file the GUI left with os-eero's units and
    Wi-Fi clients, removed once read, or - without that plugin."""
    if target != "all" and not switches.SWITCH_ID.match(target or ""):
        raise ValueError("expected a switch")
    eero = {}
    if AI_REPLY.match(path or ""):
        try:
            with open(path, encoding="utf-8") as f:
                eero = json.load(f)
        except (OSError, ValueError):
            pass
        try:
            os.unlink(path)
        except OSError:
            pass
    return {"errors": switches.refresh(switch_state_path(), switch_list(), target, eero)}


def public_switch(s):
    return {k: v for k, v in s.items() if k != "rows"}


def cmd_switchmap():
    """The switches for the tab's list and the settings' grid, and the devices seen today on none of their ports."""
    db, _, ov = load()
    try:
        info = devices_info(db, ov)
    finally:
        db.close()
    described, found, eero = switch_map(info)
    shown = sorted(described.values(), key=lambda s: ipaddress.ip_address(s["address"]).packed)
    read = any(s["fetched"] for s in shown)     # until a switch has been read, every device would count as elsewhere
    return {"switches": [public_switch(s) for s in shown],
            "names": switches.all_names(switch_list(), switches.load_state(switch_state_path())),
            "elsewhere": switches.elsewhere(described, found, eero, info, netif.link_macs(), time.time() - 86400) if read else []}


def cmd_switch(sid):
    """One switch's page: every port that is up and what is on it, with each device's traffic over the last day."""
    if not switches.SWITCH_ID.match(sid or ""):
        raise ValueError("expected a switch")
    traffic = {r["mac"]: (r["down"], r["up"]) for r in cmd_devices(24)["rows"] if r.get("mac")}
    db, _, ov = load()
    try:
        info = devices_info(db, ov)
    finally:
        db.close()
    described, _, _ = switch_map(info, traffic)
    return {"switch": described[sid]} if sid in described else {"error": "no such switch"}


def main(argv):
    try:
        cmd = argv[1] if len(argv) > 1 else ""
        arg = (lambda i: argv[i] if len(argv) > i else "")
        start_as_user(cmd, arg(2))
        if cmd == "devices":
            out = cmd_devices(hours_arg(argv[2]), wanted(arg(3)))
        elif cmd == "device":
            out = cmd_device(argv[2], hours_arg(argv[3]), span_arg(arg(4), arg(5)))
        elif cmd == "sites":
            out = cmd_sites(hours_arg(argv[2]), fold(arg(3).strip()), wanted(arg(4)))
        elif cmd == "service":       # a zoomed chart's range comes as one argument, START-END (configd passes five)
            out = cmd_service(argv[2], hours_arg(argv[3]), wanted(arg(4)), span=span_arg(*(arg(5).split("-") + [""])[:2]))
        elif cmd == "servicechart":     # one row of the services list, charted
            out = cmd_service(argv[2], hours_arg(argv[3]), wanted(arg(4)), chart=True)
        elif cmd == "networks":
            out = cmd_networks()
        elif cmd == "localnetworks":
            out = cmd_local_networks()
        elif cmd == "icons":
            out = cmd_icons()
        elif cmd == "identities":
            out = cmd_identities(argv[2] if len(argv) > 2 else "")
        elif cmd == "widget":
            out = cmd_widget()
        elif cmd == "summary":
            out = cmd_summary()
        elif cmd == "aitable":
            out = cmd_aitable(arg(2))
        elif cmd == "aipreview":
            out = cmd_aipreview(arg(2))
        elif cmd == "aimodels":
            out = cmd_aimodels()
        elif cmd == "aimodelpreview":
            out = cmd_aimodelpreview(arg(2))
        elif cmd == "hass":
            out = cmd_hass()
        elif cmd == "switchmap":
            out = cmd_switchmap()
        elif cmd == "switch":
            out = cmd_switch(arg(2))
        elif cmd == "switchrefresh":
            out = cmd_switchrefresh(arg(2), arg(3))
        else:
            out = {"error": "unknown command"}
    except (IndexError, ValueError) as e:
        out = {"error": str(e)}
    except sqlite3.Error as e:
        out = {"error": f"database: {e}"}
    print(json.dumps(out, default=list))


if __name__ == "__main__":
    main(sys.argv)
