"""Device names from Home Assistant (optional): the devices in its registry that carry a MAC address, with the name
the user gave them there and their area, read with one call to its REST template API. Stdlib only."""

import json
import os
import ssl
import time
import urllib.error
import urllib.request

# Home Assistant renders this: its devices with a MAC (the device registry's connections), then device trackers
# with a MAC attribute (a router integration, the companion app) for MACs no device has
TEMPLATE = """{%- set ns = namespace(out=[], seen=[], macs=[]) -%}
{%- for s in states -%}
  {%- set d = device_id(s.entity_id) -%}
  {%- if d and d not in ns.seen -%}
    {%- set ns.seen = ns.seen + [d] -%}
    {%- for c in device_attr(d, 'connections') or [] -%}
      {%- if c[0] == 'mac' and (c[1] | lower) not in ns.macs -%}
        {%- set ns.macs = ns.macs + [c[1] | lower] -%}
        {%- set ns.out = ns.out + [{'mac': c[1] | lower, 'name': device_attr(d, 'name_by_user') or device_attr(d, 'name'), 'area': area_name(d)}] -%}
      {%- endif -%}
    {%- endfor -%}
  {%- endif -%}
{%- endfor -%}
{%- for s in states.device_tracker -%}
  {%- set m = (s.attributes.mac or '') | lower -%}
  {%- if m and m not in ns.macs -%}
    {%- set ns.macs = ns.macs + [m] -%}
    {%- set ns.out = ns.out + [{'mac': m, 'name': s.name, 'area': area_name(s.entity_id)}] -%}
  {%- endif -%}
{%- endfor -%}
{{ ns.out | tojson }}"""

REFRESH = 600           # seconds between fetches
TIMEOUT = 15


def _mac(m):
    """aa:bb:cc:dd:ee:ff from the forms Home Assistant stores (any case, - or : separated), else None."""
    digits = "".join(c for c in str(m or "").lower() if c in "0123456789abcdef")
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2)) if len(digits) == 12 else None


def parse(text):
    """{mac: {"name", "area"}} from the template's output; entries without a MAC or a name are left out."""
    out = {}
    for d in json.loads(text):
        mac, name = _mac(d.get("mac")), str(d.get("name") or "").strip()
        if mac and name:
            out.setdefault(mac, {"name": name[:100], "area": str(d.get("area") or "").strip()[:100]})
    return out


def fetch(url, token, verify=True, timeout=TIMEOUT):
    """The names Home Assistant has, or raise OSError / ValueError with what went wrong."""
    req = urllib.request.Request(url.rstrip("/") + "/api/template", data=json.dumps({"template": TEMPLATE}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx if url.startswith("https") else None) as r:
            return parse(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise OSError("the token was refused" if e.code == 401 else f"HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise OSError(str(e.reason)) from None


class Names:
    """The last names fetched, kept in `path` so a restart or a Home Assistant outage doesn't lose them. refresh() is
    called from a background thread; get() from loupd's loop (a dict swap, so no lock is needed)."""

    def __init__(self, path, url="", token="", verify=True):
        self.path, self.url, self.token, self.verify = path, url, token, verify
        self.state = {"names": {}, "fetched": 0, "error": None, "url": url}
        try:
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("url") == url:         # names from another Home Assistant don't apply
                self.state = saved
        except (OSError, ValueError):
            pass

    @property
    def enabled(self):
        return bool(self.url and self.token)

    def get(self, mac):
        return self.state["names"].get(mac) if self.enabled else None

    def refresh(self, now=None):
        now = now or time.time()
        try:
            state = {"names": fetch(self.url, self.token, self.verify), "fetched": now, "error": None, "url": self.url}
        except Exception as e:      # anything from the far end (a bad reply, odd JSON) mustn't end the fetching for good
            state = {**self.state, "error": str(e) or e.__class__.__name__, "tried": now}
        self.state = state
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, self.path)
        except OSError as e:
            return {**state, "error": f"can't save the names: {e}"}
        return state
