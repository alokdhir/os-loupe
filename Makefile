PLUGIN_NAME=		loupe
PLUGIN_VERSION=		0.9
PLUGIN_DEPENDS=		py${PLUGIN_PYTHON}-cryptography py${PLUGIN_PYTHON}-sqlite3
PLUGIN_COMMENT=		Per-device traffic: which device talked to which site, how much, when
PLUGIN_MAINTAINER=	alok@dhir.net
PLUGIN_WWW=		https://github.com/alokdhir/os-loupe

.include "../../Mk/plugins.mk"
