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
# (the DHCP fingerprint beats talks_to: a phone running a smart-home app talks to the same cloud as the device;
# "fallback" holds fingerprints too generic to beat specific evidence, like BusyBox's udhcp - but still better
# than guessing from a private MAC)
TIERS = ("mac_prefix", "mdns_model", "hostname", "role", "strong_service", "dhcp_vendor", "dhcp_params", "talks_to",
         "mdns_service", "vendor", "fallback", "private_mac")


REGEX_FIELDS = ("hostname", "mdns_model", "dhcp_vendor")


def _tiers(rules):
    tiers = {t: [] for t in TIERS}
    for r in rules:
        conds = r["all"] if "all" in r else [r["when"]]
        tier = r.get("tier") or next(iter(conds[0]))
        tiers[tier].append((conds, r))
    return tiers


def check_rule(r):
    """None if a rule is usable, else what is wrong with it (user rules come from the GUI)."""
    try:
        conds = r["all"] if "all" in r else [r["when"]]
        if not r.get("type"):
            return "no type"
        for cond in conds:
            for field, want in cond.items():
                if field not in TIERS or field in ("role", "strong_service", "fallback"):
                    return f"unknown field {field}"
                for w in (want if isinstance(want, list) else [want]):
                    if field in REGEX_FIELDS:
                        re.compile(w)
    except (KeyError, TypeError, StopIteration, AttributeError):
        return "malformed"
    except re.error as e:
        return f"bad regular expression: {e}"
    return None


def loose(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


class Rules:
    """Shipped rules (data/devices.json) plus the user's own, which are all checked first."""

    def __init__(self, data, user=()):
        self.types = data.get("types", {})
        self.apple_models = data.get("apple_models", {})
        self.user_icons = {r["type"]: r["icon"] for r in user if r.get("icon") and r.get("type")}
        self.loose_icons = {loose(t): v["icon"] for t, v in self.types.items() if v.get("icon")}
        self.user = _tiers(user)
        self.tiers = _tiers(data.get("rules", []))

    def icon(self, dtype):
        """The icon a rule of the user's gave this type, else the built-in type's: "A/V Receiver" is "AV receiver"."""
        dtype = dtype or ""
        builtin = self.types.get(dtype, {}).get("icon") or self.loose_icons.get(loose(dtype))
        return self.user_icons.get(dtype) or builtin or ""

    def vendor(self, dtype):
        return self.types.get(dtype, {}).get("vendor", "")


_rules = None
_user_rules = []


def rules():
    global _rules
    if _rules is None:
        with open(RULES_JSON, encoding="utf-8") as f:
            _rules = Rules(json.load(f), _user_rules)
    return _rules


LISTABLE = ("mac_prefix", "vendor", "mdns_service", "talks_to")     # a comma can't be part of these


def _split_lists(r):
    """GUI rules hold one pattern; for fields where a comma can't occur, "a, b" means a or b."""
    if "when" not in r:
        return r
    when = {f: ([x.strip() for x in w.split(",") if x.strip()] if f in LISTABLE and isinstance(w, str) and "," in w else w)
            for f, w in r["when"].items()}
    if "mac_prefix" in when:
        prefixes = when["mac_prefix"] if isinstance(when["mac_prefix"], list) else [when["mac_prefix"]]
        when["mac_prefix"] = [mac_prefix(p) for p in prefixes]
    return {**r, "when": when}


def mac_prefix(text):
    """52-54-00, 525400, 52:54:00 -> 52:54:00 (how MACs are compared)."""
    hexdigits = re.sub(r"[^0-9a-fA-F]", "", text).lower()
    return ":".join(hexdigits[i:i + 2] for i in range(0, len(hexdigits), 2))


def set_user_rules(user):
    """Use the user's rules (from loupe.json) from now on. Returns [(rule, problem)] for ones skipped."""
    global _rules, _user_rules
    bad = [(r, check_rule(r)) for r in (_split_lists(r) for r in user)]
    _user_rules = [r for r, problem in bad if problem is None]
    _rules = None
    return [(r, p) for r, p in bad if p]


def _params(info):
    p = info.get("dhcp_params") or ""
    return ",".join(str(x) for x in p) if isinstance(p, list) else str(p)


def _check(field, want, ev):
    """Evidence text if this one condition holds (may be ""), else None."""
    if field == "mac_prefix":
        return f"MAC prefix {want.lower()}" if ev["mac"].startswith(want.lower()) else None
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
    t = table or rules()
    for mine, tiers in ((True, t.user), (False, t.tiers)):
        for tier in tiers.values():
            for conds, r in tier:
                src = _match(conds, ev)
                if src is not None:
                    return r["type"], (f"custom rule: {src}" if mine else src), r.get("vendor", ven)
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


# Bonjour evidence ages: per kind, only what was announced within RECENT of the newest item counts, so a
# device's current announcements outrank stale ones (e.g. another device's model it once relayed)
MDNS_KINDS = ("models", "names", "services", "hosts")
RECENT = 86400


def seen_key(kind, item):
    return f"{kind[0]}|{item}"


def current_mdns(info):
    """A copy of `info` whose mdns_* lists hold only current items. Items recorded before timestamps
    existed count only while the kind has no timestamped item at all."""
    info = dict(info or {})
    seen = info.get("seen") if isinstance(info.get("seen"), dict) else {}
    for kind in MDNS_KINDS:
        items = info.get(f"mdns_{kind}") or []
        stamped = {i: seen[seen_key(kind, i)] for i in items if seen_key(kind, i) in seen}
        if stamped:
            newest = max(stamped.values())
            info[f"mdns_{kind}"] = [i for i in items if stamped.get(i, -1) >= newest - RECENT]
    return info


# random identifiers some devices announce as host names: UUIDs and runs of 12 or more hex digits, with an
# optional short suffix (HomeKit bridges announce "<32 hex digits>-hap")
ID_NAME = re.compile(r"(?i)^([0-9a-f]{12,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(-[a-z]{1,8})?$")


# names Apple devices send instead of their own when a private Wi-Fi address is on
PLACEHOLDERS = {"mac", "iphone", "ipad", "watch", "apple-watch", "ipod", "localhost"}


def display_name(*candidates):
    """The first real name: lease or static name, DHCP host name, Bonjour name. A generic
    placeholder ("Mac", "iPhone") or an ID (a receiver calling itself "0005CD000001") only if nothing
    better is known."""
    names = [c for c in candidates if c]
    return next((n for n in names if n.lower() not in PLACEHOLDERS and not ID_NAME.match(n)),
                names[0] if names else None)


def mdns_name(info):
    """The name a device announces over Bonjour ("midnight" from midnight.local).

    Apple TVs and HomePods also announce names for the Thread/Matter accessories they relay, so a
    device's own name is preferred: the one that also appears as its AirPlay name ("70-35-60-63.1 kitchen").
    """
    hosts = [h[:-len(".local")] for h in (info or {}).get("mdns_hosts", [])
             if h.endswith(".local") and not h.startswith("_") and not ID_NAME.match(h[:-len(".local")])]
    names = [n for n in (info or {}).get("mdns_names", []) if not ID_NAME.match(n)]
    airplay = {n.split(" ", 1)[1].replace(" ", "-").lower() for n in names if re.match(r"^[0-9-]+\.\d+ ", n)}
    own = [h for h in hosts if h.lower() in airplay]
    return (own or hosts or names or [None])[0]


# Where OPNsense's DHCP servers keep their leases and fixed entries. Each server that is not in use simply
# has no file; all that exist are read, dnsmasq first.
DNSMASQ_LEASES = "/var/db/dnsmasq.leases"
DNSMASQ_HOSTS = "/var/etc/dnsmasq-hosts"
KEA_LEASES = "/var/db/kea/kea-leases4.csv"
KEA_CONF = "/usr/local/etc/kea/kea-dhcp4.conf"
ISC_LEASES = "/var/dhcpd/var/db/dhcpd.leases"
ISC_CONF = "/var/dhcpd/etc/dhcpd.conf"


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _dnsmasq_leases(text):
    out = {}
    for line in text.splitlines():
        p = line.split()
        if len(p) >= 4 and p[3] != "*":
            out[p[1].lower()] = p[3]
    return out


def _kea_leases(text):
    """Kea memfile CSV: address,hwaddr,client_id,valid_lifetime,expire,...,hostname,state,... (newest last)."""
    out, newest = {}, {}
    for row in csv.DictReader(text.splitlines()):
        mac, name = (row.get("hwaddr") or "").lower(), (row.get("hostname") or "").rstrip(".")
        try:
            expire = int(row.get("expire") or 0)
        except ValueError:
            expire = 0
        if mac and name and row.get("state", "0") != "2" and expire >= newest.get(mac, 0):   # 2: expired
            out[mac], newest[mac] = name.split(".")[0], expire
    return out


def _isc_leases(text):
    """ISC dhcpd.leases: lease ADDR { ... hardware ethernet MAC; client-hostname "NAME"; } (newest last)."""
    out = {}
    for block in re.finditer(r"lease \S+ \{(.*?)\}", text, re.S):
        body = block.group(1)
        mac = re.search(r"hardware ethernet ([0-9a-fA-F:]{17});", body)
        name = re.search(r'client-hostname "([^"]+)";', body)
        if mac and name and not re.search(r"^\s*binding state free;", body, re.M):   # not "next binding state"
            out[mac.group(1).lower()] = name.group(1)
    return out


def leases():
    """mac -> host name the device registered with, from whichever DHCP servers keep leases here."""
    out = {}
    out.update(_isc_leases(_read(ISC_LEASES)))
    out.update(_kea_leases(_read(KEA_LEASES)))
    out.update(_dnsmasq_leases(_read(DNSMASQ_LEASES)))
    return out


def _dnsmasq_hosts(text):
    out = {}
    for line in text.splitlines():
        p = line.split()
        if len(p) >= 3 and not line.startswith("#"):
            out.setdefault(p[0], p[2])
    return out


def _kea_reservations(text):
    """Reservations (hostname + ip-address) anywhere in Kea's dhcp4 config."""
    text = re.sub(r"(?m)^\s*(#|//).*$", "", text)          # Kea allows comment lines
    try:
        conf = json.loads(text)
    except ValueError:
        return {}
    out = {}

    def walk(node):
        if isinstance(node, dict):
            if node.get("ip-address") and node.get("hostname"):
                out.setdefault(node["ip-address"], node["hostname"].split(".")[0])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(conf)
    return out


def _isc_hosts(text):
    """host LABEL { ... fixed-address IP; option host-name "NAME"; } in dhcpd.conf. OPNsense labels the blocks
    s_<interface>_<n>; the name is in the host-name option."""
    out = {}
    for block in re.finditer(r"host (\S+) \{(.*?)\}", text, re.S):
        ip = re.search(r"fixed-address ([0-9.]+);", block.group(2))
        name = re.search(r'option host-name "([^"]+)";', block.group(2))
        label = block.group(1).strip('"')
        if ip and (name or not re.match(r"^s_\w+_\d+$", label)):
            out.setdefault(ip.group(1), name.group(1) if name else label)
    return out


def static_hosts():
    """ip -> name from the DHCP servers' fixed entries (reservations, static mappings, host entries)."""
    out = {}
    # Kea ships installed with a sample config full of example reservations: only trust a server's
    # fixed entries while it is actually handing out leases here
    kea = _kea_reservations(_read(KEA_CONF)) if os.path.exists(KEA_LEASES) else {}
    isc = _isc_hosts(_read(ISC_CONF)) if os.path.exists(ISC_LEASES) else {}
    for found in (_dnsmasq_hosts(_read(DNSMASQ_HOSTS)), kea, isc):
        for ip, name in found.items():
            out.setdefault(ip, name)
    return out
