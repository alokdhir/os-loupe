#!/bin/sh
# Inside a client container: setup.sh HOSTNAME VLAN...  (0 = untagged LAN)
# Puts eth0 (or VLAN sub-interfaces of it) on DHCPv4 + SLAAC with privacy
# addresses, installs curl/dig/python3 and starts the traffic loop.
# TEMP_LFT=SECONDS rotates the privacy addresses that often (Linux: a day).
set -eu
name=$1; shift
echo "$name" > /etc/hostname; hostname "$name"

if [ -n "${TEMP_LFT:-}" ]; then
	printf 'net.ipv6.conf.%s.temp_prefered_lft = %s\nnet.ipv6.conf.%s.temp_valid_lft = %s\n' \
		all "$TEMP_LFT" all $((TEMP_LFT * 2)) default "$TEMP_LFT" default $((TEMP_LFT * 2)) > /etc/sysctl.d/90-tempaddr.conf
	sysctl -q -p /etc/sysctl.d/90-tempaddr.conf
fi
for l in $(ip -o link | awk -F'[ :@]+' '$2 ~ /^eth0\./ {print $2}'); do ip link del "$l"; done   # recreated below

rm -f /etc/systemd/network/*.network /etc/systemd/network/*.netdev
up() {  # networkd [Network] for an interface that takes addresses
	cat <<EON
DHCP=ipv4
IPv6AcceptRA=yes
IPv6PrivacyExtensions=yes
[DHCPv4]
SendHostname=yes
Hostname=$name
EON
}
if [ "$*" = 0 ]; then
	{ printf '[Match]\nName=eth0\n[Network]\n'; up; } > /etc/systemd/network/eth0.network
else
	{ printf '[Match]\nName=eth0\n[Network]\nLinkLocalAddressing=no\nIPv6AcceptRA=no\n'
	  for v in "$@"; do echo "VLAN=eth0.$v"; done; } > /etc/systemd/network/eth0.network
	for v in "$@"; do
		printf '[NetDev]\nName=eth0.%s\nKind=vlan\n[VLAN]\nId=%s\n' "$v" "$v" > /etc/systemd/network/eth0.$v.netdev
		{ printf '[Match]\nName=eth0.%s\n[Network]\n' "$v"; up; } > /etc/systemd/network/eth0.$v.network
	done
fi
systemctl restart systemd-networkd
for i in $(seq 30); do ip -4 route | grep -q default && break; sleep 1; done
ip -br addr | grep -v '^lo'

export DEBIAN_FRONTEND=noninteractive
apt-get -qq update
apt-get -qq install -y curl bind9-dnsutils python3 >/dev/null

install -m 755 /root/fake-dl.py /usr/local/bin/fake-dl.py
install -m 755 /root/bakwas.sh /usr/local/bin/bakwas.sh
cat > /etc/systemd/system/fake-dl.service <<EON
[Unit]
Description=Random-bytes server for traffic between VLANs
[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/fake-dl.py 0.0.0.0
DynamicUser=yes
[Install]
WantedBy=multi-user.target
EON
cat > /etc/systemd/system/bakwas.service <<EON
[Unit]
Description=Made-up traffic for the Loupe test bench
After=network-online.target
[Service]
ExecStart=/usr/local/bin/bakwas.sh
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
EON
systemctl daemon-reload
systemctl enable -q fake-dl bakwas
systemctl restart fake-dl
echo "$name ready (bakwas enabled, not started)"
