#!/bin/bash
# On fakenet: remote viewers streaming from the media server through the test
# router's port forward (WAN 32400), one at a time with gaps, 1-4 GB at 1-4 MB/s.
# Logged like bakwas.sh, in /var/log/viewers.log.
LOG=/var/log/viewers.log
while :; do
	out=$(curl -s -o /dev/null --max-time 7200 --limit-rate $((RANDOM % 4 + 1))M \
		-w '%{http_version} %{remote_ip} down=%{size_download} up=%{size_upload}' \
		"http://10.98.0.2:32400/dl?bytes=$((RANDOM % 3000 + 1000))M" 2>/dev/null)
	echo "$(date +%FT%T) viewer rc=$? $out" >> "$LOG"
	sleep $((RANDOM % 1800 + 300))
done
