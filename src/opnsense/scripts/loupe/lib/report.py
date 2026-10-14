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

"""Traffic named by service for a report window: shared by the reports (query.py) and loupd's daily summary."""
import collections
import hashlib
import json
import time

from .services import DATA_JSON, HOSTING

SUMMARY_VERSION = 1     # raise when the labelling changes, so loupd builds the daily summary again


def summarize_day(db, svc, day):
    """One local day of the daily summary, from the hourly rows. Labelled from that day alone: nameless traffic
    takes a service from named traffic to the same server that day, not from other days (see DESIGN.md)."""
    return summarize(db, svc, "flows_1h", day, next_day(day))


def map_fingerprint(extra):
    """Changes when the shipped service map, the user's entries or the labelling code change."""
    h = hashlib.sha256(str(SUMMARY_VERSION).encode())
    with open(DATA_JSON, "rb") as f:
        h.update(f.read())
    h.update(json.dumps(extra, sort_keys=True).encode())
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


class Labels:
    """Service names for one report window, with VPN tunnels told apart.

    A device that looked up a VPN provider's domain gets "VPN (NordVPN)" instead of "VPN (WireGuard)", and other
    nameless traffic to a server it uses as a VPN (e.g. a TCP fallback) is counted as that VPN too.

    Traffic with no service, or one that only names the hosting company (Fastly CDN), takes the service of the
    other traffic between the same device and server in the window, if that is exactly one service: a stream from a
    video vendor's domain on the servers the NFL app's own names used is NFL. `inferred()` tells such rows apart.
    """

    def __init__(self, db, svc, table, start, flows=None, end=None):
        """`flows`: the window's rows from flow_totals(), when the caller has them (saves two scans of the table).
        `end`: the window ends there instead of now (a past day being summarized)."""
        self.svc = svc
        counts = collections.defaultdict(collections.Counter)      # by MAC; lookups without one by address
        end = end or 1 << 62
        for mac, ip, name, n in db.execute("SELECT mac, ip, name, sum(count) FROM lookups WHERE day >= ? AND last_seen >= ? "
                                           "AND first_seen < ? GROUP BY mac, ip, name", (start - 86400, start - 86400, end)):
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
                                 f"AND bucket < ? AND ({cond})", (start, end, *[x for pp in vpn_ports for x in pp]))
            named = db.execute(f"SELECT DISTINCT ip, server, name FROM {table} WHERE bucket >= ? AND bucket < ? AND name != ''",
                               (start, end))
        else:
            vpn_ports = set(vpn_ports)
            tunnels = {(f[0], f[3], f[5], f[4]) for f in flows if not f[2] and (f[5], f[4]) in vpn_ports}
            named = {(f[0], f[3], f[2]) for f in flows if f[2]}
        for ip, server, proto, port in sorted(tunnels):      # same answer every run if a server has two
            self.tunnels[(ip, server)] = svc.ports[(proto, port)]
        owners = collections.defaultdict(set)
        for ip, server, name in named:
            s = svc.by_my_address(server) or svc.by_name(name)
            if s and s not in HOSTING:
                owners[(ip, server)].add(s)
        self.shared = {k: v.pop() for k, v in owners.items() if len(v) == 1}

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


def flow_totals(db, table, start, end=None):
    """The window's traffic summed per device, server, port and name:
    (ip, mac, name, server, port, proto, up, down, conns, last)."""
    return db.execute(f"SELECT ip, mac, name, server, port, proto, sum(up), sum(down), sum(conns), "
                      f"max(max(bucket), max(last)) FROM {table} WHERE bucket >= ? AND bucket < ? "
                      f"GROUP BY ip, mac, name, server, port, proto", (start, end or 1 << 62)).fetchall()


def summarize(db, svc, table, start, end=None):
    """The window's traffic per device and service ('' = none): [(ip, mac, service, up, down, conns, last)].
    What the device and service lists are made from, and what loupd keeps per day for long periods."""
    flows = flow_totals(db, table, start, end)
    lab = Labels(db, svc, table, start, flows, end)
    out = {}
    for ip, mac, name, server, port, proto, up, down, conns, last in flows:
        k = (ip, mac, lab.service(ip, name, server, port, proto, mac) or "")
        r = out.get(k)
        if r is None:
            out[k] = [up, down, conns, last]
        else:
            r[0] += up
            r[1] += down
            r[2] += conns
            r[3] = max(r[3], last)
    return [(*k, *v) for k, v in out.items()]
