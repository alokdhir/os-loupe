# Loupe for OPNsense

Which device on your network talked to which site or service, how much data moved each way, and when — inside the OPNsense GUI.

Loupe answers questions like *"what was the PlayStation doing at 2 a.m.?"*, *"who's using all the bandwidth?"*, *"which devices talk to this address?"* and *"what is this unknown thing on my Wi-Fi?"* — with device names and types, site and service names, and exact upload/download bytes, kept for 30 days at 5-minute detail and a year hourly.

## What you get

- **Reporting → Loupe, Devices tab**: every device with its name, type (e.g. *MacBook Air 15″ (M3)*, *Apple TV 4K*, *PlayStation*, *Smart plug*), vendor, bytes down and up, and top services, for the last hour, day, week, month or year.
- **Device detail**: a traffic chart over time, and what it talked to — by service (*YouTube*, *Netflix*, *iCloud Private Relay*, *VPN (WireGuard)*…), by site name, and by protocol/port.
- **Services**: every service the house used in the period, with the devices that used it; search a service, site or address (`YouTube`, `playstation.net`, `203.0.113.7`) to see which devices used it, how much, first and last seen — including names that were only looked up in DNS.
- **Your own names**: rename any device or correct its type from the report pages; it follows the device by MAC address.
- **Dashboard widget**: the last 24 hours at a glance — totals, an hourly chart and the top devices.

## How it works

Loupe runs as a small service on the router and combines three sources:

1. **Names.** A kernel BPF filter passes only a handful of packet types to the service: the first packet of each TLS connection (the *ClientHello*, which carries the site name in plain text — SNI), QUIC/HTTP-3 *Initial* packets (decrypted with the public keys the protocol defines, RFC 9001/9369), DNS answers from the router to each device, DHCP requests, and mDNS/Bonjour announcements. Bulk traffic never reaches userland.
2. **Bytes.** Every connection through the router has a pf state with exact byte counters for both directions, recorded on the LAN side *before* NAT. Loupe reads the state table every 10 seconds and records the difference per device, server and port. pf keeps a closed connection around for at least 30–45 seconds, so every connection's final count is seen.
3. **Devices.** The MAC vendor (IEEE registry), the DHCP request (hostname, vendor class, parameter list), Bonjour announcements (Apple model identifiers like `Mac15,13`, AirPlay/Cast/printer services), the device's own DHCP lease name, and the services it talks to (`playstation.net` → PlayStation).

Each connection gets the name from its own handshake first, then from the DNS answer that device received for that address, then from any device's DNS answer. Service names come from a built-in map of ~300 domains (plus address-range and port fallbacks for nameless traffic) that you can extend in the settings; they are applied when reports are built, so changes cover all history.

Data lives in SQLite at `/var/db/loupe/loupe.db`. The design, the alternatives considered and the known limits are in [docs/DESIGN.md](docs/DESIGN.md).

### Why not just NetFlow / Insight?

OPNsense's NetFlow on a bridged LAN misses LAN→internet packets on ingress — uploads only show up after NAT, with the router's public address — so per-device upload totals in Insight are wrong. NetFlow also exports long connections only every 30 minutes and has no site names. Loupe's pf counters are exact in both directions and per device.

### Compared with ntopng and Zenarmor

Those are broader tools — deep packet inspection, alerts, policies, live flows — and they're good at it. Loupe does one narrower job: per-device history by site and service, with good device identification, built into OPNsense's own Reporting menu, free, and light enough to leave running on a small box.

## Device rules

How Loupe recognises devices is data, not code: [`data/devices.json`](src/opnsense/scripts/loupe/data/devices.json) (device rules, the icon and maker for each type, Apple model names) and [`data/services.json`](src/opnsense/scripts/loupe/data/services.json) (site and service names, address ranges, ports, VPN providers). One entry per line:

```json
{"when": {"hostname": "(?i)^kvm-[0-9a-f]{4}$"}, "type": "NanoKVM", "example": {"hostname": "kvm-0a1b"}}
```

A rule matches when all of its conditions do (`when`, or several in `all`); a list value means *any of*. Fields:

| Field | Matches |
|---|---|
| `mac_prefix` | start of the MAC address |
| `mdns_model` | a model the device announces over Bonjour (`model=`, `am=`, `md=`…), regex |
| `hostname` | its DHCP or Bonjour host name, regex |
| `mdns_service` | a Bonjour service it offers, exact (`_googlecast._tcp`) |
| `dhcp_vendor` | DHCP vendor class (option 60), regex |
| `dhcp_params` | start of the DHCP parameter request list (`1,121,3,6`) |
| `talks_to` | a domain it looked up or connected to, and its subdomains |
| `vendor` | a whole word in the maker's name for the MAC prefix |
| `private_mac` | `true` for a randomized (private) MAC |

Evidence is trusted in a fixed order — Bonjour model, host name, hosting role, strong Bonjour services, DHCP vendor, DHCP fingerprint, domains it talks to (after the fingerprint: a phone running a smart-home app talks to the same cloud as the device), other Bonjour services, MAC maker — and the first matching rule wins (file order within a tier). Every type needs an entry in `types` with a [Font Awesome](https://fontawesome.com/icons) icon (the set OPNsense ships), and a `vendor` when only one maker makes it.

**Adding a rule:** add it with an `example` — made-up evidence that should come out as your type (no real MACs or names) — then run the tests. They fail if the example lands on another type, or if your rule takes over another rule's example. `tools/fmtjson.py` keeps the files one entry per line.

## Footprint

On a 5 Gbps home connection (Intel N150): about 0.5% of one CPU core idle, ~4% during a full-speed transfer, and 30–50 MB of memory.

## Privacy

Loupe records which device talked to which site name and how much — not page contents, URLs or anything inside encrypted connections. Encrypted Client Hello (ECH), VPNs and relays (iCloud Private Relay) hide the real site; Loupe shows those as what they are. It's a tool for the network's owner; use it with the people on your network in mind.

## Install (development)

Requires OPNsense 26.x (Python 3 with `cryptography` and `sqlite3`, both present by default).

```sh
ROUTER=user@192.168.1.1 tools/deploy.sh            # copy src/ into /usr/local, reload configd and the menu
ROUTER=user@192.168.1.1 tools/deploy.sh uninstall  # remove it again (data in /var/db/loupe is kept)
```

The user needs passwordless `sudo` on the router. Then open **Reporting → Loupe → Settings ▾ → General**, tick *Enable*, choose the LAN interface(s) and press *Apply*.

The layout follows a plugin directory in [opnsense/plugins](https://github.com/opnsense/plugins) (`Makefile`, `pkg-descr`, `src/` installed under `/usr/local`), so it can be built as a package from that tree.

## Tests

```sh
cd tests && python3 -m unittest test_tls test_quic test_pfstate test_store test_devid test_services test_rules
```

All test data is synthetic.

## License

BSD 2-Clause — see [LICENSE](LICENSE).
