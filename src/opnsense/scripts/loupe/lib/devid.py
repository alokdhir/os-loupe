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

Strongest first: a model the device announces over mDNS, its hostname, its DHCP
fingerprint, the services it talks to, and the vendor of its MAC address.
Each guess carries its source so the GUI can explain it.
"""
import csv
import re

from . import applemodels

OUI_CSV = "/usr/local/opnsense/contrib/ieee/oui.csv"
OUI_LEN = {"MA-L": 6, "MA-M": 7, "MA-S": 9}

# mDNS TXT model values (model=, am=, md=, usb_mdl=, ty=) -> type
MODELS = [
    (r"^AppleTV", "Apple TV"),
    (r"^AudioAccessory", "HomePod"),
    (r"^(MacBook|Macmini|MacPro|iMac|Mac\d|MacStudio)", "Mac"),
    (r"^iPhone", "iPhone"),
    (r"^iPad", "iPad"),
    (r"^Watch", "Apple Watch"),
    (r"Chromecast|Google TV|Google Nest|Nest (Hub|Mini|Audio)|Google Home", "Google Cast device"),
    (r"(?i)sonos", "Sonos speaker"),
    (r"(?i)bravia|smart ?tv|^(LG|Samsung|Vizio|TCL|Hisense)", "TV"),
    (r"(?i)roku", "Roku"),
    (r"(?i)^(usb_mdl|ty)=|laserjet|officejet|deskjet|envy|pixma|ecotank|brother|epson", "Printer"),
]

# mDNS services announced -> type (weaker than a model)
SERVICES = [
    ("_googlecast._tcp", "Google Cast device"),
    ("_sonos._tcp", "Sonos speaker"),
    ("_ipp._tcp", "Printer"), ("_ipps._tcp", "Printer"), ("_printer._tcp", "Printer"), ("_pdl-datastream._tcp", "Printer"),
    ("_hap._tcp", "HomeKit accessory"), ("_hap._udp", "HomeKit accessory"),
    ("_matter._tcp", "Matter device"),
    ("_airplay._tcp", "AirPlay device"), ("_raop._tcp", "AirPlay device"),
    ("_channels_dvr._tcp", "Media server"), ("_plexmediasvr._tcp", "Media server"),
    ("_spotify-connect._tcp", "Speaker / streamer"),
    ("_smb._tcp", "Computer"), ("_ssh._tcp", "Computer"),
]

HOSTNAMES = [
    (r"(?i)iphone", "iPhone"), (r"(?i)ipad", "iPad"), (r"(?i)macbook|imac|mac-?mini|mac-?studio|mac-?pro", "Mac"),
    (r"(?i)apple-?tv", "Apple TV"), (r"(?i)homepod", "HomePod"), (r"(?i)watch", "Watch"),
    (r"(?i)^(ps[345]|playstation)", "PlayStation"), (r"(?i)xbox", "Xbox"), (r"(?i)nintendo|switch", "Nintendo Switch"),
    (r"(?i)galaxy|pixel|android|oneplus|moto", "Android phone"),
    (r"(?i)desktop-|laptop-|^win", "Windows PC"),
    (r"(?i)roku", "Roku"), (r"(?i)fire-?tv|amazon-", "Amazon device"), (r"(?i)echo", "Echo speaker"),
    (r"(?i)chromecast|google-?(home|nest|tv)", "Google Cast device"),
    (r"(?i)ecobee|thermostat|nest-?(learning|thermostat)", "Thermostat"),
    (r"(?i)printer|^hp[0-9a-f]{6}|^brw|^epson|^canon", "Printer"),
    (r"(?i)^(cam|camera)|reolink|ring-|wyze|arlo", "Camera"),
    (r"(?i)tv$|-tv-|bravia|lgwebos|samsung", "TV"),
    (r"(?i)raspberrypi|^rpi", "Raspberry Pi"),
    (r"(?i)denon|marantz|onkyo|yamaha-?(rx|av)|avr", "AV receiver"),
    (r"(?i)^(EP|HS|KP|KS|P1)\d\d|kasa|smart-?plug", "Smart plug"),
    (r"(?i)^GEModule|^GE[-_]", "GE appliance"),
    (r"(?i)tuya|^wlan0$|^esp[-_]", "Smart home device"),
]

# DHCP option 60 (vendor class) prefixes
DHCP_VENDOR = [
    ("MSFT", "Windows PC"),
    ("android-dhcp", "Android device"),
    ("dhcpcd", "Linux device"),
    ("udhcp", "Embedded Linux device"),
    ("Mfg=", "Printer"),
]
# Apple's DHCP parameter request list (iOS, iPadOS, macOS) starts like this, with no option 60
APPLE_PARAMS = [1, 121, 3, 6, 15, 108, 114, 119, 252]

# Services a device talks to -> what it is (name suffixes, matched on looked-up names)
TRAFFIC = [
    ("playstation.net", "PlayStation"), ("playstation.com", "PlayStation"), ("sonyentertainmentnetwork.com", "PlayStation"),
    ("xboxlive.com", "Xbox"), ("xbox.com", "Xbox"),
    ("nintendo.net", "Nintendo Switch"), ("nintendo.com", "Nintendo Switch"),
    ("roku.com", "Roku"), ("ring.com", "Ring device"), ("ecobee.com", "Thermostat"),
    ("sonos.com", "Sonos speaker"), ("tuyaus.com", "Smart home device"), ("tuya.com", "Smart home device"),
    ("wyzecam.com", "Camera"), ("reolink.com", "Camera"), ("meethue.com", "Hue bridge"),
    ("amazon-dss.com", "Echo speaker"), ("alexa.amazon.com", "Echo speaker"),
    ("tesla.services", "Tesla"), ("tesla.com", "Tesla"),
]

VENDORS = [
    ("Tuya", "Smart home device"), ("Espressif", "IoT device (ESP)"), ("Sonos", "Sonos speaker"),
    ("Reolink", "Camera"), ("Roku", "Roku"), ("ecobee", "Thermostat"), ("Lutron", "Lutron hub"),
    ("Ubiquiti", "Network gear"), ("eero", "Wi-Fi access point"), ("Raspberry Pi", "Raspberry Pi"),
    ("Nintendo", "Nintendo Switch"), ("Sony Interactive", "PlayStation"), ("Nest Labs", "Nest device"),
    ("Phaten", "Ceiling fan (fanSync)"), ("Signify", "Hue bridge"), ("Philips Lighting", "Hue bridge"),
    ("Hon Hai", "Device (Foxconn module)"), ("Amazon Technologies", "Amazon device"), ("Google", "Google device"),
    ("Apple", "Apple device"), ("Samsung", "Samsung device"), ("HP Inc", "Printer"), ("Hewlett Packard", "HP device"),
    ("Intel", "Computer"), ("Synology", "NAS"), ("QNAP", "NAS"), ("TP-LINK", "TP-Link device"),
    ("Aqara", "Smart home hub"), ("Lumi United", "Smart home hub"), ("D&M Holdings", "AV receiver"),
    ("ASUSTek", "Computer"), ("Micro-Star", "Computer"), ("Dell", "Computer"), ("Lenovo", "Computer"),
    ("General Electric", "GE appliance"), ("Magicjack", "VoIP adapter"), ("Globalscale", "Embedded device"),
    ("Telit", "Cellular IoT module"), ("Wyze", "Camera"), ("Ring", "Ring device"),
]


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


def _match(rules, text):
    for pat, t in rules:
        if re.search(pat, text):
            return t
    return None


def classify(mac, hostname=None, info=None, names=(), oui=None):
    """Return (type, source, vendor). `names` are server names the device looked up or connected to."""
    info = info or {}
    ven = vendor(mac, oui or {}) if not is_private(mac) else None
    if mac.lower().startswith("52:54:00"):
        return "Virtual machine", "mac", "QEMU/KVM"

    for m in info.get("mdns_models", []):
        val = m.split("=", 1)[1] if "=" in m else m
        t = _match(MODELS, val) or (_match(MODELS, m) if m.startswith(("usb_mdl=", "ty=")) else None)
        if t:
            return t, f"mdns model {val}", ven
    candidates = [hostname or "", info.get("dhcp_hostname") or ""] + list(info.get("mdns_hosts", []))
    for h in candidates:
        t = h and _match(HOSTNAMES, h.removesuffix(".local"))
        if t:
            return t, f"hostname {h}", ven
    hosted = server_role(info)
    if hosted:
        return "Server", hosted, ven
    for svc, t in SERVICES[:6]:            # strong services (cast, sonos, printing)
        if svc in info.get("mdns_services", []):
            return t, f"mdns {svc}", ven
    vc = info.get("dhcp_vendor_class") or ""
    for prefix, t in DHCP_VENDOR:
        if vc.startswith(prefix):
            return t, f"dhcp vendor {vc}", ven
    for n in names:
        for suffix, t in TRAFFIC:
            if n == suffix or n.endswith("." + suffix):
                return t, f"talks to {suffix}", ven
    params = info.get("dhcp_params") or []
    if params[:len(APPLE_PARAMS)] == APPLE_PARAMS and not vc:
        return ("Apple device" if not is_private(mac) else "iPhone/iPad/Mac"), "dhcp fingerprint", ven
    for svc, t in SERVICES[6:]:
        if svc in info.get("mdns_services", []):
            return t, f"mdns {svc}", ven
    if ven:
        t = _match([(r"(?i)\b" + re.escape(k) + r"\b", v) for k, v in VENDORS], ven)
        if t:
            return t, f"vendor {ven}", ven
    if is_private(mac):
        return "Phone/tablet/laptop", "private MAC", None
    return None, None, ven


HOMEKIT_HOSTS = ("homebridge", "scrypted")
SERVER_SERVICES = ("_smb._tcp", "_channels_dvr._tcp", "_plexmediasvr._tcp")


def server_role(info):
    """A machine hosting HomeKit bridges (Homebridge, Scrypted) or serving files/media next to HomeKit is a
    server, not an accessory. Returns the evidence, or None."""
    svcs = info.get("mdns_services", [])
    hosts = sorted({m.split("=", 1)[1] for m in info.get("mdns_models", [])
                    if m.startswith("md=") and m.split("=", 1)[1].lower() in HOMEKIT_HOSTS})
    if hosts:
        return "mdns " + " + ".join(hosts)
    if "_hap._tcp" in svcs:
        extra = [s for s in SERVER_SERVICES if s in svcs]
        if extra:
            return "mdns _hap._tcp + " + " + ".join(extra)
    return None


def model(info):
    """Marketing name of an Apple device from its announced model identifier, if known."""
    for m in (info or {}).get("mdns_models", []):
        hit = applemodels.name(m.split("=", 1)[1] if "=" in m else m)
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
