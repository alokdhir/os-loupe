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

"""Guess what a device is from the clues loupe collects.

The rules are data (data/devices.json); this module only knows how to match them
and in which order to trust the kinds of evidence. Each guess carries its source
so the GUI can explain it.
"""
import csv
import json
import os
import re

OUI_CSV = "/usr/local/opnsense/contrib/ieee/oui.csv"
OUI_LEN = {"MA-L": 6, "MA-M": 7, "MA-S": 9}
RULES_JSON = os.path.join(os.path.dirname(__file__), "..", "data", "devices.json")

# strongest evidence first; a rule's tier is its "tier", else the field of its first condition
TIERS = ("mac_prefix", "mdns_model", "hostname", "role", "strong_service", "dhcp_vendor", "talks_to", "dhcp_params",
         "mdns_service", "vendor", "private_mac")


class Rules:
    def __init__(self, data, extra=()):
        self.types = data.get("types", {})
        self.apple_models = data.get("apple_models", {})
        self.tiers = {t: [] for t in TIERS}
        for r in list(extra) + data.get("rules", []):
            conds = r["all"] if "all" in r else [r["when"]]
            tier = r.get("tier") or next(iter(conds[0]))
            self.tiers[tier].append((conds, r))

    def icon(self, dtype):
        return self.types.get(dtype, {}).get("icon", "")

    def vendor(self, dtype):
        return self.types.get(dtype, {}).get("vendor", "")


_rules = None


def rules():
    global _rules
    if _rules is None:
        with open(RULES_JSON, encoding="utf-8") as f:
            _rules = Rules(json.load(f))
    return _rules


def _params(info):
    p = info.get("dhcp_params") or ""
    return ",".join(str(x) for x in p) if isinstance(p, list) else str(p)


def _check(field, want, ev):
    """Evidence text if this one condition holds (may be ""), else None."""
    if field == "mac_prefix":
        return "mac" if ev["mac"].startswith(want.lower()) else None
    if field == "mdns_model":
        for m in ev["models"]:
            val = m.split("=", 1)[1] if "=" in m else m
            if re.search(want, val) or re.search(want, m):
                return f"mdns model {val}"
        return None
    if field == "hostname":
        for h in ev["hosts"]:
            if h and re.search(want, h.removesuffix(".local")):
                return f"hostname {h}"
        return None
    if field == "mdns_service":
        return f"mdns {want}" if want in ev["services"] else None
    if field == "dhcp_vendor":
        return (f"dhcp vendor {ev['vc']}" if ev["vc"] else "") if re.search(want, ev["vc"]) else None
    if field == "dhcp_params":
        p = ev["params"]
        return "dhcp fingerprint" if p == want or p.startswith(want + ",") else None
    if field == "talks_to":
        hit = any(n == want or n.endswith("." + want) for n in ev["names"])
        return f"talks to {want}" if hit else None
    if field == "vendor":
        return f"vendor {ev['vendor']}" if ev["vendor"] and re.search(r"(?i)\b" + re.escape(want) + r"\b", ev["vendor"]) else None
    if field == "private_mac":
        return ("private MAC" if want else "") if ev["private"] == want else None
    raise ValueError(f"unknown rule field {field}")


def _match(conds, ev):
    found = []
    for cond in conds:
        for field, want in cond.items():
            hit = None
            for w in (want if isinstance(want, list) else [want]):
                hit = _check(field, w, ev)
                if hit is not None:
                    break
            if hit is None:
                return None
            if hit:
                found.append(hit)
    return " + ".join(found)


def classify(mac, hostname=None, info=None, names=(), oui=None, table=None):
    """Return (type, source, vendor). `names` are server names the device looked up or connected to."""
    info = info or {}
    mac = mac.lower()
    private = is_private(mac)
    ven = vendor(mac, oui or {}) if not private else None
    ev = {"mac": mac, "private": private, "vendor": ven or "", "names": list(names),
          "models": info.get("mdns_models", []), "services": info.get("mdns_services", []),
          "vc": info.get("dhcp_vendor_class") or "", "params": _params(info),
          "hosts": [hostname or "", info.get("dhcp_hostname") or ""] + list(info.get("mdns_hosts", []))}
    for tier in (table or rules()).tiers.values():
        for conds, r in tier:
            src = _match(conds, ev)
            if src is not None:
                return r["type"], src, r.get("vendor", ven)
    return None, None, ven


def load_oui(path=OUI_CSV):
    table = {}
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.reader(f):
                if len(row) >= 3 and row[0] in OUI_LEN:
                    table[row[1].upper()] = row[2].strip()
    except OSError:
        pass
    return table


def vendor(mac, oui):
    hexmac = mac.replace(":", "").upper()
    for n in (9, 7, 6):
        v = oui.get(hexmac[:n])
        if v:
            return v
    return None


def is_private(mac):
    """Locally administered (randomized) MAC: phones, tablets and laptops use these per network."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except ValueError:
        return False


def model(info):
    """Marketing name of an Apple device from its announced model identifier, if known."""
    names = rules().apple_models
    for m in (info or {}).get("mdns_models", []):
        hit = names.get(m.split("=", 1)[1] if "=" in m else m)
        if hit:
            return hit
    return None


def mdns_name(info):
    """The name a device announces over Bonjour ("midnight" from midnight.local)."""
    for h in (info or {}).get("mdns_hosts", []):
        if h.endswith(".local") and not h.startswith("_"):
            return h[:-len(".local")]
    names = (info or {}).get("mdns_names", [])
    return names[0] if names else None


def leases(path="/var/db/dnsmasq.leases"):
    """mac -> hostname from dnsmasq's lease file."""
    out = {}
    try:
        with open(path) as f:
            for line in f:
                p = line.split()
                if len(p) >= 4 and p[3] != "*":
                    out[p[1].lower()] = p[3]
    except OSError:
        pass
    return out


def static_hosts(path="/var/etc/dnsmasq-hosts"):
    """ip -> short name from dnsmasq's static host entries."""
    out = {}
    try:
        with open(path) as f:
            for line in f:
                p = line.split()
                if len(p) >= 3 and not line.startswith("#"):
                    out.setdefault(p[0], p[2])
    except OSError:
        pass
    return out
