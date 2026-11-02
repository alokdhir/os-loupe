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
# than guessing from a private MAC; a HomeKit or Matter category is exact but generic, so it comes after the
# specific Bonjour services: a Fire TV stays a Fire TV rather than a "TV streamer")
TIERS = ("mac_prefix", "mdns_model", "mdns_name", "hostname", "role", "strong_service", "category", "dhcp_vendor",
         "dhcp_params", "talks_to", "mdns_service", "vendor", "fallback", "private_mac")


REGEX_FIELDS = ("hostname", "mdns_model", "mdns_name", "dhcp_vendor")


def _tiers(rules):
    """Each rule's conditions under its tier, in file order. A shipped entry is one device type with several
    alternatives ("any"): each is tried at its own tier, as a rule of that type."""
    tiers = {t: [] for t in TIERS}
    for r in rules:
        for alt in r["any"] if "any" in r else [r]:
            rule = {**{k: v for k, v in r.items() if k != "any"}, **alt} if "any" in r else r
            conds = rule["all"] if "all" in rule else [rule["when"]]
            tier = rule.get("tier") or next(iter(conds[0]))
            tiers[tier].append((conds, rule))
    return tiers


def check_rule(r):
    """None if a rule is usable, else what is wrong with it (user rules come from the GUI)."""
    try:
        conds = r["all"] if "all" in r else [r["when"]]
        if not r.get("type"):
            return "no type"
        if not all(conds):
            return "no clues"
        for cond in conds:
            for field, want in cond.items():
                if field not in TIERS or field in ("role", "strong_service", "fallback"):
                    return f"unknown field {field}"
                for w in (want if isinstance(want, list) else [want]):
                    if field in REGEX_FIELDS:
                        re.compile(w)
                    elif field == "category" and not category_code(w):
                        return f"not a category code: {w}"
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
        self.computer_vendors = data.get("computer_vendors", [])
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


LISTABLE = ("mac_prefix", "vendor", "mdns_service", "talks_to", "category")     # a comma can't be part of these


def _split_lists(r):
    """GUI rules hold one pattern per clue; for fields where a comma can't occur, "a, b" means a or b.
    A compound rule (several clues) is tried with the rules of its strongest clue."""
    if "when" not in r:
        return r
    when = {f: ([x.strip() for x in w.split(",") if x.strip()] if f in LISTABLE and isinstance(w, str) and "," in w else w)
            for f, w in r["when"].items()}
    if "mac_prefix" in when:
        prefixes = when["mac_prefix"] if isinstance(when["mac_prefix"], list) else [when["mac_prefix"]]
        when["mac_prefix"] = [mac_prefix(p) for p in prefixes]
    if when and "tier" not in r and all(f in TIERS for f in when):
        r = {**r, "tier": min(when, key=TIERS.index)}
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
    if field == "mdns_name":
        for n in ev["instances"]:
            if re.search(want, n):
                return f"mdns name {n}"
        return None
    if field == "category":
        want = category_code(want)
        return f"{CATEGORY_KINDS[want.split(':')[0]]} {want.split(':')[1]}" if want and want in ev["categories"] else None
    if field == "hostname":
        for h in ev["hosts"]:
            if h and re.search(want, h.removesuffix(".local"), re.IGNORECASE):     # host names have no case
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
        if ev["computer"]:
            return None
        hit = any(n == want or n.endswith("." + want) for n in ev["names"])
        return f"talks to {want}" if hit else None
    if field == "vendor":
        return f"vendor {ev['vendor']}" if ev["vendor"] and re.search(r"(?i)\b" + re.escape(want) + r"\b", ev["vendor"]) else None
    if field == "private_mac":
        return ("private MAC" if want else "") if ev["private"] == want else None
    raise ValueError(f"unknown rule field {field}")


# standard category codes devices announce: HomeKit's accessory category (TXT ci= on _hap), Matter's device type
# (TXT DT= on _matterc/_matterd, or their _T<n> subtypes); kept as "hap:9", "matter:0x0301"
CATEGORY_KINDS = {"hap": "HomeKit category", "matter": "Matter device type"}
MATTER_SUBTYPE = re.compile(r"^_t(\d+)\._sub\._matter[cd]\._udp$")


def category_code(text):
    """"hap:9", "HAP:09" -> "hap:9"; "matter:769", "matter:0x301" -> "matter:0x0301"; None if not a code."""
    kind, _, num = str(text).strip().lower().partition(":")
    try:
        n = int(num, 0) if num.startswith("0x") else int(num)
    except ValueError:
        return None
    if kind == "hap":
        return f"hap:{n}"
    if kind == "matter":
        return f"matter:0x{n:04x}"
    return None


def categories(info):
    """The category codes a device announces. A device announcing several HomeKit categories is a bridge
    showing other things' categories (e.g. Home Assistant's HomeKit Bridge in accessory mode): none of them count."""
    found = {category_code(c) for c in info.get("mdns_categories", [])}
    for s in info.get("mdns_services", []):
        m = MATTER_SUBTYPE.match(s)
        if m:
            found.add(category_code(f"matter:{m.group(1)}"))
    found.discard(None)
    hap = {c for c in found if c.startswith("hap:")}
    return found - hap if len(hap) > 1 else found


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
          "instances": [n for n in info.get("mdns_names", []) if not _id_item("names", n)], "categories": categories(info),
          "vc": info.get("dhcp_vendor_class") or "", "params": _params(info),
          "hosts": [hostname or "", info.get("dhcp_hostname") or ""] + list(info.get("mdns_hosts", []))}
    t = table or rules()
    computer = private or any(re.search(r"(?i)\b" + re.escape(v) + r"\b", ven or "") for v in t.computer_vendors)
    for mine, tiers in ((True, t.user), (False, t.tiers)):
        for tier in tiers.values():
            for conds, r in tier:
                # talks_to skips phones, computers and private MACs (using a vendor's app or site isn't being its
                # device), except in rules the user typed in; an AI-suggested rule is held to it like a shipped one
                ev["computer"] = computer and not (mine and r.get("source") != "ai")
                src = _match(conds, ev)
                if src is not None:
                    by = "AI rule" if r.get("source") == "ai" else "custom rule"     # the AI flow's, or typed in
                    return r["type"], (f"{by}: {src}" if mine else src), r.get("vendor", ven)
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


# the Bonjour TXT keys that name a model, most specific first: a Sony TV announces its platform as md= ("BRAVIA 4K
# VH22") and its model as model= ("XR-98X90L"); a Chromecast only md= ("Google Nest Mini"); printers ty= / usb_mdl=
MODEL_ORDER = ("model", "am", "ty", "usb_mdl", "md")
# announced values that aren't a model: feature lists (md=0,1,2), Apple board codes (J255AP), hex IDs
NOT_A_MODEL = re.compile(r"^[\d,.\s]*$|^[A-Z]\d+[a-z]*AP$|^(?i:[0-9a-f]{12,})$")


def model(info):
    """The device's model: an Apple identifier's marketing name when known (apple_models), else what it announces
    in Bonjour TXT, by MODEL_ORDER. A key with several different values names other things (a bridge announcing
    the accessories it serves): no model from it, nor from the keys after it."""
    announced = (info or {}).get("mdns_models", [])
    names = rules().apple_models
    for m in announced:
        hit = names.get(m.split("=", 1)[1] if "=" in m else m)
        if hit:
            return hit
    values = {}
    for m in announced:
        key, _, value = m.partition("=")
        value = value.strip()
        if value and not NOT_A_MODEL.match(value):
            values.setdefault(key, set()).add(value)
    for key in MODEL_ORDER:
        if key in values:
            return next(iter(values[key])) if len(values[key]) == 1 else None
    return None


# Bonjour evidence ages: per kind, only what was announced within RECENT of the newest item counts, so a
# device's current announcements outrank stale ones (e.g. another device's model it once relayed)
MDNS_KINDS = ("models", "names", "services", "hosts", "categories")
RECENT = 86400


def seen_key(kind, item):
    return f"{kind[0]}|{item}"


def current_mdns(info):
    """A copy of `info` whose mdns_* lists hold only current items. Items recorded before timestamps
    existed count only while the kind has no timestamped item at all. Random IDs (an iPhone's UUID host
    names) are never used as names, so they don't make a kind's other items look old."""
    info = dict(info or {})
    seen = info.get("seen") if isinstance(info.get("seen"), dict) else {}
    for kind in MDNS_KINDS:
        items = info.get(f"mdns_{kind}") or []
        stamped = {i: seen[seen_key(kind, i)] for i in items if seen_key(kind, i) in seen and not _id_item(kind, i)}
        if stamped:
            newest = max(stamped.values())
            info[f"mdns_{kind}"] = [i for i in items if stamped.get(i, -1) >= newest - RECENT]
    return info


# random identifiers some devices announce as host names: UUIDs and runs of 12 or more hex digits, with an
# optional short suffix (HomeKit bridges announce "<32 hex digits>-hap")
ID_NAME = re.compile(r"(?i)^([0-9a-f]{12,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(-[a-z]{1,8})?$")


# names Apple devices send instead of their own when a private Wi-Fi address is on
PLACEHOLDERS = {"mac", "iphone", "ipad", "watch", "apple-watch", "ipod", "localhost"}

# Bonjour instance names that are never a device's name: iOS announces "localnetworkprivacy" while it checks
# Local Network access; Spotify's group sessions are named "Spotify Group Session [code]"
NOT_NAMES = re.compile(r"(?i)^(localnetworkprivacy|spotify group session( \[[^]]*\])?)$")


def _id_item(kind, item):
    """An announced host or instance name that is only an ID or never a name."""
    if kind == "hosts":
        item = item[:-len(".local")] if item.endswith(".local") else item
    elif kind != "names":
        return False
    return bool(ID_NAME.match(item) or NOT_NAMES.match(item))


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
             if h.endswith(".local") and not h.startswith("_") and not _id_item("hosts", h)]
    names = [n for n in (info or {}).get("mdns_names", []) if not _id_item("names", n)]
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


# ---- one device, several MACs: an interface that replaced another (loupd links them, lib/store.py device_links) --
RELAYS_OVER = 5         # a MAC announcing more host names than this relays others' records (a Thread border router)
TOGETHER_DAYS = 2       # two MACs with traffic on more days than this together are two devices


def _sibling(a, b):
    """How two MACs look related: same maker block, a few addresses apart, or the locally administered twin."""
    x, y = int(a.replace(":", ""), 16), int(b.replace(":", ""), 16)
    if abs((x ^ (0x02 << 40)) - y) <= 16:
        return "locally administered twin"
    if a[:8] == b[:8] and not (is_private(a) or is_private(b)):
        return "adjacent MACs" if abs(x - y) <= 16 else "same maker"
    return None


def replaced_interfaces(devices, active_days):
    """Pairs of MACs that are one device on two interfaces used at different times (an Apple TV moved from Wi-Fi to
    Ethernet, a Mac's dock and its Wi-Fi, a phone's new private address): [(mac, mac, reason)].

    `devices`: {mac: {"name": its DHCP host name, "type": its type, "hosts": Bonjour host names it announced}};
    `active_days(mac)`: the days it had traffic. A pair shares its own name - one only these two MACs ever used, so
    not a default name (eero, iphone) - is related (same maker, adjacent, the locally administered twin, or a private
    address with the same type), and was hardly ever active on the same days."""
    by_name = {}
    for mac, d in devices.items():
        hosts = {h.removesuffix(".local").lower() for h in d.get("hosts") or () if h.endswith(".local")}
        hosts = {h for h in hosts if not ID_NAME.match(h)}
        names = {(d.get("name") or "").lower()} | (hosts if len(hosts) <= RELAYS_OVER else set())
        for n in names:
            if n and n not in PLACEHOLDERS and not ID_NAME.match(n):
                by_name.setdefault(n, set()).add(mac)
    out = []
    for name, macs in sorted(by_name.items()):
        if len(macs) != 2:
            continue
        a, b = sorted(macs)
        how = _sibling(a, b)
        if not how and (is_private(a) or is_private(b)) and devices[a].get("type") \
                and devices[a].get("type") == devices[b].get("type"):
            how = "private address, same type"
        if how and len(active_days(a) & active_days(b)) <= TOGETHER_DAYS:
            out.append((a, b, f"both named {name}, {how}, not active together"))
    return out
