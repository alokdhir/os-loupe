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


"""pf's states over generic netlink (family "pfctl", what pfctl itself uses on FreeBSD 14+), without
starting a process or parsing text. See netpfil/pf/pf_nl.h."""
import socket
import struct

AF_NETLINK, NETLINK_GENERIC = 38, 16
GENL_ID_CTRL, CTRL_CMD_GETFAMILY, CTRL_ATTR_FAMILY_ID, CTRL_ATTR_FAMILY_NAME = 16, 3, 1, 2
NLM_F_REQUEST, NLM_F_DUMP = 0x1, 0x300
NLMSG_ERROR, NLMSG_DONE = 2, 3
PFNL_CMD_GETSTATES = 1

# pfstate_type_t / pfstate_key_type_t
ST_ID, ST_CREATORID, ST_ORIG_IFNAME, ST_KEY_STACK = 1, 2, 4, 6
ST_CREATION, ST_PACKETS0, ST_PACKETS1, ST_BYTES0, ST_BYTES1, ST_PROTO, ST_DIRECTION = 13, 15, 16, 17, 18, 21, 22
STK_ADDR0, STK_ADDR1, STK_PORT0, STK_PORT1 = 1, 2, 3, 4
PF_IN = 1


def _attr(t, payload):
    n = 4 + len(payload)
    return struct.pack("HH", n, t) + payload + b"\0" * (-n % 4)


def attrs(buf, off=0, end=None):
    """Netlink attributes -> {type: payload} (nested ones are parsed by the caller)."""
    end = len(buf) if end is None else end
    out = {}
    while off + 4 <= end:
        n, t = struct.unpack_from("HH", buf, off)
        if n < 4:
            break
        out[t & 0x3FFF] = buf[off + 4:off + n]
        off += (n + 3) & ~3
    return out


class Client:
    def __init__(self):
        self.sock = socket.socket(AF_NETLINK, socket.SOCK_RAW, NETLINK_GENERIC)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
        self.seq = 0
        self.family = None

    def close(self):
        self.sock.close()

    def request(self, ftype, cmd, payload=b"", dump=False):
        """Send one generic netlink request; return the payloads (after genlmsghdr) of the replies."""
        self.seq += 1
        seq = self.seq
        body = struct.pack("BBH", cmd, 1, 0) + payload
        flags = NLM_F_REQUEST | (NLM_F_DUMP if dump else 0)
        self.sock.send(struct.pack("IHHII", 16 + len(body), ftype, flags, seq, 0) + body)
        out = []
        while True:
            data = self.sock.recv(1 << 20)
            if not data:
                raise OSError("netlink: connection closed")
            off = 0
            while off + 16 <= len(data):
                n, typ, _fl, rseq, _pid = struct.unpack_from("IHHII", data, off)
                if n < 16:
                    raise OSError("netlink: bad message")
                if rseq == seq:
                    if typ == NLMSG_DONE:
                        return out
                    if typ == NLMSG_ERROR:
                        err = struct.unpack_from("i", data, off + 16)[0]
                        if err:
                            raise OSError(-err, "netlink request failed")
                        return out
                    out.append(data[off + 20:off + n])
                off += (n + 3) & ~3
            if not dump and out:
                return out

    def pf_family(self):
        if self.family is None:
            reply = self.request(GENL_ID_CTRL, CTRL_CMD_GETFAMILY, _attr(CTRL_ATTR_FAMILY_NAME, b"pfctl\0"))
            fid = attrs(reply[0]).get(CTRL_ATTR_FAMILY_ID) if reply else None
            if not fid:
                raise OSError("netlink: no pfctl family")
            self.family = struct.unpack("H", fid[:2])[0]
        return self.family

    def states(self, ifaces=None):
        return parse_states(self.request(self.pf_family(), PFNL_CMD_GETSTATES, dump=True), ifaces)


def parse_states(msgs, ifaces=None):
    """GETSTATES replies -> dicts of what Loupe uses; with `ifaces`, only states whose origin interface
    is one of them (the WAN-side twin of each connection is skipped before it is parsed)."""
    names = [f.encode() + b"\0" for f in ifaces] if ifaces else None
    hh, q, i, h = _HH.unpack_from, _Q.unpack_from, _I.unpack_from, _H.unpack_from
    wanted, ntohs = WANTED, socket.ntohs
    out = []
    for m in msgs:
        if names and not any(n in m for n in names):
            continue
        a, off, end, need = {}, 0, len(m), len(wanted)
        while off + 4 <= end and need:          # stop once every field Loupe uses has been seen
            n, t = hh(m, off)
            if n < 4:
                break
            t &= 0x3FFF
            if t in wanted:
                a[t] = m[off + 4:off + n]
                need -= 1
            off += (n + 3) & ~3
        if need:
            continue
        origif = a[ST_ORIG_IFNAME].split(b"\0", 1)[0].decode()
        if ifaces and origif not in ifaces:
            continue
        k = attrs(a[ST_KEY_STACK])
        if STK_ADDR0 not in k or STK_ADDR1 not in k:
            continue
        out.append({
            "id": q(a[ST_ID])[0], "creatorid": i(a[ST_CREATORID])[0],
            "origif": origif, "proto": a[ST_PROTO][0],
            "addr0": _ip(k[STK_ADDR0]), "port0": ntohs(h(k[STK_PORT0])[0]),
            "addr1": _ip(k[STK_ADDR1]), "port1": ntohs(h(k[STK_PORT1])[0]),
            "inbound": a[ST_DIRECTION][0] == PF_IN,
            "packets": (q(a[ST_PACKETS0])[0], q(a[ST_PACKETS1])[0]),
            "bytes": (q(a[ST_BYTES0])[0], q(a[ST_BYTES1])[0]),
            "age": i(a[ST_CREATION])[0],
        })
    return out


WANTED = {ST_ID, ST_CREATORID, ST_ORIG_IFNAME, ST_KEY_STACK, ST_CREATION, ST_PACKETS0, ST_PACKETS1, ST_BYTES0,
          ST_BYTES1, ST_PROTO, ST_DIRECTION}


_HH, _Q, _I, _H = struct.Struct("HH"), struct.Struct("Q"), struct.Struct("I"), struct.Struct("H")


def _ip(b):
    return socket.inet_ntop(socket.AF_INET if len(b) == 4 else socket.AF_INET6, b)
