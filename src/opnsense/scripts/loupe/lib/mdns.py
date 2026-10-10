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

"""mDNS (Bonjour) announcements: hostnames, advertised services, and model hints from TXT records."""
from . import dns

# TXT keys that carry a device model: AirPlay/RAOP "model"/"am", Chromecast "md", device-info "model"
MODEL_KEYS = ("model", "am", "md", "usb_mdl", "ty")


def parse(payload):
    """Return {'hosts': {name.local: [ips]}, 'services': set(), 'models': set(), 'names': set()} or None."""
    try:
        is_resp, _qs, recs = dns.parse(payload)
    except (ValueError, IndexError):
        return None
    if not is_resp or not recs:
        return None
    out = {"hosts": {}, "services": set(), "models": set(), "names": set()}
    for _section, name, rtype, _ttl, value in recs:
        if rtype in (dns.T_A, dns.T_AAAA):
            out["hosts"].setdefault(name, []).append(value)
        elif rtype == dns.T_PTR and name.endswith(".local") and name.startswith("_"):
            if name == "_services._dns-sd._udp.local":
                continue
            out["services"].add(name.removesuffix(".local"))
            if value:
                out["names"].add(value.split("._")[0])
        elif rtype == dns.T_TXT and isinstance(value, dict):
            for k in MODEL_KEYS:
                if value.get(k):
                    out["models"].add(f"{k}={value[k]}")
            if value.get("fn"):  # Chromecast friendly name
                out["names"].add(value["fn"])
    if not (out["hosts"] or out["services"] or out["models"]):
        return None
    return out
