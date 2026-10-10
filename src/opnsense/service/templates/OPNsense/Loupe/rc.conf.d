{% if not helpers.empty('OPNsense.Loupe.general.enabled') and not helpers.empty('OPNsense.Loupe.general.interfaces') %}
loupe_enable="YES"
{% else %}
loupe_enable="NO"
{% endif %}
