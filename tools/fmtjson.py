#!/usr/bin/env python3
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

"""Rewrite the rule files one entry per line, so diffs and pull requests stay readable.

usage: tools/fmtjson.py src/opnsense/scripts/loupe/data/*.json
"""
import json
import sys


def dump(data):
    out = ["{"]
    keys = list(data)
    for i, k in enumerate(keys):
        v = data[k]
        comma = "," if i < len(keys) - 1 else ""
        if isinstance(v, list) and v and not all(isinstance(x, str) for x in v) or k == "_comment":
            out.append(f" {json.dumps(k)}: [")
            out += [f"  {json.dumps(x, ensure_ascii=False)}" + ("," if j < len(v) - 1 else "") for j, x in enumerate(v)]
            out.append(" ]" + comma)
        elif isinstance(v, dict):
            out.append(f" {json.dumps(k)}: {{")
            items = list(v.items())
            out += [f"  {json.dumps(a, ensure_ascii=False)}: {json.dumps(b, ensure_ascii=False)}"
                    + ("," if j < len(items) - 1 else "") for j, (a, b) in enumerate(items)]
            out.append(" }" + comma)
        else:
            out.append(f" {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}{comma}")
    return "\n".join(out + ["}", ""])


for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    text = dump(data)
    assert json.loads(text) == data
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
