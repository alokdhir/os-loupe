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

"""Per-connection byte counts from pf's state table.

Every connection through the router has a pf state with byte counters for both
directions, and the LAN-side state names the device before NAT. Polling
`pfctl -ss -vv` and diffing counters gives exact per-device up/down bytes per
server in small time slices.

Why not the flow log: OPNsense's netflow on a LAN bridge misses LAN->internet
packets on ingress (uploads only appear post-NAT on the WAN, with the public
address), and long connections are only exported every 30 minutes.

Poll at most every 10 s: pf keeps closed TCP states >= 45 s (finwait) and idle
UDP >= 30 s (udp.single), so every state is seen with its final counters.
"""
import ipaddress
import re
import subprocess

from . import netif, pfnl
from typing import NamedTuple

HEADER = re.compile(r"^\S+ (\S+) (.+?) (<-|->) (.+?)\s{2,}\S*\s*$")
COUNTS = re.compile(r"(\d+):(\d+) pkts, (\d+):(\d+) bytes")
IDLINE = re.compile(r"id: ([0-9a-f]+) creatorid: ([0-9a-f]+)")
AGE = re.compile(r"age (\d+):(\d+):(\d+)")


class State(NamedTuple):
    key: tuple          # (id, creatorid)
    origif: str
    proto: str
    local: str          # LAN device address (pre-NAT)
    lport: int
    remote: str
    rport: int
    outbound: bool      # True: the LAN device opened the connection
    up: int             # bytes sent by the device
    down: int           # bytes received by the device
    pkts_up: int
    pkts_down: int
    age: int            # seconds since the state was created


def split_endpoint(tok):
    """'1.2.3.4:443', '2001:db8::1[443]', 'a (b)' -> (addr, port). Prefers the pre-NAT address in parens."""
    if tok.endswith(")"):
        i = tok.find(" (")
        if i >= 0:
            tok = tok[i + 2:-1]
    if tok.endswith("]"):
        i = tok.find("[")
        if i >= 0:
            return tok[:i], int(tok[i + 1:-1])
    if tok.count(":") == 1:
        addr, port = tok.split(":")
        return addr, int(port)
    return tok, 0


RECORD = re.compile(r"\n(?=\S)")      # a state starts on an unindented line


def _cached(fn, cache):
    def get(addr):
        hit = cache.get(addr)
        if hit is None:
            if len(cache) > 200000:
                cache.clear()
            hit = cache[addr] = fn(addr)
        return hit
    return get


def _state(key, origif, proto, left, right, inbound, counts, age, local, group):
    """One pf state as Loupe sees it, or None. `left`/`right` are (addr, port) as pfctl prints them
    ("left <- right" for an inbound state); counts = (pkts0, pkts1, bytes0, bytes1), initiator first."""
    (ia, ip_), (ra, rp) = (right, left) if inbound else (left, right)
    il, rl = local(ia), local(ra)
    if il == rl or group(ra if il else ia):
        return None
    p_init, p_resp, b_init, b_resp = counts
    if il:
        return State(key, origif, proto, ia, ip_, ra, rp, True, b_init, b_resp, p_init, p_resp, age)
    return State(key, origif, proto, ra, rp, ia, ip_, False, b_resp, b_init, p_resp, p_init, age)


def parse(text, is_local, ifaces=None):
    """Parse `pfctl -ss -vv` output. Keeps states with exactly one local endpoint
    (on `ifaces`, if given) and returns {key: State}.

    Hot path (every 10 s, ~half the records are the WAN-side twin): the interface is
    checked before anything else is parsed, and address checks are cached per call."""
    out = {}
    local = _cached(is_local, {})
    group = _cached(is_group, {})
    for rec in RECORD.split(text):
        o = rec.find("origif: ")
        if o < 0:
            continue
        e = rec.find("\n", o)
        origif = rec[o + 8:e if e >= 0 else len(rec)].strip()
        if ifaces is not None and origif not in ifaces:
            continue
        nl = rec.find("\n")
        if nl < 0:
            continue
        m = HEADER.match(rec[:nl])
        c = COUNTS.search(rec, nl)
        i = IDLINE.search(rec, nl)
        if not (m and c and i):
            continue
        proto, left, arrow, right = m.groups()
        g = AGE.search(rec, nl)
        age = int(g.group(1)) * 3600 + int(g.group(2)) * 60 + int(g.group(3)) if g else 0
        key = (i.group(1), i.group(2))
        st = _state(key, origif, proto, split_endpoint(left), split_endpoint(right), arrow == "<-",
                    tuple(int(x) for x in c.groups()), age, local, group)
        if st:
            out[key] = st
    return out


def from_netlink(states, is_local, ifaces=None):
    """The same as parse(), from pfnl.Client.states()."""
    out = {}
    local = _cached(is_local, {})
    group = _cached(is_group, {})
    names = protocol_names()
    for s in states:
        if ifaces is not None and s["origif"] not in ifaces:
            continue
        key = (f"{s['id']:016x}", f"{s['creatorid']:08x}")
        st = _state(key, s["origif"], names.get(s["proto"], str(s["proto"])), (s["addr1"], s["port1"]),
                    (s["addr0"], s["port0"]), s["inbound"], (*s["packets"], *s["bytes"]), s["age"], local, group)
        if st:
            out[key] = st
    return out


_protocols = None


def protocol_names(path="/etc/protocols"):
    """Protocol number -> name, as pfctl prints it (tcp, udp, icmp, ipv6-icmp, ...)."""
    global _protocols
    if _protocols is None:
        _protocols = {6: "tcp", 17: "udp", 1: "icmp", 58: "ipv6-icmp"}
        try:
            with open(path) as f:
                for line in f:
                    p = line.split("#", 1)[0].split()
                    if len(p) >= 2 and p[1].isdigit():
                        _protocols.setdefault(int(p[1]), p[0])
        except OSError:
            pass
    return _protocols


def is_group(addr):
    """Multicast/broadcast destinations (mDNS, SSDP, ...) are not internet traffic."""
    try:
        a = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    return a.is_multicast or a == ipaddress.IPv4Address("255.255.255.255")


_client = None
source = None           # "netlink" or "pfctl": where the last snapshot came from


def snapshot(is_local, ifaces=None):
    """Current states, from pf over netlink; `pfctl -ss -vv` text for this poll if netlink fails
    (it is tried again next time)."""
    global _client, source
    try:
        if _client is None:
            _client = pfnl.Client()
        states = from_netlink(_client.states(ifaces), is_local, ifaces)
        source = "netlink"
        return states
    except OSError:
        if _client is not None:
            _client.close()
        _client = None
    source = "pfctl"
    text = subprocess.run(["pfctl", "-ss", "-vv"], capture_output=True, text=True, check=True).stdout
    return parse(text, is_local, ifaces)


def local_matcher(networks):
    nets = [ipaddress.ip_network(n, strict=False) for n in networks]

    def is_local(addr):
        try:
            a = ipaddress.ip_address(addr.split("%")[0])
        except ValueError:
            return False
        return any(a in n for n in nets if n.version == a.version)
    return is_local


def interface_networks(ifaces):
    """IPv4/IPv6 networks configured on the given interfaces (link-local excluded)."""
    return [str(i.network) for _, i in netif.addresses(set(ifaces)) if not i.ip.is_link_local]


class Delta(NamedTuple):
    state: State
    up: int
    down: int
    pkts_up: int
    pkts_down: int
    new: bool           # first time this connection is counted


class Tracker:
    """Turns successive snapshots into byte deltas. The first snapshot is a baseline:
    bytes moved before loupe started are not counted, except for states younger than
    `fresh` seconds (they began just now)."""

    def __init__(self, fresh=15):
        self.prev = None
        self.fresh = fresh

    def update(self, states):
        deltas = []
        first = self.prev is None
        prev = self.prev or {}
        for key, st in states.items():
            old = prev.get(key)
            if old is not None and (st.up < old.up or st.down < old.down):
                old = None   # id reused / counters reset
            if old is None:
                if first and st.age > self.fresh:
                    continue
                d = Delta(st, st.up, st.down, st.pkts_up, st.pkts_down, True)
            else:
                d = Delta(st, st.up - old.up, st.down - old.down,
                          st.pkts_up - old.pkts_up, st.pkts_down - old.pkts_down, False)
                if not (d.up or d.down):
                    continue
            deltas.append(d)
        self.prev = states
        return deltas
