#!/usr/local/bin/python3
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
TLS4 = "(ip and tcp and tcp[((tcp[12]&0xf0)>>2)] = 0x16 and tcp[((tcp[12]&0xf0)>>2)+5] = 0x01)"
TLS6 = "(ip6 and ip6[6] = 6 and ip6[40+((ip6[52]&0xf0)>>2)] = 0x16 and ip6[40+((ip6[52]&0xf0)>>2)+5] = 0x01)"
QUIC4 = "(ip and udp dst port 443 and udp[8] & 0x80 != 0)"
QUIC6 = "(ip6 and udp dst port 443 and ip6[6] = 17 and ip6[48] & 0x80 != 0)"
OTHER = "(udp src port 53 or udp dst port 67 or udp port 5353)"
FILTER = f"{TLS4} or {TLS6} or {QUIC4} or {QUIC6} or {OTHER}"


class Capture:
    def __init__(self, ifaces, emit):
        self.taps = [bpf.Bpf(i, FILTER) for i in ifaces]
        self.emit = emit
        self.quic = quic.Reassembler()
        self.stats = {"frames": 0, "tls": 0, "tls_partial": 0, "quic": 0, "dns": 0, "dhcp": 0, "mdns": 0}

    def run(self):
        fds = {t.fileno(): t for t in self.taps}
        while True:
            ready, _, _ = select.select(list(fds), [], [], 5.0)
            for fd in ready:
                for ts, frame in fds[fd].read():
                    self.stats["frames"] += 1
                    p = packet.decode(ts, frame)
                    if p is not None:
                        try:
                            self.handle(p)
                        except (ValueError, IndexError, UnicodeError) as e:
                            self.emit({"ev": "error", "ts": ts, "err": repr(e), "src": p.src, "sport": p.sport})

    def handle(self, p):
        if p.proto == packet.PROTO_TCP and p.payload[:1] == b"\x16":
            h = tls.parse_tcp_payload(p.payload)
            if h is None:
                return
            self.stats["tls"] += 1
            if not h.sni:
                self.stats["tls_partial"] += 1
            self.emit({"ev": "tls", "ts": p.ts, "client": p.src, "mac": p.src_mac, "server": p.dst,
                       "port": p.dport, "sni": h.sni, "alpn": list(h.alpn), "ech": h.ech, "complete": h.complete})
        elif p.proto == packet.PROTO_UDP and p.dport == 443:
            h = self.quic.feed(p.src, p.payload, p.ts)
            if h is not None:
                self.stats["quic"] += 1
                self.emit({"ev": "quic", "ts": p.ts, "client": p.src, "mac": p.src_mac, "server": p.dst,
                           "port": p.dport, "sni": h.sni, "alpn": list(h.alpn), "ech": h.ech})
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

    cap = Capture(args.ifaces, emit if args.print else (lambda ev: None))
    if args.seconds:
        import signal

        def stop(*_):
            print(json.dumps({"stats": cap.stats}), file=sys.stderr)
            sys.exit(0)
        signal.signal(signal.SIGALRM, stop)
        signal.setitimer(signal.ITIMER_REAL, args.seconds)
    cap.run()


if __name__ == "__main__":
    main()
