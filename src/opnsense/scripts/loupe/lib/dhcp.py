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

"""DHCPv4 client messages: MAC, hostname, vendor class, parameter request list."""
import re
import struct

MAGIC = b"\x63\x82\x53\x63"
MSG_TYPES = {1: "DISCOVER", 3: "REQUEST", 4: "DECLINE", 7: "RELEASE", 8: "INFORM"}


# some devices send junk in the host name options; like dnsmasq, take only real host names
HOSTNAME = re.compile(rb"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,252}$")


def parse(payload):
    """Return a dict for a client->server message (op=1), else None."""
    if len(payload) < 240 or payload[0] != 1 or payload[236:240] != MAGIC:
        return None
    hlen = payload[2]
    mac = ":".join(f"{b:02x}" for b in payload[28:28 + min(hlen, 16)])
    info = {"mac": mac}
    off = 240
    while off < len(payload):
        code = payload[off]
        if code == 255:
            break
        if code == 0:
            off += 1
            continue
        if off + 1 >= len(payload):
            break
        olen = payload[off + 1]
        val = payload[off + 2:off + 2 + olen]
        off += 2 + olen
        if code == 53 and val:
            info["type"] = MSG_TYPES.get(val[0], str(val[0]))
        elif code == 12 and HOSTNAME.match(val):
            info["hostname"] = val.decode()
        elif code == 60:
            info["vendor_class"] = val.decode("utf-8", "replace")
        elif code == 55:
            info["params"] = ",".join(str(b) for b in val)
        elif code == 50 and olen == 4:
            info["requested_ip"] = ".".join(str(b) for b in val)
        elif code == 81 and olen > 3 and HOSTNAME.match(val[3:]):
            info["fqdn"] = val[3:].decode()
    return info
