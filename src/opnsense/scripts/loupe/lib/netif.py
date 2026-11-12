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


"""Addresses configured on network interfaces, from getifaddrs(3) (what `ifconfig` prints), and what the router's
bridges have learned (what `ifconfig bridge0 addr` prints), without starting a process."""
import ctypes
import fcntl
import ipaddress
import socket
import struct

from . import neighbours


class _Ifaddrs(ctypes.Structure):
    pass


_Ifaddrs._fields_ = [("next", ctypes.POINTER(_Ifaddrs)), ("name", ctypes.c_char_p), ("flags", ctypes.c_uint),
                     ("addr", ctypes.c_void_p), ("netmask", ctypes.c_void_p), ("dstaddr", ctypes.c_void_p),
                     ("data", ctypes.c_void_p)]

_libc = None


def _sockaddr(ptr, family, n):
    """The address bytes of a BSD sockaddr (sa_len first). Netmasks may be shortened: pad with zeros."""
    if not ptr:
        return None
    salen = ctypes.string_at(ptr, 1)[0]
    off = 4 if family == socket.AF_INET else 8
    raw = ctypes.string_at(ptr, max(salen, 2))[off:off + n] if salen > off else b""
    return raw + b"\0" * (n - len(raw))


def addresses(ifaces):
    """[(interface, ip_interface)] for IPv4 and IPv6 addresses on `ifaces`."""
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(None, use_errno=True)
        _libc.getifaddrs.argtypes = [ctypes.POINTER(ctypes.POINTER(_Ifaddrs))]
        _libc.freeifaddrs.argtypes = [ctypes.POINTER(_Ifaddrs)]
    head = ctypes.POINTER(_Ifaddrs)()
    if _libc.getifaddrs(ctypes.byref(head)) != 0:
        raise OSError(ctypes.get_errno(), "getifaddrs")
    out = []
    try:
        p = head
        while p:
            ifa = p.contents
            name = ifa.name.decode()
            if name in ifaces and ifa.addr:
                family = ctypes.string_at(ifa.addr, 2)[1]
                n = {socket.AF_INET: 4, socket.AF_INET6: 16}.get(family)
                if n:
                    addr = _sockaddr(ifa.addr, family, n)
                    mask = _sockaddr(ifa.netmask, family, n)
                    bits = sum(bin(b).count("1") for b in mask) if mask else n * 8
                    if family == socket.AF_INET6 and addr[0] == 0xfe and addr[1] & 0xc0 == 0x80:
                        addr = addr[:2] + b"\0\0" + addr[4:]            # embedded scope id
                    out.append((name, ipaddress.ip_interface((ipaddress.ip_address(addr), bits))))
            p = ifa.next
    finally:
        _libc.freeifaddrs(head)
    return out


AF_LINK = 18        # FreeBSD and macOS


def link_macs():
    """The MAC addresses of all of this machine's interfaces (getifaddrs AF_LINK entries, sockaddr_dl: the
    interface name, then the link-layer address), lower case with colons."""
    global _libc
    if _libc is None:
        addresses(())                   # loads libc and its prototypes
    head = ctypes.POINTER(_Ifaddrs)()
    if _libc.getifaddrs(ctypes.byref(head)) != 0:
        raise OSError(ctypes.get_errno(), "getifaddrs")
    out = set()
    try:
        p = head
        while p:
            ifa = p.contents
            if ifa.addr and ctypes.string_at(ifa.addr, 2)[1] == AF_LINK:
                sdl = ctypes.string_at(ifa.addr, ctypes.string_at(ifa.addr, 1)[0])
                nlen, alen = sdl[5], sdl[6]
                mac = sdl[8 + nlen:8 + nlen + alen]
                if alen == 6 and any(mac):
                    out.add(":".join(f"{b:02x}" for b in mac))
            p = ifa.next
    finally:
        _libc.freeifaddrs(head)
    return out


IFT_ETHER, IFT_BRIDGE = 0x06, 0xd1
LINK_STATE_UP = 2
IFF_UP = 0x1
SIOCGDRVSPEC = 0xc028697b       # _IOWR('i', 123, struct ifdrv): reading a bridge needs no privilege
BRDGGIFS, BRDGRTS = 6, 7
IFBREQ_SIZE = 80                # struct ifbreq, net/if_bridgevar.h
IFBAREQ_SIZE = 40               # struct ifbareq


def link_entry(flags, data):
    """One interface from its flags and the start of its struct if_data. Up means turned on and linked: a port that is
    turned off (unassigned) keeps the link state it had when it was turned off, cable or not."""
    return {"ether": data[0] == IFT_ETHER, "bridge": data[0] == IFT_BRIDGE,
            "up": bool(flags & IFF_UP) and data[4] == LINK_STATE_UP,
            "mbit": struct.unpack_from("=Q", data, 16)[0] // 1000000}


def link_info():
    """{interface: {ether, bridge, up, mbit}} from getifaddrs: AF_LINK entries carry a sockaddr_dl and a struct if_data."""
    global _libc
    if _libc is None:
        addresses(())
    head = ctypes.POINTER(_Ifaddrs)()
    if _libc.getifaddrs(ctypes.byref(head)) != 0:
        raise OSError(ctypes.get_errno(), "getifaddrs")
    out = {}
    try:
        p = head
        while p:
            ifa = p.contents
            if ifa.addr and ifa.data and ctypes.string_at(ifa.addr, 2)[1] == AF_LINK:
                out[ifa.name.decode()] = link_entry(ifa.flags, ctypes.string_at(ifa.data, 24))
            p = ifa.next
    finally:
        _libc.freeifaddrs(head)
    return out


class _Ifdrv(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char * 16), ("cmd", ctypes.c_ulong), ("len", ctypes.c_size_t), ("data", ctypes.c_void_p)]


class _ListConf(ctypes.Structure):      # struct ifbifconf, struct ifbaconf
    _fields_ = [("len", ctypes.c_uint32), ("buf", ctypes.c_void_p)]


def _bridge_list(sock, bridge, cmd, size):
    """One of a bridge's lists, as bytes: ask with room to spare, and again with more if the answer filled it."""
    room = 256
    while True:
        buf = ctypes.create_string_buffer(room * size)
        conf = _ListConf(len=len(buf), buf=ctypes.addressof(buf))
        drv = _Ifdrv(name=bridge.encode(), cmd=cmd, len=ctypes.sizeof(conf), data=ctypes.addressof(conf))
        fcntl.ioctl(sock, SIOCGDRVSPEC, drv)
        if conf.len < len(buf):
            return buf.raw[:conf.len]
        room *= 4


def _name(raw):
    return raw.split(b"\0", 1)[0].decode("ascii", "replace")


def parse_bridge_members(raw):
    """Interface names from a list of struct ifbreq."""
    return [_name(raw[i:i + 16]) for i in range(0, len(raw) - IFBREQ_SIZE + 1, IFBREQ_SIZE)]


def parse_bridge_addresses(raw):
    """[(member interface, MAC)] from a list of struct ifbareq; only what was learned, not addresses set by hand."""
    out = []
    for i in range(0, len(raw) - IFBAREQ_SIZE + 1, IFBAREQ_SIZE):
        flags, mac = raw[i + 24], raw[i + 25:i + 31]
        if flags & 3 == 0:
            out.append((_name(raw[i:i + 16]), ":".join(f"{b:02x}" for b in mac)))
    return out


def router_ports():
    """The router's own Ethernet ports: {"bridges": [names], "ports": {port: {name, up, speed in Mbit, wan}},
    "fdb": {port: [MACs]}}. What is on a port comes from its bridge's table, or outside a bridge from the neighbour
    table. The port of the default route is the WAN: what is beyond it isn't on the LAN."""
    info = link_info()
    out = {"bridges": sorted(n for n, i in info.items() if i["bridge"]), "ports": {}, "fdb": {}}
    ports = {n: {"name": n, "up": i["up"], "speed": i["mbit"]} for n, i in info.items() if i["ether"]}
    members = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for bridge in out["bridges"]:
            for member in parse_bridge_members(_bridge_list(sock, bridge, BRDGGIFS, IFBREQ_SIZE)):
                members.add(member)
                link = info.get(member, {})
                ports[member] = {"name": member, "up": bool(link.get("up")), "speed": link.get("mbit") or 0}
            for member, mac in parse_bridge_addresses(_bridge_list(sock, bridge, BRDGRTS, IFBAREQ_SIZE)):
                if ports.get(member, {}).get("up"):
                    macs = out["fdb"].setdefault(member, [])
                    if mac not in macs:
                        macs.append(mac)
    index = neighbours.default_interface()
    wan = socket.if_indextoname(index) if index else None
    if wan in ports:
        ports[wan]["wan"] = True
    for index, macs in neighbours.by_interface().items():
        name = socket.if_indextoname(index)
        if name in ports and name not in members and name != wan and ports[name]["up"]:
            out["fdb"][name] = macs
    out["ports"] = dict(sorted(ports.items()))       # the kernel lists a bridge's newest member first
    return out
