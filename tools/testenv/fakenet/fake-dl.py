#!/usr/bin/env python3
"""Endless-bytes download server: behind nginx on fakenet (127.0.0.1:8080), or
on a client for traffic between VLANs (fake-dl.py 0.0.0.0 [PORT]).

GET /dl?bytes=500M   streams that many random bytes (K/M/G suffixes), then closes.
PUT/POST /ul         reads the body, throws it away and answers with its size.
Nothing is stored; the client counts what it got and throws it away.
"""
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CHUNK = 1 << 16
UNITS = {'': 1, 'K': 1 << 10, 'M': 1 << 20, 'G': 1 << 30}
LIMIT = 8 << 30


def size(text):
    text = text.strip().upper()
    unit = text[-1] if text and text[-1] in UNITS else ''
    return int(text[:len(text) - len(unit)]) * UNITS[unit]


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        url = urlparse(self.path)
        try:
            n = size(parse_qs(url.query).get('bytes', ['1M'])[0])
        except ValueError:
            n = -1
        if url.path != '/dl' or not 0 <= n <= LIMIT:
            self.send_error(400)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(n))
        self.end_headers()
        try:
            while n > 0:
                part = os.urandom(min(CHUNK, n))
                self.wfile.write(part)
                n -= len(part)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_PUT(self):
        n = int(self.headers.get('Content-Length') or 0)
        got = 0
        while got < n:
            part = self.rfile.read(min(CHUNK, n - got))
            if not part:
                break
            got += len(part)
        body = b'%d\n' % got
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_PUT

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    ThreadingHTTPServer((sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1',
                         int(sys.argv[2]) if len(sys.argv) > 2 else 8080), Handler).serve_forever()
