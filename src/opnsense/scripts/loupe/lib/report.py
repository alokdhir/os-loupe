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

"""Traffic named by service, with the evidence of its local day: shared by the reports (query.py) and loupd's
summaries (per hour for recent days, per day for all of them, and per day by site).

A row's service can depend on other traffic: nameless traffic takes the service of named traffic to the same server,
and a VPN tunnel is told apart by the provider's domain the device looked up. That evidence is gathered per local
day, so the same traffic gets the same name in every report period. loupd stores each day's evidence with its
summaries; reports use it, adding the evidence of minutes loupd hasn't summarized yet.
"""
import collections
import hashlib
import json
import sqlite3
import time

from .services import DATA_JSON, HOSTING

SUMMARY_VERSION = 4     # raise when the naming or what the summaries hold changes, so loupd builds them again
HOURLY_DAYS = 3         # days kept in the hourly summary (report periods up to 48 hours)
SETTLE = 120            # seconds until loupd has written a minute's traffic (it writes once a minute)


def map_fingerprint(svc):
    """Changes when the shipped service map, the user's entries or the naming code change."""
    h = hashlib.sha256(str(SUMMARY_VERSION).encode())
    with open(DATA_JSON, "rb") as f:
        h.update(f.read())
    h.update(json.dumps(svc.extra, sort_keys=True).encode())
    return h.hexdigest()[:16]


def local_day(ts):
    """Local midnight starting the day `ts` is in."""
    t = time.localtime(ts)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


def next_day(day):
    return local_day(day + 90000)       # 25 h: a day with a clock change is 23 or 25 hours long


def days(first, last):
    """Each local day from `first` through `last` (both day starts)."""
    d = first
    while d <= last:
        yield d
        d = next_day(d)


def pieces(start, end):
    """[start, end) cut at local midnights: (day, piece start, piece end)."""
    d = local_day(start)
    while d < end:
        n = next_day(d)
        yield d, max(start, d), min(end, n)
        d = n


class Labels:
    """Service names from a set of evidence, with VPN tunnels told apart.

    A device that looked up a VPN provider's domain gets "VPN (NordVPN)" instead of "VPN (WireGuard)", and other
    nameless traffic to a server it uses as a VPN (e.g. a TCP fallback) is counted as that VPN too.

    Traffic with no service, or one that only names the hosting company (Fastly CDN), takes the service of the
    other traffic between the same device and server, if that is exactly one service: a stream from a video
    vendor's domain on the servers the NFL app's own names used is NFL. `inferred()` tells such rows apart.

    Built from a window's traffic (the constructor), or from a day's stored evidence (`from_evidence`).
    """

    def __init__(self, db, svc, table, start, flows=None, end=None, lookups_from=None, keys=None):
        """The evidence in [start, end) of `table`. `flows`: that window's rows from flow_totals() (or any rows
        starting ip, mac, name, server, port, proto), when the caller has them (saves two scans of the table).
        VPN providers come from lookups since a day before the window, or since `lookups_from`. `keys` (addresses
        and MACs): only those devices' evidence - all of it is per device, so it's the same for them."""
        self.svc = svc
        counts = collections.defaultdict(collections.Counter)      # by MAC; lookups without one by address
        end = end or 1 << 62
        since = start - 86400 if lookups_from is None else lookups_from
        mine, look, args = "", "", ()
        if keys is not None:
            args = (json.dumps(sorted(keys)),)
            mine = " AND ip IN (SELECT value FROM json_each(?))"
            # providers are kept by MAC (by address for lookups without one): written so both use the MAC index
            look = " AND (mac IN (SELECT value FROM json_each(?)) OR (mac = '' AND ip IN (SELECT value FROM json_each(?))))"
        # day < end: implied by first_seen < end, and without it SQLite skip-scans the MAC index (10x slower)
        for mac, ip, name, n in db.execute(f"SELECT mac, ip, name, sum(count) FROM lookups WHERE day >= ? AND day < ? "
                                           f"AND last_seen >= ? AND first_seen < ?{look} GROUP BY mac, ip, name",
                                           (since - 86400, end, since, end, *args * 2)):
            p = svc.vpn_provider(name)
            if p:
                counts[mac or ip][p] += n
        self.provider = {k: c.most_common(1)[0][0] for k, c in counts.items()}
        self.tunnels = {}
        # VPN ports from the service map (the user's port names replace the shipped ones)
        vpn_ports = [pp for pp, s in svc.ports.items() if s.startswith("VPN (")]
        if flows is None:
            cond = " OR ".join("(proto = ? AND port = ?)" for _ in vpn_ports) or "0"
            tunnels = db.execute(f"SELECT DISTINCT ip, server, proto, port FROM {table} WHERE bucket >= ? AND name = '' "
                                 f"AND bucket < ?{mine} AND ({cond})", (start, end, *args, *[x for pp in vpn_ports for x in pp]))
            named = db.execute(f"SELECT DISTINCT ip, server, name FROM {table} WHERE bucket >= ? AND bucket < ?{mine} "
                               f"AND name != ''", (start, end, *args))
        else:
            vpn_ports = set(vpn_ports)
            tunnels = {(f[0], f[3], f[5], f[4]) for f in flows if not f[2] and (f[5], f[4]) in vpn_ports}
            named = {(f[0], f[3], f[2]) for f in flows if f[2]}
        for ip, server, proto, port in sorted(tunnels):      # same answer every run if a server has two
            self.tunnels[(ip, server)] = svc.ports[(proto, port)]
        self.owners = collections.defaultdict(set)
        for ip, server, name in named:
            s = svc.by_my_address(server) or svc.by_name(name)
            if s and s not in HOSTING:
                self.owners[(ip, server)].add(s)
        self._share()

    @classmethod
    def from_evidence(cls, svc, rows):
        """rows: (kind, ip, server, value) as evidence() gives them."""
        self = cls.__new__(cls)
        self.svc, self.provider, self.tunnels, self.owners = svc, {}, {}, collections.defaultdict(set)
        for kind, ip, server, value in rows:
            if kind == "owner":
                self.owners[(ip, server)].add(value)
            elif kind == "tunnel":
                self.tunnels[(ip, server)] = value
            elif kind == "provider":
                self.provider[ip] = value
        self._share()
        return self

    def evidence(self):
        """What a day's names were decided from: (kind, ip, server, value). 'provider' rows hold the MAC (or the
        address, for lookups without one) in `ip`."""
        out = [("owner", ip, server, s) for (ip, server), v in self.owners.items() for s in v]
        out += [("tunnel", ip, server, t) for (ip, server), t in self.tunnels.items()]
        out += [("provider", k, "", p) for k, p in self.provider.items()]
        return out

    def merged(self, newer):
        """This evidence plus `newer`'s (traffic since it was stored): its own picks stand, new servers are added."""
        out = Labels.from_evidence(self.svc, ())
        out.provider = {**newer.provider, **self.provider}
        out.tunnels = {**newer.tunnels, **self.tunnels}
        for src in (self.owners, newer.owners):
            for k, v in src.items():
                out.owners[k] |= v
        out._share()
        return out

    def _share(self):
        self.shared = {k: next(iter(v)) for k, v in self.owners.items() if len(v) == 1}
        self._named = {}

    def named(self, ip, name, server, port, proto, mac=""):
        """(service(), inferred()) at once, remembered: a device's page names the same traffic every hour."""
        k = (ip, name, server, port, proto, mac)
        hit = self._named.get(k)
        if hit is None:
            s = self._own(ip, name, server, port, proto, mac)
            inferred = (not s or s in HOSTING) and (ip, server) in self.shared
            hit = self._named[k] = (self.shared[(ip, server)] if inferred else s, inferred)
        return hit

    def _own(self, ip, name, server, port, proto, mac):
        s = self.svc.by_my_address(server) or self.svc.by_name(name)
        if s:
            return s
        s = self.tunnels.get((ip, server)) or self.svc.service(name, server, port, proto)
        if s and s.startswith("VPN ("):
            p = self.provider.get(mac) or self.provider.get(ip)
            if p:
                return f"VPN ({p})"
        return s

    def service(self, ip, name, server, port, proto, mac=""):
        s = self._own(ip, name, server, port, proto, mac)
        if not s or s in HOSTING:
            return self.shared.get((ip, server)) or s
        return s

    def inferred(self, ip, name, server, port, proto, mac=""):
        """True when service() named this traffic from other traffic on the same server."""
        s = self._own(ip, name, server, port, proto, mac)
        return (not s or s in HOSTING) and (ip, server) in self.shared


class Namer:
    """Labels for traffic by its local day: the evidence loupd stored for that day (when built with this service
    map), else gathered from the day's hourly rows (slower), plus the evidence of the window being named."""

    def __init__(self, db, svc):
        self.db, self.svc, self.fingerprint = db, svc, map_fingerprint(svc)
        self._built, self._days = None, {}

    def built(self, day):
        """When loupd last summarized `day` with this service map (0: not, or with another map)."""
        if self._built is None:
            try:
                self._built = {d: b for d, b in self.db.execute("SELECT day, built FROM summary_days WHERE map = ?",
                                                                (self.fingerprint,))}
            except sqlite3.OperationalError:        # loupd hasn't created the summary yet (just updated)
                self._built = {}
        return self._built.get(day, 0)

    def sites_until(self, day):
        """Where loupd's site summary of `day` ends (its hourly rows before this): `day` when it has none built with
        this service map."""
        built = self.built(day)
        return max(day, sites_until(next_day(day), built)) if built else day

    def day(self, day, keys=None):
        """The day's evidence; `keys` (a tuple of addresses and MACs) limits it to those devices."""
        k = (day, keys)
        if k not in self._days:
            if self.built(day):
                sql, args = "SELECT kind, ip, server, value FROM day_names WHERE day = ?", (day,)
                if keys is not None:
                    sql += " AND ip IN (%s)" % ",".join("?" * len(keys))
                    args += keys
                self._days[k] = Labels.from_evidence(self.svc, self.db.execute(sql, args))
            else:
                self._days[k] = Labels(self.db, self.svc, "flows_1h", day, None, next_day(day), keys=keys)
        return self._days[k]

    def labels(self, day, table, start, end, flows, keys=None):
        """For rows of `table` in [start, end) (within `day`); `flows`: those rows, as for Labels. Stored evidence
        covers what loupd had written when it built the day; traffic since then adds its own."""
        built = self.built(day)
        stored = self.day(day, keys)
        if built and end <= built - SETTLE:
            return stored
        return stored.merged(Labels(self.db, self.svc, table, start, flows, end, built - SETTLE if built else None, keys))


def flow_totals(db, table, start, end=None, where="", args=()):
    """The window's traffic summed per device, server, port and name:
    (ip, mac, name, server, port, proto, up, down, conns, last)."""
    return db.execute(f"SELECT ip, mac, name, server, port, proto, sum(up), sum(down), sum(conns), "
                      f"max(max(bucket), max(last)) FROM {table} WHERE bucket >= ? AND bucket < ? {where} "
                      f"GROUP BY ip, mac, name, server, port, proto", (start, end or 1 << 62, *args)).fetchall()


def add(out, key, up, down, conns, last):
    r = out.get(key)
    if r is None:
        out[key] = [up, down, conns, last]
    else:
        r[0] += up
        r[1] += down
        r[2] += conns
        r[3] = max(r[3], last)


def add_site(out, ip, mac, name, server, service, inferred, up, down, conns, first, last):
    """Traffic per device, site and service: the site is the server's name, or its address when it has none.
    {(ip, mac, site, named, service): [inferred, up, down, conns, first, last, {servers}]}; inferred while all of
    it was named from other traffic on the same server."""
    k = (ip, mac, name or server, 1 if name else 0, service)
    r = out.get(k)
    if r is None:
        out[k] = [inferred, up, down, conns, first, last, {server}]
    else:
        r[0] = r[0] and inferred
        r[1] += up
        r[2] += down
        r[3] += conns
        r[4] = min(r[4], first)
        r[5] = max(r[5], last)
        r[6].add(server)


def site_rows(sites):
    """add_site()'s totals as rows: (ip, mac, site, named, service, inferred, up, down, conns, first, last, servers)."""
    return [(*k, *v) for k, v in sites.items()]


def sites_until(end, built):
    """The site summary of a day ending at `end`, built at `built`, holds its hourly rows before this: the whole
    day once it had ended, else the hours that had ended (and been written)."""
    return end if built - SETTLE >= end else (built - SETTLE) // 3600 * 3600


def build_day(db, svc, day, end=None, until=None):
    """One local day from the hourly rows, named with that day's evidence: ({(ip, mac, service): [up, down, conns,
    last]}, {(hour, ip, mac, service): [...]}, Labels, add_site()'s totals of the hours before `until`)."""
    end = end or next_day(day)
    until = end if until is None else until
    rows = db.execute("SELECT bucket, ip, mac, name, server, port, proto, up, down, conns, max(bucket, last) FROM flows_1h "
                      "WHERE bucket >= ? AND bucket < ?", (day, end)).fetchall()
    lab = Labels(db, svc, "flows_1h", day, [r[1:7] for r in rows], end)
    daily, hourly, sites = {}, {}, {}
    for bucket, ip, mac, name, server, port, proto, up, down, conns, last in rows:
        s, inferred = lab.named(ip, name, server, port, proto, mac)
        s = s or ""
        add(daily, (ip, mac, s), up, down, conns, last)
        add(hourly, (bucket, ip, mac, s), up, down, conns, last)
        if bucket < until:
            add_site(sites, ip, mac, name, server, s, bool(s) and inferred, up, down, conns, bucket, last)
    return daily, hourly, lab, sites


def recent_rows(namer, table, start, end):
    """Traffic per device and service in [start, end) (up to two days): [(ip, mac, service, up, down, conns, last)].
    Hours loupd has summarized come from the hourly summary; the rest is named here with the day's evidence."""
    db, out = namer.db, {}
    for day, a, b in pieces(start, end):
        built = namer.built(day)
        first = -(-a // 3600) * 3600                    # the first whole hour in the piece
        done = first
        while done + 3600 <= b and done + 3600 + SETTLE <= built:      # summarized after the hour ended
            done += 3600
        if done > first:
            for ip, mac, s, up, down, conns, last in db.execute(
                    "SELECT ip, mac, service, sum(up), sum(down), sum(conns), max(last) FROM services_1h "
                    "WHERE hour >= ? AND hour < ? GROUP BY ip, mac, service", (first, done)):
                add(out, (ip, mac, s), up, down, conns, last)
            ranges = [(a, first), (done, b)]
        else:
            ranges = [(a, b)]
        for x, y in ranges:
            if x >= y:
                continue
            flows = flow_totals(db, table, x, y)
            if not flows:
                continue
            lab = namer.labels(day, table, x, y, flows)
            for ip, mac, name, server, port, proto, up, down, conns, last in flows:
                add(out, (ip, mac, lab.service(ip, name, server, port, proto, mac) or ""), up, down, conns, last)
    return [(*k, *v) for k, v in out.items()]
