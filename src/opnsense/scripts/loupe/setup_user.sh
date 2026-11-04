#!/bin/sh

# The user Loupe runs as, and its data. Run as root: by the rc script at each start and by the installers.
# The uid is fixed (below 2000, which OPNsense leaves alone) so files keep their owner across a reinstall.

pw usershow loupe >/dev/null 2>&1 || \
	pw useradd loupe -u 1787 -d /nonexistent -s /usr/sbin/nologin -c "Loupe traffic monitor" || exit 1
mkdir -p /var/db/loupe
chown -R loupe:loupe /var/db/loupe
chmod 750 /var/db/loupe
