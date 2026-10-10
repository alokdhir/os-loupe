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

"""loupe capture: tap interfaces for TLS/QUIC ClientHellos, DNS answers, DHCP and mDNS.

usage: capture.py --print IFACE [IFACE ...]
"""
import argparse
import json
import select
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from lib import bpf, dhcp, dns, mdns, packet, quic, tls  # noqa: E402

# Kernel filter: only the packets we parse ever reach userland.
#  - TCP segments whose payload starts with a TLS handshake record holding a ClientHello
#  - QUIC long-header packets on UDP 443 (Initials)
#  - DNS answers (UDP from port 53), DHCP client messages, mDNS
TLS4 = ("(ip and tcp and tcp[((tcp[12]&0xf0)>>2)] = 0x16 and tcp[((tcp[12]&0xf0)>>2)+1] = 0x03"
        " and tcp[((tcp[12]&0xf0)>>2)+5] = 0x01)")
TLS6 = ("(ip6 and ip6[6] = 6 and ip6[40+((ip6[52]&0xf0)>>2)] = 0x16 and ip6[40+((ip6[52]&0xf0)>>2)+1] = 0x03"
        " and ip6[40+((ip6[52]&0xf0)>>2)+5] = 0x01)")
QUIC4 = "(ip and udp dst port 443 and udp[8] & 0x80 != 0)"
QUIC6 = "(ip6 and udp dst port 443 and ip6[6] = 17 and ip6[48] & 0x80 != 0)"
# Continuation of a ClientHello too big for one segment (post-quantum key shares): the
# next client segment ends the write, so it has PSH, is short, and doesn't start a TLS
# record. Most bulk traffic is full-size and PSH-less, so few of these reach userland.
PL4 = "(ip[2:2] - ((ip[0]&0xf)<<2) - ((tcp[12]&0xf0)>>2))"
CONT4 = f"(ip and tcp dst port 443 and tcp[13] & 8 != 0 and {PL4} > 0 and {PL4} < 1400 and tcp[((tcp[12]&0xf0)>>2)] & 0xfc != 0x14)"
OTHER = "(udp src port 53 or udp dst port 67 or udp port 5353)"
FILTER = f"{TLS4} or {TLS6} or {QUIC4} or {QUIC6} or {CONT4} or {OTHER}"
PARTIAL_TTL = 3.0
MAX_PARTIAL = 4096


class Capture:
    def __init__(self, emit):
        self.emit = emit
        self.quic = quic.Reassembler()
        self.partial = {}  # (client, sport, server, dport) -> [next_seq, bytes, first_packet, ts]
        self.stats = {"frames": 0, "tls": 0, "tls_joined": 0, "tls_nosni": 0, "quic": 0,
                      "dns": 0, "dhcp": 0, "mdns": 0}

    def run(self, ifaces, tick=None):
        """Capture forever. `tick(now)` runs about once a second, between reads."""
        fds = {t.fileno(): t for t in (bpf.Bpf(i, FILTER) for i in ifaces)}
        while True:
            ready, _, _ = select.select(list(fds), [], [], 1.0)
            now = time.time()
            self.expire_partial(now)
            if tick is not None:
                tick(now)
            for fd in ready:
                for ts, frame in fds[fd].read():
                    self.stats["frames"] += 1
                    p = packet.decode(ts, frame)
                    if p is not None:
                        try:
                            self.handle(p)
                        except (ValueError, IndexError, UnicodeError) as e:
                            self.emit({"ev": "error", "ts": ts, "err": repr(e), "src": p.src, "sport": p.sport})

    def emit_tls(self, p, h, joined=False):
        self.stats["tls"] += 1
        if joined:
            self.stats["tls_joined"] += 1
        if not h.sni:
            self.stats["tls_nosni"] += 1
        self.emit({"ev": "tls", "ts": p.ts, "client": p.src, "cport": p.sport, "mac": p.src_mac,
                   "server": p.dst, "port": p.dport, "sni": h.sni, "alpn": list(h.alpn), "ech": h.ech, "joined": joined,
                   "seg": len(p.payload), "rec": tls.record_length(p.payload)})

    def expire_partial(self, now):
        for k in [k for k, v in self.partial.items() if now - v[3] > PARTIAL_TTL]:
            _seq, buf, first, _ts = self.partial.pop(k)
            self.emit_tls(first, tls.parse_tcp_payload(buf) or tls.Hello(None, (), False, False), joined=False)

    def handle_tcp(self, p):
        key = (p.src, p.sport, p.dst, p.dport)
        if tls.looks_like_hello(p.payload):
            h = tls.parse_tcp_payload(p.payload)
            if h is None:
                return
            if h.sni or h.complete or len(p.payload) >= tls.record_length(p.payload):
                self.emit_tls(p, h)
            else:
                if len(self.partial) >= MAX_PARTIAL:
                    self.expire_partial(float("inf"))
                self.partial[key] = [(p.seq + len(p.payload)) & 0xFFFFFFFF, p.payload, p, p.ts]
            return
        entry = self.partial.get(key)
        if entry is None or p.seq != entry[0]:
            return
        buf = entry[1] + p.payload
        h = tls.parse_tcp_payload(buf)
        if h is not None and (h.sni or h.complete or len(buf) >= tls.record_length(buf)):
            del self.partial[key]
            self.emit_tls(entry[2], h, joined=True)
        else:
            entry[0] = (p.seq + len(p.payload)) & 0xFFFFFFFF
            entry[1] = buf

    def handle(self, p):
        if p.proto == packet.PROTO_TCP:
            if p.payload:
                self.handle_tcp(p)
        elif p.proto == packet.PROTO_UDP and p.dport == 443:
            h = self.quic.feed(p.src, p.payload, p.ts)
            if h is not None:
                self.stats["quic"] += 1
                self.emit({"ev": "quic", "ts": p.ts, "client": p.src, "cport": p.sport, "mac": p.src_mac,
                           "server": p.dst, "port": p.dport, "sni": h.sni, "alpn": list(h.alpn), "ech": h.ech})
        elif p.proto == packet.PROTO_UDP and p.sport == 53:
            a = dns.answers(p.payload)
            if a:
                self.stats["dns"] += 1
                qname, addrs, chain, ttl = a
                self.emit({"ev": "dns", "ts": p.ts, "client": p.dst, "name": qname, "addrs": addrs,
                           "cname": chain, "ttl": ttl})
        elif p.proto == packet.PROTO_UDP and p.dport == 67:
            d = dhcp.parse(p.payload)
            if d:
                self.stats["dhcp"] += 1
                self.emit({"ev": "dhcp", "ts": p.ts, **d})
        elif p.proto == packet.PROTO_UDP and (p.sport == 5353 or p.dport == 5353):
            m = mdns.parse(p.payload)
            if m:
                self.stats["mdns"] += 1
                self.emit({"ev": "mdns", "ts": p.ts, "client": p.src, "mac": p.src_mac,
                           "hosts": m["hosts"], "services": sorted(m["services"]),
                           "models": sorted(m["models"]), "names": sorted(m["names"])})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="print events as JSON lines")
    ap.add_argument("--only", help="comma-separated event types to print (tls,quic,dns,dhcp,mdns,error)")
    ap.add_argument("--seconds", type=float, help="stop after N seconds and print stats")
    ap.add_argument("ifaces", nargs="+")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None

    def emit(ev):
        if only and ev["ev"] not in only:
            return
        ev["ts"] = time.strftime("%H:%M:%S", time.localtime(ev["ts"]))
        print(json.dumps(ev, separators=(",", ":")), flush=True)

    cap = Capture(emit if args.print else (lambda ev: None))
    if args.seconds:
        import signal

        def stop(*_):
            print(json.dumps({"stats": cap.stats}), file=sys.stderr)
            sys.exit(0)
        signal.signal(signal.SIGALRM, stop)
        signal.setitimer(signal.ITIMER_REAL, args.seconds)
    cap.run(args.ifaces)


if __name__ == "__main__":
    main()
