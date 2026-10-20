#!/bin/sh
# Copy the test bench scripts to the host: push.sh root@host [CHECK_VMS]
set -eu
host=$1; keep=${2:-}
dir=$(dirname "$0")
${SSH:-ssh} "$host" 'mkdir -p /root/loupe-testenv'
COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -C "$dir" -cf - . | ${SSH:-ssh} "$host" 'tar -C /root/loupe-testenv -xf -'
${SSH:-ssh} "$host" "printf '#!/bin/sh\nCHECK_VMS=\"$keep\" exec /root/loupe-testenv/teardown.sh\n' > /root/loupe-testenv/undo.sh; chmod +x /root/loupe-testenv/undo.sh /root/loupe-testenv/*.sh"
