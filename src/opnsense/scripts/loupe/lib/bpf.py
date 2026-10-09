"""Minimal FreeBSD BPF reader: open /dev/bpf, attach to an interface, install a filter, read frames.

The filter is compiled by tcpdump (`tcpdump -ddd`), which is part of OPNsense, so we
never hand-assemble BPF programs.
"""
import fcntl
import os
import struct
import subprocess

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


def compile_filter(expr, snaplen=SNAPLEN):
    """Compile a tcpdump expression to a list of (code, jt, jf, k) instructions."""
    out = subprocess.run(
        ["tcpdump", "-s", str(snaplen), "-ddd", expr],
        capture_output=True, text=True, check=True,
    ).stdout.split("\n")
    count = int(out[0])
    insns = [tuple(int(x) for x in line.split()) for line in out[1:1 + count]]
    if len(insns) != count:
        raise ValueError("unexpected tcpdump -ddd output")
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
