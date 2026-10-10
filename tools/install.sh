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

# Install or update Loupe on the OPNsense box itself, from a copy of this repository. Run as root:
#   sh tools/install.sh
# (tools/uninstall.sh removes it.)
#
# Files go where the package would put them (src/ -> /usr/local). The list of installed files is kept
# in /usr/local/etc/loupe.files, so an update removes files that are gone from the new version.

set -e

PREFIX=/usr/local
MANIFEST=$PREFIX/etc/loupe.files
SRC=$(cd "$(dirname "$0")/../src" && pwd)

if [ "$(id -u)" != 0 ]; then
    echo "run as root (or with sudo)" >&2
    exit 1
fi
if [ ! -d "$PREFIX/opnsense/mvc" ]; then
    echo "this is not an OPNsense system" >&2
    exit 1
fi

refresh_gui() {
    # menu, ACL and compiled templates are cached; configd reads its actions at start
    rm -f /var/lib/php/tmp/opnsense_menu_cache.xml /var/lib/php/tmp/opnsense_acl_cache.json
    rm -f /var/lib/php/cache/*opnsense_loupe*
    service configd restart >/dev/null
}

remove_listed() {
    # $1: file with paths relative to $PREFIX; then drop directories left empty
    [ -f "$1" ] || return 0
    while read -r f; do
        rm -f "$PREFIX/$f"
    done < "$1"
    sed 's#/[^/]*$##' "$1" | sort -ru | while read -r d; do
        rmdir -p "$PREFIX/$d" 2>/dev/null || true
    done
}

NEW=$(mktemp)
(cd "$SRC" && find . -type f ! -name .DS_Store ! -path '*/__pycache__/*' | sed 's#^\./##' | sort) > "$NEW"

# files from a previous version that this one no longer has
if [ -f "$MANIFEST" ]; then
    OLD=$(mktemp)
    sort "$MANIFEST" | comm -23 - "$NEW" > "$OLD"
    remove_listed "$OLD"
    rm -f "$OLD"
fi

while read -r f; do
    mkdir -p "$PREFIX/$(dirname "$f")"
    if [ -x "$SRC/$f" ]; then mode=755; else mode=644; fi
    install -o root -g wheel -m $mode "$SRC/$f" "$PREFIX/$f"
done < "$NEW"
install -o root -g wheel -m 644 "$NEW" "$MANIFEST"
rm -f "$NEW"
find $PREFIX/opnsense/scripts/loupe -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

refresh_gui
for i in 1 2 3 4 5 6 7 8 9 10; do        # configd takes a moment to come back
    configctl template reload OPNsense/Loupe >/dev/null 2>&1 && break
    sleep 1
done
if $PREFIX/etc/rc.d/loupe status >/dev/null 2>&1; then
    $PREFIX/etc/rc.d/loupe restart >/dev/null
    echo "Loupe updated and restarted."
else
    echo "Loupe installed. Enable it in Reporting -> Loupe -> Settings."
fi
