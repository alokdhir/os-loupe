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

"""Friendly service names: domain suffix -> service, plus address-range and port fallbacks.

A domain pattern may start with a label prefix: "vod-hulu-*.media.dssott.com" matches vod-hulu-akc-na.media.dssott.com
but not other names under media.dssott.com (a service streaming from another company's servers).

Applied when reports are built (not stored), so edits to the map apply to all history.
Users add or override suffixes in the settings; those win over the built-in table.
"""
import ipaddress
import json
import os

DATA_JSON = os.path.join(os.path.dirname(__file__), "..", "data", "services.json")

with open(DATA_JSON, encoding="utf-8") as _f:
    _data = json.load(_f)
SUFFIXES = _data["suffixes"]                                       # domain suffix -> service
RANGES = [tuple(x) for x in _data["ranges"]]                        # address ranges -> owner, for nameless traffic
PORTS = {(proto, port): name for proto, port, name in _data["ports"]}   # (proto, remote port) -> service, last resort
VPN_PROVIDERS = _data["vpn_providers"]                              # domains a VPN app uses -> provider
HOSTING = set(_data["hosting"])                                     # names that only say whose servers these are


def as_network(text):
    """An IP address or range from the settings, or None for a domain."""
    try:
        return ipaddress.ip_network(text.strip(), strict=False)
    except ValueError:
        return None


def as_port(text):
    """'tcp/8443' -> ('tcp', 8443), else None."""
    proto, _, port = text.strip().lower().partition("/")
    if proto in ("tcp", "udp") and port.isdigit() and 0 < int(port) < 65536:
        return proto, int(port)
    return None


class AddressTable:
    """Networks -> names; get() gives the first network in the list that holds an address, like scanning the list,
    with one dict lookup per prefix length and the answer remembered per address (reports ask for the same
    servers over and over)."""

    def __init__(self, nets):
        self.by_len = {}                        # (version, prefix length) -> {network as int: (list position, name)}
        for i, (net, s) in enumerate(nets):
            self.by_len.setdefault((net.version, net.prefixlen), {}).setdefault(int(net.network_address), (i, s))
        self._cache = {}

    def __bool__(self):
        return bool(self.by_len)

    def get(self, addr):
        try:
            return self._cache[addr]
        except KeyError:
            pass
        hit = None
        try:
            a = ipaddress.ip_address(addr)
        except ValueError:
            a = None
        if a is not None:
            n, bits = int(a), a.max_prefixlen
            found = [t[(n >> (bits - plen)) << (bits - plen)] for (v, plen), t in self.by_len.items()
                     if v == a.version and (n >> (bits - plen)) << (bits - plen) in t]
            if found:
                hit = min(found)[1]
        self._cache[addr] = hit
        return hit


class ServiceMap:
    def __init__(self, extra=None):
        """`extra`: the user's entries, the same kinds as the shipped file - a list of {"kind": domain | address |
        port | vpn, "pattern", "name"} (an older {pattern: name} dict means domains and addresses). Addresses are
        checked before any name; the user's domains, ports and VPN app domains win over the shipped ones."""
        self.suffixes = dict(SUFFIXES)
        self.ports = dict(PORTS)
        self.vpn = dict(VPN_PROVIDERS)
        mine = []
        if isinstance(extra, dict):
            extra = [{"kind": "", "pattern": k, "name": v} for k, v in extra.items()]
        for e in extra or []:
            kind, pattern, name = e.get("kind") or "", str(e.get("pattern") or "").strip(), e.get("name") or ""
            if not pattern or not name:
                continue
            if kind == "port":
                p = as_port(pattern)
                if p:
                    self.ports[p] = name
            elif kind == "vpn":
                self.vpn[pattern.lower().strip(".")] = name
            elif kind in ("address", "") and as_network(pattern) is not None:
                mine.append((as_network(pattern), name))
            elif kind in ("domain", ""):
                self.suffixes[pattern.lower().strip(".")] = name
        self.mine = AddressTable(sorted(mine, key=lambda x: -x[0].prefixlen))   # most specific first
        self.ranges = AddressTable((ipaddress.ip_network(n), s) for n, s in RANGES)
        self.prefixed = {}                                            # suffix -> [(first-label prefix, service)]
        for pattern, s in self.suffixes.items():
            label, star, rest = pattern.partition("*.")
            if star and label and "." not in label:
                self.prefixed.setdefault(rest, []).append((label, s))
        for v in self.prefixed.values():
            v.sort(key=lambda x: -len(x[0]))                          # longest prefix first
        self._cache = {}

    def by_my_address(self, addr):
        """The user's own name for this server address, if they gave one."""
        if not self.mine:
            return None
        return self.mine.get(addr)

    def by_name(self, name):
        if not name:
            return None
        hit = self._cache.get(name)
        if hit is None:
            hit = ""
            parts = name.lower().rstrip(".").split(".")
            for i in range(len(parts)):          # longest suffix first; a prefixed label just ahead of the next one
                s = self.suffixes.get(".".join(parts[i:]))
                if not s and self.prefixed:
                    s = next((s for p, s in self.prefixed.get(".".join(parts[i + 1:]), ()) if parts[i].startswith(p)), None)
                if s:
                    hit = s
                    break
            self._cache[name] = hit
        return hit or None

    def by_address(self, addr):
        return self.ranges.get(addr)

    def service(self, name, server, port, proto):
        s = self.by_my_address(server) or self.by_name(name)
        if s:
            return s
        owner = self.by_address(server)
        if owner == "Apple" and proto == "udp" and port == 443:
            return "iCloud Private Relay"   # QUIC to Apple without a name we could see
        return owner or self.ports.get((proto, port))

    def vpn_provider(self, name):
        """The VPN provider whose app uses this domain (to name the provider behind a bare tunnel)."""
        parts = (name or "").lower().rstrip(".").split(".")
        for i in range(len(parts) - 1):
            p = self.vpn.get(".".join(parts[i:]))
            if p:
                return p
        return None
