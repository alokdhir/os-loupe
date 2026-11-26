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


"""Ask configd for another plugin's data over its socket, as configctl does, without starting a process. The socket is
open to every local user, so this works after Loupe has dropped root."""
import json
import socket

SOCKET = "/var/run/configd.socket"
END = b"\x00\x00\x00"           # configd ends every answer with this, after the output and two newlines


def run(command, path=None, timeout=5):
    """configd's answer to `command` (e.g. "eero query units") as text, or None when configd can't be reached."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path or SOCKET)
            sock.sendall(command.encode())
            data = b""
            while not data.endswith(END):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
    except OSError:
        return None
    return data.removesuffix(END).decode(errors="replace")


def query(command, kind, path=None, timeout=5):
    """The JSON configd's action prints, when it is of type `kind` (list, dict), else None: a plugin that isn't installed
    is answered with text ("Action not allowed or missing"), an unknown query with {"error": ...}."""
    text = run(command, path, timeout)
    try:
        out = json.loads(text) if text else None
    except ValueError:
        return None
    if not isinstance(out, kind) or (isinstance(out, dict) and "error" in out):
        return None
    return out


def eero():
    """os-eero's units and Wi-Fi clients, {"units": [...], "clients": [...]}, or {} without that plugin."""
    units = query("eero query units", list)
    clients = query("eero query clients all", list) if units is not None else None
    return {"units": units, "clients": clients} if clients is not None else {}


def eero_names(clients):
    """{MAC: name} from os-eero's clients: the names eero has from the device or its user, not the maker's name or
    the MAC it shows when it has neither."""
    out = {}
    for c in clients or []:
        if not isinstance(c, dict) or c.get("vendor_name") is not False:
            continue
        mac, name = c.get("mac"), c.get("name")
        if isinstance(mac, str) and isinstance(name, str) and name.strip():
            out[mac.lower()] = name.strip()
    return out
