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

# Build os-loupe and os-eero packages on an OPNsense box and publish them to the package repository
# served by GitHub Pages from this repository's gh-pages branch (https://alokdhir.github.io/os-loupe/).
#
#   LOUPE_SSH="ssh -i key" ROUTER=user@router tools/publish.sh
#
# Packages are built from what is committed (git archive HEAD of each repository), with OPNsense's own plugin
# tooling (a checkout of opnsense/plugins, PLUGINS_DIR) on the OPNsense box, so their dependencies are the
# versions OPNsense ships. They hold no compiled code: one package serves every architecture.
# A changed plugin needs a new PLUGIN_VERSION (or PLUGIN_REVISION) in its Makefile, or pkg won't upgrade to it.

set -e
cd "$(dirname "$0")/.."
LOUPE=$(pwd)
: "${ROUTER:?set ROUTER=user@router (an OPNsense box to build on)}"
SSH=${LOUPE_SSH:-ssh}
EERO=${EERO_DIR:-$LOUPE/../os-eero}
PLUGINS=${PLUGINS_DIR:-$LOUPE/../opnsense-plugins}
PLUGIN_DIRS="$LOUPE:loupe $EERO:eero"

for p in $PLUGIN_DIRS; do
    dir=${p%:*}
    if [ -n "$(git -C "$dir" status --porcelain)" ]; then
        echo "$dir has uncommitted changes: packages are built from what is committed" >&2
        exit 1
    fi
done
[ -d "$PLUGINS/Mk" ] || { echo "no opnsense/plugins checkout at $PLUGINS (set PLUGINS_DIR)" >&2; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"; git -C "$LOUPE" worktree remove --force "$WORK/pages" 2>/dev/null || :' EXIT

# the build tree: opnsense/plugins tooling plus each plugin under net-mgmt/
mkdir -p "$WORK/plugins/net-mgmt"
git -C "$PLUGINS" archive HEAD Mk Scripts Templates | tar -xf - -C "$WORK/plugins"
BUILD=""
for p in $PLUGIN_DIRS; do
    dir=${p%:*} name=${p#*:}
    mkdir -p "$WORK/plugins/net-mgmt/$name"
    git -C "$dir" archive HEAD | tar -xf - -C "$WORK/plugins/net-mgmt/$name"
    version=$(sed -n 's/^PLUGIN_VERSION=[[:space:]]*//p' "$dir/Makefile")
    commit="$version 0 $(git -C "$dir" rev-parse --short HEAD)"
    # PLUGIN_DEVEL empty: a release package (os-NAME, not os-NAME-devel); PLUGIN_NO_ABI: any FreeBSD ABI/arch
    BUILD="$BUILD (cd plugins/net-mgmt/$name && make package PLUGIN_DEVEL= PLUGIN_NO_ABI=yes PLUGIN_COMMIT='$commit' >/dev/null) &&"
done

REMOTE=$($SSH "$ROUTER" mktemp -d /tmp/publish.XXXXXX)
# a version already published must not change under the same name (pkg would never offer it): an unchanged
# plugin keeps its published package (a rebuild differs only in timestamps); a changed one needs a new version
cat > "$WORK/plugins/samever.sh" <<'CHECK'
set -e
for pkg in repo/All/*.pkg; do
    name=$(basename "$pkg")
    fetch -qo "old-$name" "https://alokdhir.github.io/os-loupe/repo/All/$name" 2>/dev/null || continue
    if [ "$(tar -xOf "$pkg" --exclude '+*' | sha256)" != "$(tar -xOf "old-$name" --exclude '+*' | sha256)" ]; then
        echo "$name is already published with different contents: bump PLUGIN_VERSION" >&2
        exit 1
    fi
    mv "old-$name" "$pkg"
done
CHECK
COPYFILE_DISABLE=1 tar -czf - -C "$WORK" plugins | $SSH "$ROUTER" "tar -xzf - -C $REMOTE"
$SSH "$ROUTER" "cd $REMOTE && $BUILD mkdir -p repo/All && cp plugins/net-mgmt/*/work/pkg/*.pkg repo/All/ && sh plugins/samever.sh && cd repo && pkg repo . >/dev/null" || { $SSH "$ROUTER" "rm -rf $REMOTE"; exit 1; }
$SSH "$ROUTER" "tar -czf - -C $REMOTE repo; rm -rf $REMOTE" | tar -xzf - -C "$WORK"

git -C "$LOUPE" fetch -q origin gh-pages
git -C "$LOUPE" worktree add -q "$WORK/pages" origin/gh-pages
rm -rf "$WORK/pages/repo"
cp -R "$WORK/repo" "$WORK/pages/repo"
cd "$WORK/pages"
git add -A repo
if git diff --cached --quiet -- repo/All; then
    git reset -q
    echo "nothing new to publish"
    exit 0
fi
git commit -qm "Package repository: $(cd repo/All && ls *.pkg | sed 's/\.pkg$//' | tr '\n' ' ')"
git push -q origin HEAD:gh-pages
echo "published: $(cd repo/All && ls)"
