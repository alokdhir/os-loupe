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
import sys
import syslog
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from capture import Capture  # noqa: E402
from lib import devid, names, neighbours, pfstate, report, services, store, unbound  # noqa: E402
from lib.report import local_day  # noqa: E402


MAX_BUFFERED = 200000       # unwritten rows kept while the database is failing


class Loupe:
    def __init__(self, ifaces, db, poll=10, flush=60, retention=(30, 365, 30), service_map=None):
        self.ifaces = set(ifaces)
        self.is_local = pfstate.local_matcher(pfstate.interface_networks(ifaces))
        self.store = store.Store(db)
        self.names = names.Names()
        self.tracker = pfstate.Tracker(fresh=poll)
        self.poll_every, self.flush_every, self.retention = poll, flush, retention
        self.next_poll = self.next_flush = 0        # schedule: time.monotonic() (a clock step must not stall it)
        self.next_prune = time.monotonic() + 300
        self.next_classify = time.monotonic() + 30
        self.next_summary = time.monotonic() + 45
        self.svc = services.ServiceMap(service_map)
        self.map_fingerprint = report.map_fingerprint(self.svc)
        self.stopping = False
        self.oui = devid.load_oui()
        self.ip_mac = {}
        self.flows = {}
        self.lookups = {}
        self.devices = collections.defaultdict(dict)
        self.live = set()
        self.error_hour, self.errors_logged = None, 0
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
        if kind == "error":                  # a packet capture could not handle (logged, at most 10 an hour)
            hour = int(ev.get("ts", 0) // 3600)
            if hour != self.error_hour:
                self.error_hour, self.errors_logged = hour, 0
            if self.errors_logged < 10:
                self.errors_logged += 1
                syslog.syslog(syslog.LOG_WARNING, f"loupe: skipped a packet from {ev.get('src')}: {ev.get('err')}")
            return
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
            seen = info.setdefault("seen", {})          # when each item was last announced (devid.current_mdns)
            hosts = [h for h in ev.get("hosts", {}) if h.endswith(".local")]
            for k, items in (("models", ev.get("models")), ("names", ev.get("names")),
                             ("services", ev.get("services")), ("hosts", hosts)):
                if items:
                    info[f"mdns_{k}"] = sorted(set(info.get(f"mdns_{k}", [])) | set(items))
                    for item in items:
                        seen[devid.seen_key(k, item)] = int(ev["ts"])

    def lookup(self, ts, ip, name, source):
        k = (local_day(ts), ip, name, source)
        v = self.lookups.get(k)
        if v is None:
            self.lookups[k] = [1, int(ts), int(ts), self.ip_mac.get(ip, "")]
        else:
            v[0] += 1
            v[2] = int(ts)

    # ---- periodic work --------------------------------------------------------------
    def tick(self, now):
        """`now` is wall-clock time for the data; the schedule runs on the monotonic clock."""
        mono = time.monotonic()
        if self.stopping:               # the final write happens here, never inside the signal handler
            try:
                self.guarded("final flush", self.flush, now)
            finally:
                sys.exit(0)
        if mono >= self.next_poll:
            self.next_poll = mono + self.poll_every
            self.guarded("pf poll", self.poll, now)
        # a database or system error is logged and retried later; it must not stop the daemon
        # (flush keeps its buffered minute until a write succeeds)
        if mono >= self.next_flush:
            self.next_flush = mono + self.flush_every
            self.guarded("flush", self.flush, now)
        if mono >= self.next_classify:
            # right after the minute's write: new evidence (a fresh Bonjour model, a lease) counts within a
            # minute. A pass over every device takes ~0.2 s.
            self.next_classify = mono + self.flush_every
            self.guarded("networks", self.refresh_networks)
            self.guarded("classify", self.classify, now)
        if mono >= self.next_summary:
            self.next_summary = mono + self.flush_every
            self.guarded("summary", self.summarize, now)
        if mono >= self.next_prune:
            self.next_prune = mono + 3600
            self.guarded("prune", self.store.prune, now, *self.retention)

    @staticmethod
    def guarded(what, fn, *args):
        try:
            fn(*args)
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"loupe: {what} failed: {e!r}")

    def refresh_networks(self):
        """LAN networks can change while running (e.g. a delegated IPv6 prefix arrives). An interface
        briefly without addresses (being reconfigured) keeps the networks it had."""
        nets = pfstate.interface_networks(self.ifaces)
        if nets:
            self.is_local = pfstate.local_matcher(nets)

    def classify(self, now):
        """Store what loupe detects. Names/types set in the GUI are layered on at report time
        (query.py), so editing or resetting them shows up immediately."""
        leases, hosts = devid.leases(), devid.static_hosts()
        rows = []
        for mac, ip, hostname, info, seen in self.store.devices_with_names(now - 7 * 86400):
            info = devid.current_mdns(info)     # what the device announces now outranks older announcements
            name = leases.get(mac) or hosts.get(ip)
            t, src, ven = devid.classify(mac, name or hostname, info, seen, self.oui)
            rows.append((devid.display_name(name, hostname, devid.mdns_name(info)), ven, t, src, devid.model(info), mac))
        self.store.set_device_types(rows)

    def summarize(self, now, budget=1.0):
        """Keep the summaries current: today every 10 minutes, a day again once it has ended, and every day after
        the service map changed - newest first, about `budget` seconds of work a call."""
        oldest = self.store.oldest_hour()
        if oldest is None:
            return
        first, today = local_day(oldest), local_day(now)
        state = self.store.summary_state(first)
        due = []
        for d in reversed(list(report.days(first, today))):
            fp, built = state.get(d, (None, 0))
            if fp != self.map_fingerprint or (now - built >= 600 if d == today else built < report.next_day(d) + report.SETTLE):
                due.append(d)
        t0 = time.monotonic()
        recent = local_day(now - report.HOURLY_DAYS * 86400)
        for d in due:
            end = report.next_day(d)
            daily, hourly, lab = report.build_day(self.store.db, self.svc, d, end)
            self.store.replace_day(d, end, daily, hourly if d >= recent else None, lab.evidence(), self.map_fingerprint, now)
            if time.monotonic() - t0 >= budget:
                break

    def refresh_macs(self):
        self.ip_mac.update(neighbours.table())

    def poll(self, now):
        states = pfstate.snapshot(self.is_local, self.ifaces)
        self.live = set(states)
        bucket = int(now // 60 * 60)          # minute; the store derives 5-minute and hourly rows
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
        self.guarded("neighbour table", self.refresh_macs)     # best effort: rows get their MAC next time
        for (_b, ip, *_rest), v in self.flows.items():
            if not v[0]:
                v[0] = self.ip_mac.get(ip, "")
            if v[0]:
                d = self.devices[v[0]]
                if ":" not in ip:
                    d.setdefault("ip", ip)      # IPv4 only: static mappings and fallbacks match IPv4
                d["ts"] = max(d.get("ts", 0), now)
        for (_d, ip, *_rest), v in self.lookups.items():
            if not v[3]:
                v[3] = self.ip_mac.get(ip, "")
        devices = {m: d for m, d in self.devices.items() if "ts" in d}
        self.names.expire(self.live, now)      # also while the database fails: it must not grow
        try:
            self.store.write(self.flows, self.lookups, devices, now)
        except Exception:
            if len(self.flows) + len(self.lookups) > MAX_BUFFERED:     # the database has been failing for a while
                syslog.syslog(syslog.LOG_ERR, f"loupe: dropping {len(self.flows)} unwritten traffic rows")
                self.flows, self.lookups = {}, {}
                self.devices = collections.defaultdict(dict)
            raise
        self.flows, self.lookups = {}, {}
        self.devices = collections.defaultdict(dict)


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
               (r.get("days_5m", 30), r.get("days_1h", 365), r.get("days_lookups", 30)), conf.get("services"))
    cap = Capture(lp.emit)

    def stop(*_):
        lp.stopping = True              # tick() does the final write and exits (within a second)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    syslog.syslog(syslog.LOG_NOTICE, f"loupe: started on {' '.join(ifaces)}")
    cap.run(ifaces, tick=lp.tick)


if __name__ == "__main__":
    main()
