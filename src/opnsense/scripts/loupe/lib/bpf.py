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

"""Minimal FreeBSD BPF reader: open /dev/bpf, attach to an interface, install a filter, read frames.

The filter is compiled by libpcap (part of the base system, the same compiler tcpdump uses),
so we never hand-assemble BPF programs.
"""
import ctypes
import ctypes.util
import fcntl
import os
import struct

# ioctls (sys/net/bpf.h, amd64)
BIOCGBLEN = 0x40044266      # _IOR('B', 102, u_int)
BIOCSBLEN = 0xC0044266      # _IOWR('B', 102, u_int)
BIOCSETF = 0x80104267       # _IOW('B', 103, struct bpf_program)
BIOCSETIF = 0x8020426C      # _IOW('B', 108, struct ifreq)
BIOCSRTIMEOUT = 0x8010426D  # _IOW('B', 109, struct timeval)
BIOCIMMEDIATE = 0x80044270  # _IOW('B', 112, u_int)
BIOCSSEESENT = 0x80044277   # _IOW('B', 119, u_int)

BUFSIZE = 4 * 1024 * 1024
SNAPLEN = 2048


class _Insn(ctypes.Structure):          # struct bpf_insn
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]


class _Program(ctypes.Structure):       # struct bpf_program
    _fields_ = [("len", ctypes.c_uint), ("insns", ctypes.POINTER(_Insn))]


DLT_EN10MB = 1
PCAP_NETMASK_UNKNOWN = 0xFFFFFFFF
_pcap = None


def _libpcap():
    global _pcap
    if _pcap is None:
        lib = ctypes.CDLL(ctypes.util.find_library("pcap") or "libpcap.so")
        lib.pcap_open_dead.restype = ctypes.c_void_p
        lib.pcap_open_dead.argtypes = [ctypes.c_int, ctypes.c_int]
        lib.pcap_compile.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Program), ctypes.c_char_p, ctypes.c_int,
                                     ctypes.c_uint32]
        lib.pcap_geterr.restype = ctypes.c_char_p
        lib.pcap_geterr.argtypes = [ctypes.c_void_p]
        lib.pcap_freecode.argtypes = [ctypes.POINTER(_Program)]
        lib.pcap_close.argtypes = [ctypes.c_void_p]
        _pcap = lib
    return _pcap


def compile_filter(expr, snaplen=SNAPLEN):
    """Compile a tcpdump expression for Ethernet to a list of (code, jt, jf, k) instructions."""
    lib = _libpcap()
    handle = lib.pcap_open_dead(DLT_EN10MB, snaplen)
    if not handle:
        raise OSError("pcap_open_dead failed")
    prog = _Program()
    try:
        if lib.pcap_compile(handle, ctypes.byref(prog), expr.encode(), 1, PCAP_NETMASK_UNKNOWN) != 0:
            raise ValueError(f"bad filter: {lib.pcap_geterr(handle).decode(errors='replace')}")
        insns = [(i.code, i.jt, i.jf, i.k) for i in prog.insns[:prog.len]]
        lib.pcap_freecode(ctypes.byref(prog))
    finally:
        lib.pcap_close(handle)
    return insns


class Bpf:
    def __init__(self, iface, expr, bufsize=BUFSIZE, timeout=1.0):
        self.iface = iface
        self.fd = self._open()
        fcntl.ioctl(self.fd, BIOCSBLEN, struct.pack("I", bufsize))  # must precede BIOCSETIF
        fcntl.ioctl(self.fd, BIOCSETIF, struct.pack("16s16x", iface.encode()))
        fcntl.ioctl(self.fd, BIOCSSEESENT, struct.pack("I", 1))     # include the router's own replies (DNS)
        sec = int(timeout)
        fcntl.ioctl(self.fd, BIOCSRTIMEOUT, struct.pack("qq", sec, int((timeout - sec) * 1e6)))
        self.bufsize = struct.unpack("I", fcntl.ioctl(self.fd, BIOCGBLEN, struct.pack("I", 0)))[0]
        self._insns = compile_filter(expr)
        raw = b"".join(struct.pack("HBBI", *i) for i in self._insns)
        self._prog_buf = bytearray(raw)  # keep alive: the kernel copies it, but be safe
        import ctypes
        self._cbuf = (ctypes.c_char * len(raw)).from_buffer(self._prog_buf)
        prog = struct.pack("IxxxxP", len(self._insns), ctypes.addressof(self._cbuf))
        fcntl.ioctl(self.fd, BIOCSETF, prog)

    @staticmethod
    def _open():
        try:
            return os.open("/dev/bpf", os.O_RDONLY)  # cloning device
        except OSError:
            for n in range(256):
                try:
                    return os.open(f"/dev/bpf{n}", os.O_RDONLY)
                except OSError:
                    continue
        raise OSError("no free /dev/bpf")

    def fileno(self):
        return self.fd

    def read(self):
        """Yield (timestamp, frame bytes) for each captured frame in one buffer read."""
        try:
            buf = os.read(self.fd, self.bufsize)
        except InterruptedError:
            return
        off = 0
        n = len(buf)
        while off + 26 <= n:
            sec, usec, caplen, _datalen, hdrlen = struct.unpack_from("qqIIH", buf, off)
            start = off + hdrlen
            yield sec + usec / 1e6, buf[start:start + caplen]
            off = (start + caplen + 7) & ~7  # BPF_WORDALIGN, sizeof(long) == 8

    def close(self):
        os.close(self.fd)
