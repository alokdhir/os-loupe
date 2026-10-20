#!/bin/sh
# On the host: create and set up the client containers on the trunk: clients.sh [CONTAINER...] (default all).
set -eu
d=$(dirname "$0")
# container  hostname  MAC (Apple, private, Intel, QEMU prefixes)  VLANs (0 = untagged; the first has the default
# route)  privacy address lifetime (s)  role (client: browses and downloads; server: a Plex-like media server that
# remote viewers stream from through the router's port 32400, with an update check now and then)
CLIENTS="loupe-c1 den-laptop f0:ee:7a:00:00:c1 0 - client
loupe-c2 kids-tablet 02:00:00:00:00:c2 20 3600 client
loupe-c3 office-pc e4:c7:67:00:00:c3 30 - client
loupe-c4 plex-server 52:54:00:00:00:c4 30,20 - server"
names=$(echo "$CLIENTS" | awk '{print $2}')
only=" $* "
echo "$CLIENTS" | while read -r ct name mac vlans lft role; do
	[ $# -gt 0 ] && case "$only" in *" $ct "*) ;; *) continue ;; esac
	exec 3<&0 </dev/null  # incus reads stdin; keep the list away from it
	incus info "$ct" >/dev/null 2>&1 \
		|| incus init images:debian/13 "$ct" --no-profiles -s default -c limits.cpu=1 -c limits.memory=256MiB >/dev/null
	incus config device get "$ct" eth0 type >/dev/null 2>&1 \
		|| incus config device add "$ct" eth0 nic nictype=bridged parent=loupetrk0 hwaddr="$mac" >/dev/null
	incus start "$ct" 2>/dev/null || true
	sleep 3
	for f in client/setup.sh client/bakwas.sh fakenet/fake-dl.py; do
		incus file push "$d/$f" "$ct/root/$(basename "$f")"
	done
	printf 'ROLE=%s\nPEERS="%s"\n' "$role" "$(echo $names | tr ' ' '\n' | grep -vx "$name" | tr '\n' ' ')" \
		| incus file push - "$ct/etc/default/bakwas"
	[ "$lft" = - ] && lft=
	incus exec "$ct" --env TEMP_LFT="$lft" --env ROLE="$role" -- sh /root/setup.sh "$name" $(echo "$vlans" | tr , ' ')
	exec 0<&3
done
