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


"""Talk to Unbound's remote-control port directly (what `unbound-control` does), without starting
a process: TLS with the control certificate, one command line, read the answer until EOF."""
import re
import socket
import ssl

CONF = "/var/unbound/unbound.conf"


def control_settings(path=CONF):
    """host, port, server cert, client cert, client key from the remote-control section."""
    want = {"control-interface": "127.0.0.1", "control-port": "953", "server-cert-file": "",
            "control-cert-file": "", "control-key-file": ""}
    section = None
    with open(path) as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            m = re.match(r"^([a-z-]+):\s*(.*)$", line)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip().strip('"')
            if not val:
                section = key
            elif section == "remote-control" and key in want:
                want[key] = val
    return (want["control-interface"], int(want["control-port"]), want["server-cert-file"],
            want["control-cert-file"], want["control-key-file"])


def command(cmd, path=CONF, timeout=10):
    host, port, server_cert, cert, key = control_settings(path)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False                 # the server certificate is pinned instead
    ctx.load_verify_locations(server_cert)
    ctx.load_cert_chain(cert, key)
    with socket.create_connection((host, port), timeout=timeout) as raw, ctx.wrap_socket(raw) as s:
        s.sendall(f"UBCT1 {cmd}\n".encode())
        chunks = []
        while True:
            try:
                b = s.recv(65536)
            except ssl.SSLZeroReturnError:
                break
            if not b:
                break
            chunks.append(b)
    return b"".join(chunks).decode("utf-8", "replace")
