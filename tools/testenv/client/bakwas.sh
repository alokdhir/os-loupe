#!/bin/bash
# Made-up traffic, forever: small and big transfers to the .fake sites (v4/v6,
# HTTP/2 or HTTP/3), uploads, pulls from other clients across VLANs and, with
# INTERNET=1, small requests to real sites (v4). One log line per transfer in
# /var/log/bakwas.log.
# PEERS="name ..." in /etc/default/bakwas lists the other clients; ROLE=server only
# does a media server's upkeep (update checks, metadata) every 20-30 minutes.
# INTERNET=1 also visits real sites. Off by default: they leave through the host's
# own network, where they show up as the host's traffic.
PEERS= ROLE= INTERNET=
[ -f /etc/default/bakwas ] && . /etc/default/bakwas
LOG=/var/log/bakwas.log
# REAL sites are visited often; RARE ones (device makers, distro mirrors) now and then, like a person would
REAL=(www.google.com www.youtube.com www.netflix.com www.apple.com www.icloud.com www.amazon.com
	www.microsoft.com login.microsoftonline.com github.com api.github.com www.wikipedia.org www.reddit.com
	www.facebook.com www.instagram.com www.whatsapp.com www.spotify.com open.spotify.com www.cloudflare.com
	www.bbc.co.uk www.nytimes.com www.cnn.com www.dropbox.com zoom.us slack.com discord.com www.twitch.tv
	duckduckgo.com www.bing.com outlook.office.com www.linkedin.com www.hulu.com www.disneyplus.com
	www.weather.com)
RARE=(www.roku.com ring.com www.sonos.com www.ecobee.com www.tesla.com www.xbox.com www.nintendo.com
	www.playstation.com deb.debian.org www.speedtest.net)
H3=" www.google.com www.youtube.com www.facebook.com www.instagram.com www.cloudflare.com "
FAKE=(video cdn.video photos backup chat news games updates)

pick() { local a=("$@"); echo "${a[RANDOM % ${#a[@]}]}"; }
log() { echo "$(date +%FT%T) $*" >> "$LOG"; }
fam() { [ $((RANDOM % 2)) = 0 ] && echo -4 || echo -6; }
proto() { [ $((RANDOM % 3)) = 0 ] && echo --http3-only || echo --http2; }

xfer() {  # kind curl-args... ; logs what curl reports
	local kind=$1; shift
	local out
	out=$(curl -sk -o /dev/null --max-time 900 -w '%{http_version} %{remote_ip} down=%{size_download} up=%{size_upload}' "$@" 2>/dev/null)
	log "$kind rc=$? $out ${*: -1}"
}

if [ "$ROLE" = server ]; then
	while :; do
		for _ in $(seq $((RANDOM % 3 + 2))); do
			if [ -n "$INTERNET" ]; then
				xfer real -4 --max-time 20 "https://$(pick plex.tv www.themoviedb.org image.tmdb.org api.github.com)/"
			else
				xfer small $(fam) $(proto) "https://$(pick video cdn.video photos).fake/"
			fi
		done
		xfer small $(fam) $(proto) "https://updates.fake/"
		[ $((RANDOM % 6)) = 0 ] && xfer big $(fam) $(proto) --limit-rate 20M \
			"https://updates.fake/dl?bytes=$((RANDOM % 150 + 50))M"
		sleep $((RANDOM % 600 + 1200))
	done
fi

while :; do
	r=$((RANDOM % 100))
	if [ $r -lt 45 ] && [ -z "$INTERNET" ]; then
		xfer small $(fam) $(proto) "https://$(pick "${FAKE[@]}").fake/"
	elif [ $r -lt 45 ]; then
		[ $((RANDOM % 50)) = 0 ] && s=$(pick "${RARE[@]}") || s=$(pick "${REAL[@]}")
		case "$H3" in *" $s "*) p=--http3 ;; *) p= ;; esac
		xfer real -4 $p --max-time 20 "https://$s/"
	elif [ $r -lt 70 ]; then
		xfer small $(fam) $(proto) "https://$(pick "${FAKE[@]}").fake/"
	elif [ $r -lt 85 ]; then
		[ "$(jobs -r | wc -l)" -lt 2 ] && xfer big $(fam) $(proto) --limit-rate $((RANDOM % 30 + 5))M \
			"https://$(pick video cdn.video updates games).fake/dl?bytes=$((RANDOM % 400 + 20))M" &
	elif [ $r -lt 92 ]; then
		n=$((RANDOM % 100 + 5))
		head -c ${n}M /dev/urandom | xfer upload $(fam) $(proto) -H "Content-Length: $((n << 20))" \
			--limit-rate 10M -T - "https://$(pick backup photos).fake/ul"
	elif [ -n "$PEERS" ]; then
		peer=$(pick $PEERS)
		gw=$(ip -4 route | awk '/default/ {print $3; exit}')
		ip=$(dig +short -p 53053 @"$gw" "$peer.internal" | head -1)
		[ -n "$ip" ] && xfer peer --limit-rate 20M "http://$ip:8080/dl?bytes=$((RANDOM % 50 + 5))M"
	fi
	sleep $((RANDOM % 25 + 3))
done
