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


"""Addresses configured on network interfaces, from getifaddrs(3) (what `ifconfig` prints),
without starting a process."""
import ctypes
import ipaddress
import socket


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
