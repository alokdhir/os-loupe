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

# Development: install this working tree on a router over SSH (runs tools/install.sh there).
#   ROUTER=admin@192.168.1.1 tools/deploy.sh              install or update
#   ROUTER=admin@192.168.1.1 tools/deploy.sh uninstall    remove
# Runs the installer with sudo (it asks for the password as usual) unless ROUTER is root@.
# Extra ssh options: LOUPE_SSH="ssh -i ~/.ssh/key".

set -e
cd "$(dirname "$0")/.."
: "${ROUTER:?set ROUTER=user@router}"
SSH=${LOUPE_SSH:-ssh}
case "$ROUTER" in root@*) SUDO= ;; *) SUDO=sudo ;; esac
[ -t 0 ] && TTY=-t || TTY=

DIR=$($SSH "$ROUTER" mktemp -d /tmp/loupe.XXXXXX)
COPYFILE_DISABLE=1 tar --exclude .DS_Store --exclude __pycache__ -cf - src tools/install.sh | $SSH "$ROUTER" tar -C "$DIR" -xf -
$SSH $TTY "$ROUTER" "$SUDO sh $DIR/tools/install.sh $1; rc=\$?; rm -rf $DIR; exit \$rc"
