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

"""Print the screenshot shield (tools/shield.js) as a bookmarklet that blurs your words.

usage: tools/shield.py WORD...       e.g. tools/shield.py alice bob example.net 203.0.113.7

Make a bookmark with the printed text as its address; click it on any OPNsense page to blur, again to undo.
Words match anywhere in a text, ignoring case: "bob" blurs the "Bob" of "Bobs-iPhone". They stay on your machine: the
repo is public, so keep the list out of it.
"""
import json
import os
import re
import sys
import urllib.parse


def bookmarklet(words, source):
    body = re.sub(r'/\*.*?\*/', '', source, flags=re.S)               # comments (none inside strings)
    body = '\n'.join(line.strip() for line in body.splitlines() if line.strip())
    code = 'window.LOUPE_SHIELD_WORDS=%s;%s' % (json.dumps(words), body)
    return 'javascript:' + urllib.parse.quote(code, safe="()=;,:'*!~-_.")


def main(argv):
    words = [w for w in argv[1:] if w.strip()]
    if not words:
        sys.exit(__doc__.strip())
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shield.js')) as f:
        print(bookmarklet(words, f.read()))


if __name__ == '__main__':
    main(sys.argv)
