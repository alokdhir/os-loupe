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

"""Give each connection a server name.

Best first:
  sni   - the TLS/QUIC ClientHello of this exact connection (client ip+port, server ip+port)
  dns   - a name this client looked up that answered with the server's address
  dns*  - a name any client looked up that answered with the server's address
A connection keeps the name it got first; it is never renamed mid-life.
"""
import time

HELLO_TTL = 600       # keep unmatched hellos this long (pf sees the state within one poll)
DNS_MIN_TTL = 300     # remember DNS answers at least this long, whatever their TTL
DNS_MAX_TTL = 6 * 3600


class Names:
    def __init__(self):
        self.hellos = {}      # (client, cport, server, sport) -> (name, source, ts)
        self.dns = {}         # (client, addr) -> (qname, expires)
        self.dns_any = {}     # addr -> (qname, expires)
        self.conn = {}        # pf state key -> (name, source)

    def tls(self, ev, source="sni"):
        if ev.get("sni"):
            self.hellos[(ev["client"], ev["cport"], ev["server"], ev["port"])] = (ev["sni"], source, ev["ts"])

    def dns_answer(self, ev):
        exp = ev["ts"] + min(max(ev.get("ttl") or 0, DNS_MIN_TTL), DNS_MAX_TTL)
        for addr in ev["addrs"]:
            self.dns[(ev["client"], addr)] = (ev["name"], exp)
            self.dns_any[addr] = (ev["name"], exp)

    def seed_unbound(self, text, now):
        """Seed the any-client DNS map from `unbound-control dump_cache`, so connections
        opened before loupe started still get a name."""
        cname = {}
        addrs = []
        for line in text.splitlines():
            f = line.split()
            if len(f) < 5 or f[2] != "IN":
                continue
            name, rtype, value = f[0].rstrip(".").lower(), f[3], f[4].rstrip(".").lower()
            if rtype == "CNAME":
                cname[value] = name
            elif rtype in ("A", "AAAA"):
                addrs.append((name, value))
        for name, addr in addrs:
            seen = set()
            while name in cname and name not in seen:   # report the name the client asked for
                seen.add(name)
                name = cname[name]
            self.dns_any.setdefault(addr, (name, now + DNS_MIN_TTL))

    def name_for(self, st, now=None):
        """Name for a pfstate.State; remembered per state for its lifetime."""
        got = self.conn.get(st.key)
        if got is not None and got[0] is not None:
            return got
        now = now or time.time()
        h = self.hellos.pop((st.local, st.lport, st.remote, st.rport), None)
        if h:
            got = (h[0], h[1])
        else:
            d = self.dns.get((st.local, st.remote))
            if d and d[1] >= now:
                got = (d[0], "dns")
            else:
                d = self.dns_any.get(st.remote)
                got = (d[0], "dns*") if d and d[1] >= now else (None, None)
        # unnamed connections are retried on later polls (the hello may still be in flight)
        self.conn[st.key] = got
        return got

    def expire(self, live_keys, now=None):
        now = now or time.time()
        self.conn = {k: v for k, v in self.conn.items() if k in live_keys}
        self.hellos = {k: v for k, v in self.hellos.items() if now - v[2] < HELLO_TTL}
        self.dns = {k: v for k, v in self.dns.items() if v[1] >= now}
        self.dns_any = {k: v for k, v in self.dns_any.items() if v[1] >= now}
