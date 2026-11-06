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



"""Which device is on which switch port. A refresh reads each switch's MAC table and ports over SNMP (poll) and keeps
the raw result; report time works out what each port leads to (locate) and describes it with Loupe's names (ports). Only
standard MIBs (BRIDGE-MIB, Q-BRIDGE-MIB, IF-MIB). Nothing here needs root."""
import concurrent.futures
import json
import os
import re
import time

from . import snmp

SYS_DESCR = snmp.oid_tuple("1.3.6.1.2.1.1.1.0")
SYS_NAME = snmp.oid_tuple("1.3.6.1.2.1.1.5.0")
BRIDGE_ADDR = snmp.oid_tuple("1.3.6.1.2.1.17.1.1.0")
BASE_PORT_IF = snmp.oid_tuple("1.3.6.1.2.1.17.1.4.1.2")
FDB_BRIDGE = snmp.oid_tuple("1.3.6.1.2.1.17.4.3.1.2")       # BRIDGE-MIB: index is the MAC
FDB_QBRIDGE = snmp.oid_tuple("1.3.6.1.2.1.17.7.1.2.2.1.2")  # Q-BRIDGE-MIB: index is the VLAN and the MAC
IF_DESCR = snmp.oid_tuple("1.3.6.1.2.1.2.2.1.2")
IF_TYPE = snmp.oid_tuple("1.3.6.1.2.1.2.2.1.3")
IF_OPER = snmp.oid_tuple("1.3.6.1.2.1.2.2.1.8")
IF_NAME = snmp.oid_tuple("1.3.6.1.2.1.31.1.1.1.1")
IF_SPEED = snmp.oid_tuple("1.3.6.1.2.1.31.1.1.1.15")
ETHERNET = {6, 62, 69, 117}         # ifType: ethernetCsmacd, fastEther, fastEtherFX, gigabitEthernet
UP, NOT_PRESENT = 1, 6
POLL_SECONDS = 30                   # the most one switch may take
SWITCH_ID = re.compile(r"^[0-9a-fA-F-]{36}$")


def _text(value):
    return value.decode("utf-8", "replace").strip("\0 \t\r\n") if isinstance(value, bytes) else ""


def _mac(octets):
    return ":".join(f"{b:02x}" for b in octets)


def fdb_mac(index):
    """A Q-BRIDGE row's MAC. The standard index is vlan.mac; some switches add the length: vlan.6.mac."""
    if len(index) == 8 and index[1] == 6:
        index = index[:1] + index[2:]
    if len(index) != 7 or any(not 0 <= p <= 255 for p in index[1:]):
        return None
    return _mac(index[1:])


def short_port(name):
    return re.sub(r"^TenGigabitEthernet", "TE", name)


def speed_text(mbit):
    if not mbit:
        return ""
    return f"{mbit / 1000:g}G" if mbit >= 1000 else f"{mbit}M"


def model_of(descr):
    return descr.split(",")[0].strip()


def read_tables(client):
    """{descr, sysname, mac, ports: {ifIndex: {name, up, speed}}, fdb: {ifIndex: [MACs]}, ignored}, MACs on down ports left out."""
    descr = _text(client.get(SYS_DESCR))
    sysname = _text(client.get(SYS_NAME))
    bridge = client.get(BRIDGE_ADDR)
    names = client.walk(IF_NAME) or client.walk(IF_DESCR)
    types = client.walk(IF_TYPE)
    opers = client.walk(IF_OPER)
    speeds = client.walk(IF_SPEED)
    base = {i[0]: v for i, v in client.walk(BASE_PORT_IF).items() if len(i) == 1}
    table = {}                  # MAC -> bridge ports
    for index, port in client.walk(FDB_QBRIDGE).items():
        mac = fdb_mac(index)
        if mac and port:
            table.setdefault(mac, set()).add(port)
    if not table:               # the older table, for switches without the VLAN one
        for index, port in client.walk(FDB_BRIDGE).items():
            if len(index) == 6 and port and all(0 <= p <= 255 for p in index):
                table.setdefault(_mac(index), set()).add(port)
    on_port = {}
    for mac, bridge_ports in table.items():
        for port in bridge_ports:
            on_port.setdefault(base.get(port, port), []).append(mac)
    ports, fdb, ignored = {}, {}, 0
    for index, name in names.items():
        if len(index) != 1 or opers.get(index) == NOT_PRESENT:
            continue
        i = index[0]
        macs = on_port.get(i, [])
        if _text(name).lower().startswith("cpu") or (types.get(index) not in ETHERNET and not macs):
            continue                # the CPU, VLANs, unused link aggregates
        up = opers.get(index) == UP
        ports[str(i)] = {"name": short_port(_text(name)), "up": up, "speed": speeds.get(index, 0) if up else 0}
        if up:
            if macs:
                fdb[str(i)] = sorted(macs)
        else:
            ignored += len(macs)    # switches can list stale entries on a port with nothing plugged in
    return {"descr": descr, "sysname": sysname, "mac": _mac(bridge) if isinstance(bridge, bytes) and len(bridge) == 6 else "",
            "ports": ports, "fdb": fdb, "ignored": ignored}


def poll(address, community, now=None):
    """Read one switch. Returns its tables with `fetched`, or {"error": why}."""
    try:
        with snmp.Client(address, community, deadline=time.monotonic() + POLL_SECONDS) as client:
            out = read_tables(client)
    except snmp.SnmpError as e:
        return {"error": "no SNMP answer: check the address, that SNMP is on and the community" if str(e) == "no answer" else str(e)}
    except (OSError, ValueError) as e:
        return {"error": str(e)}
    if not out["ports"]:
        return {"error": "it answered, but lists no ports"}
    if not out["fdb"]:
        out["note"] = "it answered, but its MAC table is empty"
    out["fetched"] = int(now or time.time())
    return out


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"polls": {}, "eero": {}}
    return data if isinstance(data, dict) and isinstance(data.get("polls"), dict) else {"polls": {}, "eero": {}}


def save_state(path, state):
    tmp = f"{path}.{os.getpid()}"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def _router_poll(router):
    return {"descr": ", ".join(router["bridges"]), "ports": router["ports"], "fdb": router["fdb"], "fetched": int(time.time())}


def remember(memory, polls, router_macs, now):
    """Update {MAC: {switch, port, seen}}, the port each device was last found on. A switch forgets a device that goes
    quiet, so this is what says where it is until it talks again. Tables kept from a failed read don't overrule a newer
    finding, and devices not found for REMEMBER_DAYS are dropped, as are those on switches no longer listed."""
    _, _, where = locate(polls, router_macs)
    out = {mac: m for mac, m in memory.items()
           if m.get("switch") in polls and now - (m.get("seen") or 0) < REMEMBER_DAYS * 86400}
    for mac, (sid, port) in where.items():
        seen = polls[sid].get("fetched") or now
        if seen >= out.get(mac, {}).get("seen", 0):
            out[mac] = {"switch": sid, "port": port, "seen": seen}
    return out


def refresh(path, configured, target, eero, polling=poll, router_macs=(), router=None):
    """Read the enabled switches in `configured`, or only the one `target` names, and keep the results. A switch that
    fails keeps its last good tables, with why. Returns {id: error or None}."""
    wanted = [s for s in configured if s.get("enabled") and target in ("all", s["id"])]
    state = load_state(path)
    results = {}
    if wanted:
        with concurrent.futures.ThreadPoolExecutor(len(wanted)) as pool:
            results = dict(zip((s["id"] for s in wanted), pool.map(lambda s: polling(s["address"], s["community"]), wanted)))
    now = int(time.time())
    known = {s["id"] for s in configured}
    polls = {k: v for k, v in state["polls"].items() if k in known}
    for sid, result in results.items():
        if result.get("error"):
            kept = polls.get(sid, {})
            polls[sid] = {**kept, "error": result["error"], "tried": now}
        else:
            polls[sid] = {**result, "tried": now}
    listed = {s["id"]: polls[s["id"]] for s in configured if s.get("enabled") and s["id"] in polls}
    if listed and router and router.get("ports"):
        listed[ROUTER_ID] = _router_poll(router)    # last: where a device ties, a switch's port wins over the router's
    save_state(path, {"polls": polls, "eero": eero or {}, "last_ports": remember(state.get("last_ports") or {}, listed, router_macs, now)})
    return {sid: r.get("error") for sid, r in results.items()}


def locate(polls, router_macs):
    """Work out the links between the polled switches. Returns ({id: uplink ifIndex}, {id: {ifIndex: other switch's id}},
    {MAC: (id, ifIndex)}): each device on the port nearest to it, which is never an uplink or a link to another switch,
    and where several switches still list it (an unpolled switch between them), the port with the fewest devices."""
    router_macs = set(router_macs)
    by_mac = {p["mac"]: sid for sid, p in polls.items() if p.get("mac")}
    uplinks, links = {}, {}
    for sid, p in polls.items():
        if sid == ROUTER_ID:        # the top of the tree
            uplinks[sid] = None
            continue
        best = max(p.get("fdb", {}).items(), key=lambda kv: len(router_macs & set(kv[1])), default=None)
        uplinks[sid] = best[0] if best and router_macs & set(best[1]) else None
    behind = {}     # {id: {ifIndex: the switches listed there}}
    for sid, p in polls.items():
        behind[sid] = {}
        for i, macs in p.get("fdb", {}).items():
            if i != uplinks[sid]:
                others = {by_mac.get(mac) for mac in macs} - {None, sid}
                if others:
                    behind[sid][i] = others
    below = {sid: set().union(*ports.values()) if ports else set() for sid, ports in behind.items()}
    for sid, ports in behind.items():
        # a port can list a whole chain of switches: it links to the one the others are behind
        links[sid] = {i: sorted(others, key=lambda o: (any(o in below[t] for t in others if t != o), o))[0]
                      for i, others in ports.items()}
    places = {}
    for sid, p in polls.items():
        for i, macs in p.get("fdb", {}).items():
            if i == uplinks[sid] or i in links[sid]:
                continue
            for mac in macs:
                if mac not in by_mac and mac not in router_macs:
                    size = len(macs)
                    if mac not in places or size < places[mac][0]:
                        places[mac] = (size, sid, i)
    return uplinks, links, {mac: (sid, i) for mac, (_, sid, i) in places.items()}


def ranges(names):
    """TE2, TE3, TE4 -> TE2-TE4."""
    out, first, last, at = [], None, None, None

    def close():
        if first is not None:
            out.append(first if first == last else f"{first}-{last}")

    for name in names:
        m = re.match(r"(.*?)(\d+)$", name)
        key = (m.group(1), int(m.group(2))) if m else None
        if key and at and key == (at[0], at[1] + 1):
            last = name
        else:
            close()
            first = last = name
        at = key
    close()
    return ", ".join(out)


class Eero:
    """What os-eero says at refresh time, when it is installed: {"units": [...], "clients": [...]}."""

    def __init__(self, data):
        data = data if isinstance(data, dict) else {}
        units = [u for u in data.get("units") or [] if isinstance(u, dict) and u.get("ip")]
        clients = [c for c in data.get("clients") or [] if isinstance(c, dict) and c.get("mac")]
        self.units = {u["ip"]: u for u in units}
        self.clients = {}
        self.wifi = set()
        self.offline = set()
        for c in clients:
            mac = c["mac"].lower()
            if c.get("connected"):
                self.wifi.add(mac)
                self.clients.setdefault(c.get("unit") or "", []).append({**c, "mac": mac})
            else:
                self.offline.add(mac)

    def under(self, unit):
        """The units meshed through this one, over Wi-Fi."""
        via = f"Wireless via {unit.get('name')} "
        return [u for u in self.units.values() if str(u.get("backhaul") or "").startswith(via)]


def _plain(info, mac):
    """The fields the page shows for a device, as the Devices tab names it."""
    d = info.get(mac) or {}
    return {"id": d.get("mac") or mac, "name": d.get("name") or d.get("ip") or mac, "ip": d.get("ip") or "",
            "icon": d.get("icon") or "", "type": d.get("type") or "",
            "model": "" if d.get("model_in_name") else d.get("model") or "", "area": d.get("area") or "",
            "known": bool(d)}


def _detail(d):
    return " · ".join(x for x in (d["type"], d["model"], d["area"]) if x)


def _device_row(info, mac, traffic, kind="device", depth=0, note=""):
    d = _plain(info, mac)
    row = {"kind": kind, "name": d["name"], "ip": d["ip"], "icon": d["icon"], "detail": " · ".join(x for x in (_detail(d), note) if x),
           "depth": depth, "id": d["id"] if d["known"] else ""}
    used = traffic.get(d["id"]) or traffic.get(mac)
    if used:
        row["down"], row["up"] = used
    return row


def _client_row(info, client, traffic, depth):
    row = _device_row(info, client["mac"], traffic, "client", depth)
    if row["name"] == client["mac"] or not row["id"]:
        row["name"] = client.get("name") or row["name"]
    row["ip"] = row["ip"] or client.get("ip") or ""
    row["detail"] = row["detail"] or client.get("band") or ""
    return row


def _unit_rows(unit, eero, info, traffic, depth=0):
    clients = eero.clients.get(unit.get("name") or "", [])
    backhaul = unit.get("backhaul") or ""
    count = f"{len(clients)} Wi-Fi client{'' if len(clients) == 1 else 's'}"
    rows = [{"kind": "ap", "name": f"eero {unit.get('name') or ''}".strip(), "ip": unit["ip"], "icon": "fa-solid fa-wifi",
             "detail": f"{backhaul} · {count}" if backhaul else count, "depth": depth, "id": "", "unit": unit.get("name") or ""}]
    listed = [_client_row(info, c, traffic, depth + 1) for c in clients]
    rows.extend(sorted(listed, key=lambda r: r["name"].lower()))
    for other in eero.under(unit):
        rows.extend(_unit_rows(other, eero, info, traffic, depth + 1))
    return rows


def _port_devices(macs, eero, info, traffic):
    """A port's rows, and what the page should say about the port as a whole."""
    addresses = {(info.get(m) or {}).get("ip"): m for m in macs if (info.get(m) or {}).get("ip")}
    units = [u for ip, u in eero.units.items() if ip in addresses]
    radios = {addresses[u["ip"]][:14] for u in units}       # an eero's radios have MACs next to its wired one
    wired, ap = {}, []
    for mac in macs:
        if mac in eero.wifi or mac[:14] in radios:
            continue
        row = _device_row(info, mac, traffic, note="last seen on Wi-Fi" if mac in eero.offline else "")
        wired.setdefault(row["id"] or mac, row)
        if "access point" in (info.get(mac) or {}).get("type", "").lower():
            ap.append(row["name"])
    rows = []
    wired_units = sorted((u for u in units if not str(u.get("backhaul") or "").startswith("Wireless")),
                         key=lambda u: str(u.get("name")))
    for unit in wired_units:
        rows.extend(_unit_rows(unit, eero, info, traffic))
    rows.extend(sorted(wired.values(), key=lambda r: r["name"].lower()))
    note = ""
    if len(wired_units) > 1:
        note = f"{len(wired_units)} eeros on this port: another switch"
    elif ap and not units and len(wired) > 1:
        note = f"Same port as {', '.join(sorted(ap))}: these may be on its Wi-Fi or wired"
    return rows, note


ROUTER_ID = "router"
REMEMBER_DAYS = 30


def display_name(switch, poll_result):
    """What a switch is called: the name given in Loupe, the one set on the switch, its model, else its address."""
    return (switch.get("name") or poll_result.get("sysname") or model_of(poll_result.get("descr") or "")
            or switch.get("address") or "")


def all_names(configured, state):
    """{id: name} for every switch in the settings, enabled or not."""
    polls = state.get("polls", {})
    return {s["id"]: display_name(s, polls.get(s["id"], {})) for s in configured}


def _order(port_id):
    return int(port_id) if str(port_id).isdigit() else 0


def describe(configured, state, info, router_macs, traffic=None, router=None, since=0):
    """The whole map, as the tab and device pages use it: {id: {id, name, address, model, fetched, tried, error, note,
    up, total, devices, uplink, uplink_id, rows, down, ignored}} for the enabled switches, in the settings' order, and
    {MAC: where} for each device on a port or Wi-Fi. `router`: its own ports (netif.router_ports), which count as one
    more switch, the top of the tree, while any switch is listed. A device no switch lists now shows on the port it was
    last found on, marked `last_here`, if it has been seen since `since`."""
    traffic = traffic or {}
    enabled = [s for s in configured if s.get("enabled")]
    polls = {s["id"]: state.get("polls", {}).get(s["id"], {}) for s in enabled}
    if enabled and router and router.get("ports"):
        # last, so that where a device ties, a switch's port wins over the router's
        enabled.append({"id": ROUTER_ID, "name": "Router", "address": ""})
        polls[ROUTER_ID] = _router_poll(router)
    eero = Eero(state.get("eero"))
    uplinks, links, where = locate(polls, router_macs)
    on_port = {}
    for mac, place in where.items():
        on_port.setdefault(place, []).append(mac)
    names = {s["id"]: display_name(s, polls[s["id"]]) for s in enabled}
    last_on = {}    # {(id, port): {MAC: when found there}}
    for mac, m in (state.get("last_ports") or {}).items():
        d = info.get(mac)
        place = (m.get("switch"), m.get("port"))
        if (d and (d.get("last_seen") or 0) >= since and mac not in where and mac not in eero.wifi
                and not set(d.get("macs") or ()) & (set(where) | eero.wifi)
                and place[0] in polls and place[1] in polls[place[0]].get("ports", {})
                and place[1] != uplinks.get(place[0]) and place[1] not in links.get(place[0], {})):
            last_on.setdefault(place, {})[mac] = m.get("seen") or 0
    out = {}
    found = {}
    for s in enabled:
        sid = s["id"]
        p = polls[sid]
        ports = p.get("ports") or {}
        parent = next((other for other, ls in links.items() if sid in ls.values()), None)
        parent_port = next((i for i, o in links.get(parent, {}).items() if o == sid), None)
        uplink_text = ""
        if parent:
            uplink_text = f"{names[parent]} {(polls[parent].get('ports', {}).get(parent_port) or {}).get('name', '')}".strip()
        elif uplinks.get(sid):
            uplink_text = "Router"
        rows = []
        devices = 0
        for i in sorted(ports, key=_order):
            port = ports[i]
            remembered = last_on.get((sid, i), {})
            if not port.get("up") and not remembered:
                continue
            head = {"port": port.get("name") or i, "speed": speed_text(port.get("speed") or 0) if port.get("up") else "down"}
            if port.get("wan"):
                body = [{"kind": "wan", "name": "WAN", "ip": "", "icon": "fa-solid fa-globe", "detail": "", "depth": 0, "id": ""}]
                note = ""
            elif i == uplinks.get(sid):
                body = [{"kind": "uplink", "name": uplink_text or "Router", "ip": "", "icon": "", "detail": "", "depth": 0,
                         "id": parent or "", "switch": bool(parent)}]
                note = ""
            elif i in links[sid]:
                other = links[sid][i]
                body = [{"kind": "switch", "name": names[other], "ip": next((x["address"] for x in enabled if x["id"] == other), ""),
                         "icon": "fa-solid fa-network-wired", "detail": model_of(polls[other].get("descr") or ""), "depth": 0,
                         "id": other, "switch": True}]
                note = ""
            else:
                macs = on_port.get((sid, i), []) + list(remembered)
                body, note = _port_devices(macs, eero, info, traffic)
                when = {_device_row(info, mac, {})["id"]: seen for mac, seen in remembered.items()}
                for row in body:
                    if row["kind"] == "device" and row["id"] in when:
                        row["last_here"] = when[row["id"]]
                devices += sum(1 for r in body if r["kind"] in ("device", "client"))
                for mac in macs:
                    if mac not in eero.wifi:
                        found[mac] = {"switch": sid, "name": names[sid], "port": head["port"]}
                        if mac in remembered:
                            found[mac]["last_here"] = remembered[mac]
                if not body:
                    body = [{"kind": "none", "name": "", "ip": "", "icon": "", "detail": "", "depth": 0, "id": ""}]
            for n, row in enumerate(body):
                rows.append({**row, "port": head["port"] if n == 0 else "", "speed": head["speed"] if n == 0 else "",
                             "port_note": note if n == 0 else ""})
                if row["kind"] == "ap":     # who a Wi-Fi client is on: this eero, on this port
                    for c in eero.clients.get(row["unit"], []):
                        found[c["mac"]] = {"switch": sid, "name": names[sid], "port": head["port"], "via": row["name"]}
        down = [ports[i].get("name") or i for i in sorted(ports, key=_order) if not ports[i].get("up")]
        out[sid] = {"id": sid, "name": names[sid], "address": s.get("address") or "", "model": model_of(p.get("descr") or ""),
                    "fetched": p.get("fetched") or 0, "tried": p.get("tried") or 0, "error": p.get("error") or "",
                    "note": p.get("note") or "", "up": sum(1 for x in ports.values() if x.get("up")), "total": len(ports),
                    "devices": devices, "uplink": uplink_text, "uplink_id": parent or "", "rows": rows,
                    "down": ranges(down), "ignored": p.get("ignored") or 0}
    return out, found, eero


def elsewhere(described, found, eero, info, router_macs, since, traffic=None):
    """Devices seen since `since` that no port or eero accounts for (on the router's own ports, say, or a switch that
    isn't polled)."""
    traffic = traffic or {}
    skip = set(found) | eero.wifi | set(router_macs)
    addresses = {s["address"] for s in described.values()} | set(eero.units)
    rows = {}
    for mac, d in info.items():
        if mac in skip or set(d.get("macs") or ()) & skip or (d.get("last_seen") or 0) < since or d.get("ip") in addresses:
            continue
        row = _device_row(info, mac, traffic)
        rows.setdefault(row["id"], row)
    return sorted(rows.values(), key=lambda r: r["name"].lower())
