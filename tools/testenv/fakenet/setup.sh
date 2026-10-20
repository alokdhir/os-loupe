#!/bin/sh
# Inside the fakenet container: a made-up internet for the test router's WAN.
# Each site gets its own v4 and v6 address; nginx serves them over HTTP, HTTPS
# (HTTP/2) and HTTP/3; dnsmasq answers for them; fake-dl.py streams downloads.
set -eu
SITES="video cdn.video photos backup chat news games updates"

# addresses: .100 is DNS, sites from .101
n=101; addrs=""; dns=""
for s in $SITES; do
	addrs="$addrs
Address=10.98.0.$n/24
Address=2001:db8:98::$n/64"
	dns="$dns
address=/$s.fake/10.98.0.$n
address=/$s.fake/2001:db8:98::$n"
	n=$((n + 1))
done
cat > /etc/systemd/network/eth0.network <<EON
[Match]
Name=eth0

[Network]
Address=10.98.0.100/24
Address=2001:db8:98::100/64$addrs
Gateway=10.98.0.1
DNS=10.98.0.1
IPv6AcceptRA=no
LinkLocalAddressing=ipv6

[Route]
Destination=2001:db8:1::/48
Gateway=2001:db8:98::2
EON
networkctl reload; networkctl reconfigure eth0; sleep 3

export DEBIAN_FRONTEND=noninteractive
apt-get -qq update
apt-get -qq install -y nginx dnsmasq-base openssl python3 curl >/dev/null

cat > /etc/systemd/system/fake-dns.service <<EON
[Unit]
Description=DNS for the made-up .fake sites
After=network-online.target
[Service]
ExecStart=/usr/sbin/dnsmasq -k --conf-file=/etc/fake-dns.conf
[Install]
WantedBy=multi-user.target
EON
cat > /etc/fake-dns.conf <<EON
listen-address=10.98.0.100
bind-interfaces
no-resolv
no-hosts
local-ttl=300
log-facility=-$dns
EON

install -m 755 /root/fake-dl.py /usr/local/bin/fake-dl.py
cat > /etc/systemd/system/fake-dl.service <<EON
[Unit]
Description=Random-bytes download server
[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/fake-dl.py
DynamicUser=yes
[Install]
WantedBy=multi-user.target
EON

[ -f /etc/nginx/fake.crt ] || openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 3650 \
	-subj /CN=fake -addext "subjectAltName=DNS:*.fake,DNS:*.video.fake" \
	-keyout /etc/nginx/fake.key -out /etc/nginx/fake.crt 2>/dev/null
rm -f /etc/nginx/sites-enabled/default
cat > /etc/nginx/conf.d/fake.conf <<'EON'
server {
	listen 80 default_server;
	listen [::]:80 default_server;
	listen 443 ssl default_server;
	listen [::]:443 ssl default_server;
	listen 443 quic reuseport default_server;
	listen [::]:443 quic reuseport default_server;
	http2 on;
	http3 on;
	server_name _;
	ssl_certificate /etc/nginx/fake.crt;
	ssl_certificate_key /etc/nginx/fake.key;
	add_header Alt-Svc 'h3=":443"; ma=86400' always;
	access_log off;

	location /dl {
		proxy_pass http://127.0.0.1:8080;
		proxy_http_version 1.1;
		proxy_buffering off;
		proxy_read_timeout 1h;
	}
	location /ul {
		client_max_body_size 0;
		client_body_timeout 1h;
		proxy_pass http://127.0.0.1:8080;
		proxy_http_version 1.1;
		proxy_request_buffering off;
		proxy_read_timeout 1h;
	}
	location / {
		default_type text/plain;
		return 200 "$host\n";
	}
}
EON
install -m 755 /root/viewers.sh /usr/local/bin/viewers.sh
cat > /etc/systemd/system/viewers.service <<EON
[Unit]
Description=Remote viewers streaming from the bench's media server
After=network-online.target
[Service]
ExecStart=/usr/local/bin/viewers.sh
Restart=always
RestartSec=60
[Install]
WantedBy=multi-user.target
EON
systemctl daemon-reload
systemctl enable -q fake-dns fake-dl viewers
systemctl restart fake-dns fake-dl viewers
nginx -t 2>&1 | tail -1
systemctl restart nginx
echo "fakenet ready: $SITES"
