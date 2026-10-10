#!/bin/sh
# Dev install: copy src/ into the router's /usr/local (as the package would) and reload.
#   tools/deploy.sh            install / update
#   tools/deploy.sh uninstall  stop the service and remove every installed file (data in /var/db/loupe is kept)
set -e
cd "$(dirname "$0")/.."
ROUTER=${ROUTER:-api-user@192.168.0.1}
SSH="ssh -i $HOME/.ssh/opnsense_ed25519 -o IdentitiesOnly=yes $ROUTER"
FILES=$(cd src && find . -type f ! -name '.DS_Store' ! -path '*/__pycache__/*' | sed 's#^\./##' | sort)

if [ "$1" = "uninstall" ]; then
    $SSH "sudo -n /usr/local/etc/rc.d/loupe onestop 2>/dev/null; cd /usr/local && sudo -n rm -f $(echo $FILES | tr '\n' ' ') \
        /etc/rc.conf.d/loupe /usr/local/etc/loupe.json && sudo -n rm -rf /usr/local/opnsense/scripts/loupe \
        /usr/local/opnsense/mvc/app/models/OPNsense/Loupe /usr/local/opnsense/mvc/app/controllers/OPNsense/Loupe \
        /usr/local/opnsense/mvc/app/views/OPNsense/Loupe /usr/local/opnsense/service/templates/OPNsense/Loupe; \
        sudo -n rm -f /var/lib/php/tmp/opnsense_menu_cache.xml /var/lib/php/tmp/opnsense_acl_cache.json; \
        sudo -n service configd restart >/dev/null; echo uninstalled"
    exit 0
fi

COPYFILE_DISABLE=1 tar -C src --exclude .DS_Store --exclude __pycache__ -cf - . | $SSH "set -e; T=\$(mktemp -d); tar -C \$T -xf -; \
    cd \$T && find . -type f | while read f; do sudo -n install -D -o root -g wheel -m \$( [ -x \"\$f\" ] && echo 755 || echo 644 ) \"\$f\" \"/usr/local/\$f\" 2>/dev/null \
        || { sudo -n mkdir -p \"/usr/local/\$(dirname \"\$f\")\"; sudo -n install -o root -g wheel -m \$( [ -x \"\$f\" ] && echo 755 || echo 644 ) \"\$f\" \"/usr/local/\$f\"; }; done; \
    rm -rf \$T; sudo -n chmod 755 /usr/local/opnsense/scripts/loupe/*.py /usr/local/etc/rc.d/loupe; \
    sudo -n rm -f /var/lib/php/tmp/opnsense_menu_cache.xml /var/lib/php/tmp/opnsense_acl_cache.json; \
    sudo -n service configd restart >/dev/null; echo installed"
