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

"""loupe daemon: capture names, poll pf byte counters, write per-device traffic to SQLite.

usage: loupd.py [--config /usr/local/etc/loupe.json] [--db PATH] [--poll S] [--flush S] [IFACE ...]
"""
import argparse
import collections
import json
import signal
import ssl
import subprocess
import sys
import syslog
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from capture import Capture  # noqa: E402
from lib import devid, names, neighbours, pfstate, store, unbound  # noqa: E402



def local_day(ts):
    t = time.localtime(ts)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


class Loupe:
    def __init__(self, ifaces, db, poll=10, flush=60, retention=(30, 365, 30)):
        self.ifaces = set(ifaces)
        self.is_local = pfstate.local_matcher(pfstate.interface_networks(ifaces))
        self.store = store.Store(db)
        self.names = names.Names()
        self.tracker = pfstate.Tracker(fresh=poll)
        self.poll_every, self.flush_every, self.retention = poll, flush, retention
        self.next_poll = self.next_flush = 0
        self.next_prune = time.time() + 300
        self.next_classify = time.time() + 30
        self.oui = devid.load_oui()
        self.ip_mac = {}
        self.flows = {}
        self.lookups = {}
        self.devices = collections.defaultdict(dict)
        self.live = set()
        self.state_source = None
        self.seed_names()

    def seed_names(self):
        try:
            out = unbound.command("dump_cache", timeout=30)
        except (OSError, ssl.SSLError):         # Unbound not running or not in use: no seed
            return
        self.names.seed_unbound(out, time.time())

    # ---- capture events -------------------------------------------------------------
    def emit(self, ev):
        kind = ev["ev"]
        if kind in ("tls", "quic"):
            if not self.is_local(ev["client"]) or self.is_local(ev["server"]):
                return  # LAN<->LAN TLS (AirPlay, lockdownd, ...) carries no site
            self.names.tls(ev, "sni" if kind == "tls" else "quic")
            self.ip_mac.setdefault(ev["client"], ev["mac"])
            if ev.get("sni"):
                self.lookup(ev["ts"], ev["client"], ev["sni"], kind if kind == "quic" else "sni")
        elif kind == "dns":
            if self.is_local(ev["client"]):
                self.names.dns_answer(ev)
                self.lookup(ev["ts"], ev["client"], ev["name"], "dns")
        elif kind == "dhcp":
            d = self.devices[ev["mac"]]
            d["ts"] = ev["ts"]
            if ev.get("hostname"):
                d["hostname"] = ev["hostname"]
            info = d.setdefault("info", {})
            for k in ("vendor_class", "params", "fqdn"):
                if ev.get(k):
                    info[f"dhcp_{k}"] = ev[k]
        elif kind == "mdns":
            d = self.devices[ev["mac"]]
            d["ts"] = ev["ts"]
            if ":" not in ev["client"]:
                d["ip"] = ev["client"]    # mDNS often comes from the IPv6 link-local address
            info = d.setdefault("info", {})
            for k in ("models", "names", "services"):
                if ev.get(k):
                    info[f"mdns_{k}"] = sorted(set(info.get(f"mdns_{k}", [])) | set(ev[k]))
            hosts = [h for h in ev.get("hosts", {}) if h.endswith(".local")]
            if hosts:
                info["mdns_hosts"] = sorted(set(info.get("mdns_hosts", [])) | set(hosts))

    def lookup(self, ts, ip, name, source):
        k = (local_day(ts), ip, name, source)
        v = self.lookups.get(k)
        if v is None:
            self.lookups[k] = [1, int(ts), int(ts)]
        else:
            v[0] += 1
            v[2] = int(ts)

    # ---- periodic work --------------------------------------------------------------
    def tick(self, now):
        if now >= self.next_poll:
            self.next_poll = now + self.poll_every
            try:
                self.poll(now)
            except (subprocess.SubprocessError, OSError) as e:
                syslog.syslog(syslog.LOG_ERR, f"loupe: pf poll failed: {e!r}")
        # a database or system error is logged and retried later; it must not stop the daemon
        # (flush keeps its buffered minute until a write succeeds)
        if now >= self.next_flush:
            self.next_flush = now + self.flush_every
            self.guarded("flush", self.flush, now)
        if now >= self.next_classify:
            self.next_classify = now + 600
            self.guarded("networks", self.refresh_networks)
            self.guarded("classify", self.classify, now)
        if now >= self.next_prune:
            self.next_prune = now + 3600
            self.guarded("prune", self.store.prune, now, *self.retention)

    @staticmethod
    def guarded(what, fn, *args):
        try:
            fn(*args)
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"loupe: {what} failed: {e!r}")

    def refresh_networks(self):
        """LAN networks can change while running (e.g. a delegated IPv6 prefix arrives)."""
        self.is_local = pfstate.local_matcher(pfstate.interface_networks(self.ifaces))

    def classify(self, now):
        """Store what loupe detects. Names/types set in the GUI are layered on at report time
        (query.py), so editing or resetting them shows up immediately."""
        leases, hosts = devid.leases(), devid.static_hosts()
        rows = []
        for mac, ip, hostname, info, seen in self.store.devices_with_names(now - 7 * 86400):
            name = leases.get(mac) or hosts.get(ip)
            t, src, ven = devid.classify(mac, name or hostname, info, seen, self.oui)
            rows.append((devid.display_name(name, hostname, devid.mdns_name(info)), ven, t, src, devid.model(info), mac))
        self.store.set_device_types(rows)

    def refresh_macs(self):
        self.ip_mac.update(neighbours.table())

    def poll(self, now):
        states = pfstate.snapshot(self.is_local, self.ifaces)
        if pfstate.source != self.state_source:
            self.state_source = pfstate.source
            syslog.syslog(syslog.LOG_NOTICE, f"loupe: reading pf states via {pfstate.source}")
        self.live = set(states)
        bucket = int(now // 300 * 300)
        for d in self.tracker.update(states):
            st = d.state
            name, source = self.names.name_for(st, now)
            k = (bucket, st.local, st.remote, st.rport if st.outbound else st.lport, st.proto, name or "")
            v = self.flows.get(k)
            if v is None:
                v = self.flows[k] = [self.ip_mac.get(st.local, ""), source or "", int(not st.outbound), 0, 0, 0, 0]
            v[3] += d.up
            v[4] += d.down
            v[5] += d.pkts_up + d.pkts_down
            v[6] += d.new

    def flush(self, now):
        self.refresh_macs()
        for (_b, ip, *_rest), v in self.flows.items():
            if not v[0]:
                v[0] = self.ip_mac.get(ip, "")
            if v[0]:
                d = self.devices[v[0]]
                d.setdefault("ip", ip)
                d["ts"] = max(d.get("ts", 0), now)
        devices = {m: d for m, d in self.devices.items() if "ts" in d}
        self.store.write(self.flows, self.lookups, devices, now)
        self.flows, self.lookups = {}, {}
        self.devices = collections.defaultdict(dict)
        self.names.expire(self.live, now)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="JSON written by the OPNsense template")
    ap.add_argument("--db", default="/var/db/loupe/loupe.db")
    ap.add_argument("--poll", type=float, default=10)
    ap.add_argument("--flush", type=float, default=60)
    ap.add_argument("ifaces", nargs="*")
    args = ap.parse_args()
    syslog.openlog("loupe", syslog.LOG_PID, syslog.LOG_DAEMON)
    conf = {}
    if args.config:
        with open(args.config) as f:
            conf = json.load(f)
    ifaces = args.ifaces or [i for i in conf.get("interfaces", []) if i]
    if not ifaces:
        syslog.syslog(syslog.LOG_ERR, "loupe: no interfaces configured")
        sys.exit(1)
    for rule, problem in devid.set_user_rules(conf.get("rules", [])):
        syslog.syslog(syslog.LOG_WARNING, f"loupe: skipping device rule {json.dumps(rule)}: {problem}")
    r = conf.get("retention", {})
    lp = Loupe(ifaces, args.db, args.poll, args.flush,
               (r.get("days_5m", 30), r.get("days_1h", 365), r.get("days_lookups", 30)))
    cap = Capture(lp.emit)

    def stop(*_):
        lp.flush(time.time())
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    syslog.syslog(syslog.LOG_NOTICE, f"loupe: started on {' '.join(ifaces)}")
    cap.run(ifaces, tick=lp.tick)


if __name__ == "__main__":
    main()
