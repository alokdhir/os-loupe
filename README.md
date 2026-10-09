# os-loupe

An OPNsense plugin that shows which device on your LAN talked to which site, how much, and when.

- **Names, not just IPs:** reads the site name from each connection's first packet (TLS SNI, QUIC Initial) and from the router's own DNS answers.
- **Bytes per device per site:** from the NetFlow data OPNsense already collects (`/var/log/flowd.log`), with upload and download split correctly.
- **Device types:** MAC vendor, DHCP fingerprint, Bonjour model, traffic hints — or your own label.
- **Reporting → Loupe** pages and a dashboard widget.

Lightweight by design: a kernel filter passes only handshake, DNS, DHCP and mDNS packets to the daemon; byte counts come from NetFlow.

Status: early development.
