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

"""The AI Rule Generator: a table of the devices Loupe can't tell apart and a prompt for any AI chat, then the rules
the AI answers with, checked against every device before the user saves any.

Nothing here talks to an AI: the user copies the prompt into the chat of their choice and pastes the reply back.
The table carries evidence only - no MACs or addresses, names people gave things stripped of possessives - under
row ids (D1, D2...) that map back to devices on this router only.
"""
import hashlib
import json
import re

from . import devid

# rule fields the AI may use (and the GUI can store): see the prompt
FIELDS = ("vendor", "hostname", "mdns_name", "mdns_model", "mdns_service", "category", "dhcp_vendor", "talks_to")
REGEX = ("hostname", "mdns_name", "mdns_model", "dhcp_vendor")
# domains that say nothing about what a device is: platforms, CDNs, clouds, time
COMMON_DOMAINS = ("google.com", "googleapis.com", "gstatic.com", "googleusercontent.com", "google-analytics.com",
                  "doubleclick.net", "app-measurement.com", "crashlytics.com", "firebaseio.com", "apple.com",
                  "icloud.com", "mzstatic.com", "akamai.net", "akamaiedge.net", "akamaihd.net", "edgekey.net",
                  "edgesuite.net", "cloudflare.com", "cloudflare-dns.com", "cloudfront.net", "fastly.net",
                  "amazonaws.com", "amazontrust.com", "azure.com", "azure.net", "windows.net", "microsoft.com",
                  "msftconnecttest.com", "ntp.org", "nist.gov", "arpa", "local")
MAX_ROWS = 200        # a question an AI chat still takes (~50 KB); a bigger network asks in parts


def _registered(name):
    """The registered domain of a host name: x.y.example.com -> example.com, a.b.example.co.uk -> example.co.uk."""
    parts = name.rstrip(".").lower().split(".")
    if len(parts) > 2 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "net", "org", "ac", "gov"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def scrub(text):
    """Names people gave things can carry a name: "Sam's iPhone" -> "[name]'s iPhone"."""
    return re.sub(r"(?i)\b[a-z]+[’']s\b", "[name]'s", text or "")


def needs_help(dtype, table=None):
    """A device without a type, or with one that names only a platform or maker (data/devices.json: generic)."""
    t = table or devid.rules()
    return not dtype or bool(t.types.get(dtype, {}).get("generic"))


def evidence(dev):
    """What the table shows for a device. `dev`: {"vendor", "type", "type_source", "model", "hostname",
    "info" (current_mdns), "names" (sites it used)}."""
    info = dev.get("info") or {}
    hosts = [h.removesuffix(".local") for h in info.get("mdns_hosts", []) if h.endswith(".local")
             and not devid._id_item("hosts", h)]
    inst = [n for n in info.get("mdns_names", []) if not devid._id_item("names", n)]
    if len(hosts) > devid.RELAYS_OVER:          # it relays other devices' records: they say nothing about it
        hosts, inst = [], []
    domains = {}
    for n in dev.get("names") or ():
        d = _registered(n)
        if not any(d == c or d.endswith("." + c) for c in COMMON_DOMAINS):
            domains[d] = domains.get(d, 0) + 1
    t = dev.get("type") or ""
    return {
        "vendor": dev.get("vendor") or ("(private MAC)" if dev.get("private") else ""),
        "current": f"{t} ({dev.get('type_source')})" if t else "",
        "model": dev.get("model") or "",
        "hostname": scrub(dev.get("hostname") or ""),
        "bonjour_names": sorted({scrub(n) for n in inst})[:4],
        "bonjour_hosts": sorted({scrub(h) for h in hosts})[:3],
        "bonjour_models": sorted(info.get("mdns_models", []))[:4],
        "bonjour_services": sorted(info.get("mdns_services", []))[:8],
        "categories": sorted(devid.categories(info)),
        "dhcp_vendor": info.get("dhcp_vendor_class") or "",
        "dhcp_params": devid._params(info),
        "domains": [d for d, _ in sorted(domains.items(), key=lambda kv: (-kv[1], kv[0]))[:8]],
    }


COLUMNS = ("vendor", "current", "model", "hostname", "bonjour_names", "bonjour_hosts", "bonjour_models",
           "bonjour_services", "categories", "dhcp_vendor", "dhcp_params", "domains")


# what a row's id is made of: the evidence, but not the device's current type, which a rule saved between copying
# the prompt and pasting the answer changes (domains stay in: for a bare Wi-Fi module they are all that tells it apart)
IDENTITY = tuple(c for c in COLUMNS if c != "current")


def row_id(ev):
    """A row's id: a fingerprint of the device's identity evidence, the same however the list has changed between
    copying the prompt and pasting the answer (a position would point at another device by then)."""
    key = [sorted(ev.get(c) or []) if c == "domains" else ev.get(c) for c in IDENTITY]
    return "D" + hashlib.sha1(json.dumps(key).encode()).hexdigest()[:6].upper()


def rows(devices):
    """Devices with the same evidence share a row: [(id, [macs], evidence)], in the order given; at most MAX_ROWS
    (the caller can tell from the count)."""
    out, seen = [], {}
    for mac, ev in devices:
        rid = row_id(ev)
        if rid in seen:
            seen[rid][1].append(mac)
            continue
        seen[rid] = (rid, [mac], ev)
        out.append(seen[rid])
    return out[:MAX_ROWS]


def prompt(table_rows, types):
    """The text the user pastes into an AI chat: the job, the evidence columns, the rule format, the type names to
    reuse and the answer format (a made-up example), then the table."""
    cell = lambda v: (", ".join(v) if isinstance(v, list) else str(v)).replace("|", "/") or "-"     # noqa: E731
    lines = [f"| {rid} | {len(macs)} | " + " | ".join(cell(ev[c]) for c in COLUMNS) + " |" for rid, macs, ev in table_rows]
    return f"""{PROMPT_START}, a home-network monitor on an OPNsense router, identify devices it could not identify, or only identified generically (from the maker of their network chip). Each row below is one device, or a group of identical devices ("count"). Use web search where it helps: model numbers, cloud domains, Bonjour service names, MAC vendors.

For each row, work out what the device is: its category (what kind of thing it is, e.g. "Garage door opener", "Smart scale", "Oven"), its brand, and its model if the evidence shows one. Then write RULES that recognise this device and others like it in the future.

## Evidence columns
- vendor: maker of the device's network chip, from its MAC address. Often a Wi-Fi module maker (Espressif, Texas Instruments, Telit), not the product's brand.
- current: Loupe's current type and the evidence it used (blank, or too generic to be useful)
- model: the model the device announces, if any
- hostname: the host name it sends in DHCP
- bonjour_names: names it announces Bonjour (mDNS) services under; often brand and model
- bonjour_hosts: its Bonjour host names
- bonjour_models: Bonjour TXT model keys (key=value)
- bonjour_services: Bonjour service types it announces
- categories: standard category codes it announces: hap:N is a HomeKit accessory category, matter:0xNNNN a Matter device type
- dhcp_vendor: DHCP vendor class (option 60)
- dhcp_params: DHCP parameter request list (option 55)
- domains: internet domains it contacted (generic ones such as Google, Apple, CDNs and NTP removed)

## Rule format
A rule matches when every condition in "when" matches; a list value means any of. Fields:
- "vendor": a word or words of the MAC vendor (whole words, case-insensitive)
- "hostname": regex on the DHCP host name (case-insensitive)
- "mdns_name": regex on a Bonjour name (lower case)
- "mdns_model": regex on a Bonjour TXT model value
- "mdns_service": a Bonjour service type, exactly
- "category": a category code, exactly, e.g. "hap:9"
- "dhcp_vendor": regex on the DHCP vendor class
- "talks_to": a domain the device contacts, and its subdomains

Rules must be specific to the product:
- never a rule on a network-chip maker alone ("Espressif", "Texas Instruments", "Telit"), and never on a generic or shared cloud domain (azure-devices.net, amazonaws.com);
- prefer the strongest evidence: model / Bonjour name > hostname > Bonjour service or category > a product-specific domain > vendor;
- combine conditions when one alone could match unrelated devices (e.g. vendor "Espressif" AND talks_to "acme-cloud.example"): a computer or home server using a product's cloud domain must not match.

## Types
Use one of these existing type names ONLY if it describes the device exactly; otherwise give the specific category in plain words (singular, first word capitalised, e.g. "Garage door opener"). Don't fall back to a vague existing type such as "Smart home device" or "Embedded device" when you know what the device is.
{", ".join(sorted(types))}

## Response format
Reply with ONE JSON object in a single ```json code block. No comments inside the JSON. Example (made up):
```json
{{
  "rules": [
    {{
      "devices": ["D0A1B2C"],
      "when": {{"vendor": "Espressif", "talks_to": "acme-cloud.example"}},
      "type": "Pool controller",
      "vendor": "Acme",
      "model": "PC-200",
      "confidence": "high",
      "note": "one sentence: what the evidence shows"
    }}
  ],
  "unknown": [{{"devices": ["D3D4E5F"], "note": "what is missing to identify it"}}]
}}
```
- "model" only if the evidence shows it; otherwise leave it out. "confidence": high, medium or low.
- "devices" lists row ids exactly as they appear in the table; every row appears in a rule's "devices" or in "unknown".
- Don't invent evidence that isn't in the table.

## Devices
| id | count | {" | ".join(COLUMNS)} |
|{"---|" * (len(COLUMNS) + 2)}
""" + "\n".join(lines) + "\n"


NO_RULES = "No rules in what was pasted. Paste the AI's whole answer: the rules come as a JSON block."
PROMPT_START = "You are helping Loupe"      # how the prompt begins: pasted back by mistake, it isn't an answer


# ---- the reply ----------------------------------------------------------------------------------------------------
_ESCAPE = re.compile(r'\\(.)', re.S)       # a backslash and the character after it, pair by pair


def _escapes(text):
    """Backslashes JSON doesn't allow, as AIs write them in regexes: "\\$" is a shell habit for the end anchor (drop
    the backslash); "\\d", "\\." are regex escapes (keep the backslash)."""
    def fix(m):
        c = m.group(1)
        return m.group(0) if c in '\\"/bfnrtu' else c if c == "$" else "\\\\" + c
    return _ESCAPE.sub(fix, text)


def _loads(text):
    try:
        return json.loads(text, strict=False)       # not strict: a tab or such inside a string is kept
    except ValueError:
        return json.loads(_escapes(text), strict=False)     # "\$" in a regex: AIs write it; JSON doesn't allow it


def _objects(text, key):
    """The complete objects of the array `key` in a reply that may be cut off or broken further on."""
    fixed = _escapes(text)
    m = re.search(r'"' + key + r'"\s*:\s*\[', fixed)
    if not m:
        return []
    dec, pos, out = json.JSONDecoder(strict=False), m.end(), []
    while True:
        while pos < len(fixed) and fixed[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(fixed) or fixed[pos] != "{":
            return out
        try:
            obj, pos = dec.raw_decode(fixed, pos)
        except ValueError:
            return out
        out.append(obj)


def parse(text):
    """(rules, unknown, problem) from whatever was pasted: the JSON in a ```json block, or the outermost {...};
    invalid escapes tolerated; a reply cut off keeps its complete rules. problem is None, or what went wrong."""
    # control characters other than line breaks and tabs: copied along from some chat pages, never meant
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text or "")
    if PROMPT_START in text and "## Response format" in text:     # its example would read as a rule
        return [], [], "That's the prompt. Paste it into the AI chat, then paste the AI's answer here."
    block = re.search(r"```(?:json)?\s*(\{.*?)```", text, re.S)
    body = block.group(1) if block else text[text.find("{"):] if "{" in text else ""
    if not body:
        return [], [], NO_RULES
    try:
        data = _loads(body[:body.rfind("}") + 1] if block is None else body)
        if not isinstance(data, dict):
            raise ValueError
        problem = None
    except ValueError:
        data = {"rules": _objects(body, "rules"), "unknown": _objects(body, "unknown")}
        kept = len(data["rules"])
        depth = body.count("{") - body.count("}")       # still open at the end: the answer stops partway
        problem = (f"The answer stops partway, in the middle of a rule: kept the {kept} complete rules. Copy it again "
                   "once the AI has finished writing, or ask it to continue and paste the rest." if depth > 0 else
                   f"The answer isn't valid JSON: kept the {kept} rules that could be read.")
    listed = data.get("rules") if isinstance(data.get("rules"), list) else []
    # a rule is an object with conditions ("when", an object) and a type; anything else in the list is skipped
    rules = [r for r in listed if isinstance(r, dict) and isinstance(r.get("when"), dict) and r["when"]
             and isinstance(r.get("type"), str) and r["type"].strip()]
    unknown = [u for u in data.get("unknown") or [] if isinstance(u, dict)] if isinstance(data.get("unknown"), list) else []
    if not rules:
        return [], [], NO_RULES
    if len(rules) < len(listed):
        skipped = f"Skipped {len(listed) - len(rules)} entries that aren't rules (no conditions or no type)."
        problem = f"{problem} {skipped}" if problem else skipped
    return rules, unknown, problem


MODELS_START = "You are helping Loupe name product models"


def models_prompt(codes):
    """The prompt for naming model codes: [(code, maker, device type, how many devices)] - nothing else about them."""
    lines = [f"| {code} | {maker or '-'} | {dtype or '-'} | {n} |".replace("\n", " ") for code, maker, dtype, n in codes]
    return f"""{MODELS_START}. Devices on a home network announce these model codes: internal identifiers (like Apple's Mac16,1), part numbers or SKUs. For each, give the name people know the product by, with its generation or year where that tells it apart (e.g. "Apple TV 4K (3rd generation)", "MacBook Air (15-inch, M3, 2024)").

Check each code against a reliable source (the maker's support pages, a model identifier list); leave out codes you can't confirm, and codes that are already a readable product name.

## Response format
Reply with ONE JSON object in a single ```json code block, nothing inside it but this shape (made-up example):
```json
{{"models": [{{"identifier": "ACME9,1", "model": "Acme Box (2nd generation)", "confidence": "high"}}]}}
```
"identifier" exactly as listed; "confidence": high, medium or low.

## Codes
| code | maker | device type | devices |
|---|---|---|---|
""" + "\n".join(lines) + "\n"


def parse_model_names(text):
    """(names, problem) from a pasted answer to the model-name prompt: [(identifier, name, confidence)]."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text or "")
    if MODELS_START in text and "## Codes" in text:
        return [], "That's the prompt. Paste it into the AI chat, then paste the AI's answer here."
    block = re.search(r"```(?:json)?\s*(\{.*?)```", text, re.S)
    body = block.group(1) if block else text[text.find("{"):] if "{" in text else ""
    try:
        data = _loads(body[:body.rfind("}") + 1] if block is None else body) if body else {}
        listed = data.get("models") if isinstance(data, dict) else None
        problem = None
    except ValueError:
        listed, problem = _objects(body, "models"), "The answer stops partway or isn't valid JSON: kept the names that could be read."
    names = [(m["identifier"].strip(), m["model"].strip()[:100], str(m.get("confidence") or ""))
             for m in listed or [] if isinstance(m, dict) and isinstance(m.get("identifier"), str)
             and isinstance(m.get("model"), str) and m["identifier"].strip() and m["model"].strip()]
    if not names:
        return [], "No model names in what was pasted. Paste the AI's whole answer: the names come as a JSON block."
    return names, problem


def to_rule(r):
    """A suggested rule as a user rule ({"when", "type", "vendor", "source": "ai"}) and what the GUI stores for it
    (one box per field; a list in a regex field becomes one alternation), or (None, None, problem)."""
    when, dtype = r.get("when"), (r.get("type") or "").strip()
    if not isinstance(when, dict) or not when or not dtype:
        return None, None, "needs a 'when' and a 'type'"
    fields = {}
    for f, want in when.items():
        if f not in FIELDS:
            return None, None, f"uses '{f}', which rules here can't"
        values = [str(w).strip() for w in (want if isinstance(want, list) else [want]) if str(w).strip()]
        if not values:
            return None, None, f"'{f}' is empty"
        if f in REGEX:
            fields[f] = values[0] if len(values) == 1 else "|".join(f"(?:{v})" for v in values)
        else:
            if any("," in v for v in values):
                return None, None, f"'{f}' can't contain a comma"
            fields[f] = ", ".join(values)
    rule = {"when": {f: v for f, v in fields.items()}, "type": dtype, "source": "ai"}
    if r.get("vendor"):
        rule["vendor"] = str(r["vendor"])
    problem = devid.check_rule(devid._split_lists(rule))
    if problem:
        return None, None, problem
    return rule, {**fields, "type": dtype}, None


def preview(suggested, devices, rows_ids, user_rules, oui):
    """Each suggested rule checked against every device: what it would change and whether it is safe to take.

    `devices`: [(mac, hostname for rules, info, names)] as loupd classifies them; `rows_ids`: {"D1": [macs]} from
    the table; `user_rules`: the user's rules now. Returns one dict per suggested rule: its rows, type, vendor,
    model, confidence, note, the GUI fields to save ("fields", None if it can't be saved), "changes" [[old, new,
    count]], and flags: "invalid" (why), "spills" (changes devices outside its rows), "nothing" (changes no device,
    and why: "duplicate" of a rule the user has, "already" the type its devices have, "unknown_rows" ids no device
    has, else it matches none), "vaguer" (a specific type to a generic one); "pick" is whether it starts ticked."""
    def same(a, b):
        return a.get("type") == b.get("type") and devid._split_lists(a).get("when") == devid._split_lists(b).get("when")
    def run(rules):
        devid.set_user_rules(rules)
        return {mac: devid.classify(mac, hn, info, names, oui)[0] for mac, hn, info, names in devices}
    try:
        base = run(user_rules)
        out = []
        for r in suggested:
            ids = [str(d) for d in (r.get("devices") or []) if isinstance(d, (str, int))]
            mine = {m for d in ids for m in rows_ids.get(d.upper(), [])}
            rule, fields, problem = to_rule(r)
            item = {"devices": ids, "type": (r.get("type") or "").strip(), "vendor": r.get("vendor") or "",
                    "model": r.get("model") or "", "confidence": r.get("confidence") or "",
                    "note": r.get("note") or "", "when": r.get("when") or {}, "fields": fields,
                    "changes": [], "invalid": problem, "spills": 0, "nothing": False, "vaguer": False,
                    "duplicate": False, "already": False, "others": [],
                    "unknown_rows": [d for d in ids if d.upper() not in rows_ids]}
            if rule:
                new = run([rule] + list(user_rules))
                changed = {mac for mac in new if new[mac] != base[mac]}
                counts = {}
                for mac in changed:
                    k = (base[mac] or "", new[mac] or "")
                    counts[k] = counts.get(k, 0) + 1
                item["changes"] = [[a, b, n] for (a, b), n in sorted(counts.items(), key=lambda kv: -kv[1])]
                item["spills"] = len(changed - mine)
                item["others"] = sorted(changed - mine)      # their MACs; query.py names them
                item["nothing"] = not changed
                if not changed:
                    item["duplicate"] = any(isinstance(u, dict) and "when" in u and same(rule, u) for u in user_rules)
                    item["already"] = bool(mine) and all(base[m] == rule["type"] for m in mine if m in base)
                item["vaguer"] = any(not needs_help(a) and needs_help(b) for a, b in counts)
            item["pick"] = bool(rule) and not (item["spills"] or item["nothing"] or item["vaguer"])
            out.append(item)
        return out
    finally:
        devid.set_user_rules(user_rules)
