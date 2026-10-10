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


"""IP -> MAC from the kernel's ARP and IPv6 neighbour tables, read with sysctl(3) (what
`arp -an` / `ndp -an` print), without starting a process.

The route(4) sysctl NET_RT_FLAGS/RTF_LLINFO returns one rt_msghdr per entry followed by its
sockaddrs: RTA_DST (the IP) and RTA_GATEWAY (a sockaddr_dl holding the MAC).
"""
import ctypes
import socket
import struct

CTL_NET, PF_ROUTE, NET_RT_FLAGS, RTF_LLINFO = 4, 17, 2, 0x400
AF_LINK = 18
RTA_DST, RTA_GATEWAY = 0x1, 0x2


class _RtMetrics(ctypes.Structure):          # struct rt_metrics (net/route.h)
    _fields_ = [(n, ctypes.c_ulong) for n in ("locks", "mtu", "hopcount", "expire", "recvpipe", "sendpipe",
                                               "ssthresh", "rtt", "rttvar", "pksent", "weight", "nhidx")] + \
               [("filler", ctypes.c_ulong * 2)]


class _RtMsghdr(ctypes.Structure):           # struct rt_msghdr (net/route.h)
    _fields_ = [("msglen", ctypes.c_ushort), ("version", ctypes.c_ubyte), ("type", ctypes.c_ubyte),
                ("index", ctypes.c_ushort), ("spare1", ctypes.c_ushort), ("flags", ctypes.c_int),
                ("addrs", ctypes.c_int), ("pid", ctypes.c_int), ("seq", ctypes.c_int), ("errno", ctypes.c_int),
                ("fmask", ctypes.c_int), ("inits", ctypes.c_ulong), ("rmx", _RtMetrics)]


HDR = ctypes.sizeof(_RtMsghdr)
ADDRS = _RtMsghdr.addrs.offset
ALIGN = ctypes.sizeof(ctypes.c_long)
_libc = None


def _sysctl(mib):
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(None, use_errno=True)
    arr = (ctypes.c_int * len(mib))(*mib)
    for _ in range(3):                   # the table can grow between the two calls
        size = ctypes.c_size_t(0)
        if _libc.sysctl(arr, len(mib), None, ctypes.byref(size), None, 0) != 0:
            raise OSError(ctypes.get_errno(), "sysctl")
        buf = ctypes.create_string_buffer(size.value + 8192)
        size = ctypes.c_size_t(len(buf))
        if _libc.sysctl(arr, len(mib), buf, ctypes.byref(size), None, 0) == 0:
            return buf.raw[:size.value]
    raise OSError(ctypes.get_errno(), "sysctl")


def parse(data):
    """rt_msghdr records -> {ip: mac}."""
    out, off = {}, 0
    while off + HDR <= len(data):
        msglen = struct.unpack_from("H", data, off)[0]
        if msglen == 0:
            break
        addrs = struct.unpack_from("i", data, off + ADDRS)[0]
        p, ip, mac = off + HDR, None, None
        for bit in range(8):
            if not addrs & (1 << bit):
                continue
            salen, family = data[p], data[p + 1]
            if bit == 0 and family == socket.AF_INET:
                ip = socket.inet_ntop(socket.AF_INET, data[p + 4:p + 8])
            elif bit == 0 and family == socket.AF_INET6:
                raw = bytearray(data[p + 8:p + 24])
                if raw[0] == 0xfe and raw[1] & 0xc0 == 0x80:
                    raw[2] = raw[3] = 0          # link-local: the kernel embeds the scope id here
                ip = socket.inet_ntop(socket.AF_INET6, bytes(raw))
            elif bit == 1 and family == AF_LINK:
                nlen, alen = data[p + 5], data[p + 6]
                if alen == 6:
                    mac = ":".join(f"{b:02x}" for b in data[p + 8 + nlen:p + 14 + nlen])
            p += (salen + ALIGN - 1) & ~(ALIGN - 1) if salen else ALIGN
        if ip and mac:
            out[ip] = mac
        off += msglen
    return out


def table():
    """{ip: mac} for IPv4 (ARP) and IPv6 (neighbour discovery)."""
    out = {}
    for family in (socket.AF_INET, socket.AF_INET6):
        out.update(parse(_sysctl([CTL_NET, PF_ROUTE, 0, family, NET_RT_FLAGS, RTF_LLINFO])))
    return out
