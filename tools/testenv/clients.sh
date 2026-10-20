#!/bin/sh
# On the host: create and set up the client containers on the trunk.
set -eu
d=$(dirname "$0")
# container  hostname  MAC (Apple, private, Intel, QEMU prefixes)  VLANs (0 = untagged)  privacy address lifetime (s)
CLIENTS="loupe-c1 den-laptop f0:ee:7a:00:00:c1 0 -
loupe-c2 kids-tablet 02:00:00:00:00:c2 20 3600
loupe-c3 office-pc e4:c7:67:00:00:c3 30 -
loupe-c4 media-box 52:54:00:00:00:c4 20,30 3600"
names=$(echo "$CLIENTS" | awk '{print $2}')
echo "$CLIENTS" | while read -r ct name mac vlans lft; do
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
	echo "PEERS=\"$(echo $names | tr ' ' '\n' | grep -vx "$name" | tr '\n' ' ')\"" | incus file push - "$ct/etc/default/bakwas"
	[ "$lft" = - ] && lft=
	incus exec "$ct" --env TEMP_LFT="$lft" -- sh /root/setup.sh "$name" $(echo "$vlans" | tr , ' ')
	exec 0<&3
done
