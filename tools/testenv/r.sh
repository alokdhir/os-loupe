#!/bin/sh
# Run a command on the test router from the host: r.sh CMD..., or r.sh < script (sh)
d=$(dirname "$0")
[ $# -gt 0 ] || set -- /bin/sh -s
exec ssh -i "$d/key" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o BatchMode=yes -o ConnectTimeout=10 root@${ROUTER:-10.98.0.2} "$@"
