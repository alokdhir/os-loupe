#!/bin/sh
# Copyright (C) 2026 Alok K. Dhir
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
# OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

# Remove Loupe from the OPNsense box. Run as root:
#   sh tools/uninstall.sh
# Removes the files listed in /usr/local/etc/loupe.files (written by install.sh). The settings in the
# OPNsense configuration and the traffic data in /var/db/loupe are kept, so a reinstall picks up again.

set -e

PREFIX=/usr/local
MANIFEST=$PREFIX/etc/loupe.files

if [ "$(id -u)" != 0 ]; then
    echo "run as root (or with sudo)" >&2
    exit 1
fi
if [ ! -f "$MANIFEST" ]; then
    echo "Loupe is not installed (no $MANIFEST)" >&2
    exit 1
fi

[ -x $PREFIX/etc/rc.d/loupe ] && $PREFIX/etc/rc.d/loupe onestop >/dev/null 2>&1 || true

while read -r f; do
    rm -f "$PREFIX/$f"
done < "$MANIFEST"
sed 's#/[^/]*$##' "$MANIFEST" | sort -ru | while read -r d; do
    rmdir -p "$PREFIX/$d" 2>/dev/null || true          # directories left empty
done
rm -rf $PREFIX/opnsense/scripts/loupe
rm -f "$MANIFEST" /etc/rc.conf.d/loupe $PREFIX/etc/loupe.json

rm -f /var/lib/php/tmp/opnsense_menu_cache.xml /var/lib/php/tmp/opnsense_acl_cache.json
rm -f /var/lib/php/cache/*opnsense_loupe*
service configd restart >/dev/null

echo "Loupe removed. Traffic data is still in /var/db/loupe; delete it to start over."
