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


"""The configd client against a fake configd on a temporary Unix socket."""
import os
import socket
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "opnsense", "scripts", "loupe"))
from lib import configd  # noqa: E402

UNITS = '[{"name": "Office", "ip": "192.0.2.2"}]'
CLIENTS = '[{"mac": "02:00:00:00:00:10", "name": "iPad-1", "vendor_name": false}]'


class FakeConfigd:
    """Answers each connection as configd does: the output, two newlines, three NULs (in small pieces)."""

    def __init__(self, answers):
        self.answers, self.asked = answers, []
        self.dir = tempfile.mkdtemp(dir="/tmp")
        self.path = os.path.join(self.dir, "c.sock")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(4)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                cmd = conn.recv(4096).decode()
                self.asked.append(cmd)
                reply = (self.answers.get(cmd, "Action not allowed or missing") + "\n\n").encode() + b"\x00\x00\x00"
                for i in range(0, len(reply), 7):
                    conn.sendall(reply[i:i + 7])

    def close(self):
        self.sock.close()
        os.unlink(self.path)
        os.rmdir(self.dir)


class TestConfigd(unittest.TestCase):
    def setUp(self):
        self.fake = FakeConfigd({"eero query units": UNITS, "eero query clients all": CLIENTS,
                                 "eero query nosuch": '{"error": "unknown query"}'})
        self.addCleanup(self.fake.close)
        self.orig = configd.SOCKET
        configd.SOCKET = self.fake.path
        self.addCleanup(setattr, configd, "SOCKET", self.orig)

    def test_query_parses_the_answer(self):
        self.assertEqual(configd.query("eero query units", list, self.fake.path), [{"name": "Office", "ip": "192.0.2.2"}])

    def test_missing_action_or_error_is_none(self):
        self.assertIsNone(configd.query("nosuch query x", list, self.fake.path))
        self.assertIsNone(configd.query("eero query nosuch", dict, self.fake.path))
        self.assertIsNone(configd.query("eero query units", dict, self.fake.path))       # not what was expected

    def test_eero(self):
        self.assertEqual(configd.eero(), {"units": [{"name": "Office", "ip": "192.0.2.2"}],
                                       "clients": [{"mac": "02:00:00:00:00:10", "name": "iPad-1", "vendor_name": False}]})

    def test_eero_names(self):
        clients = [{"mac": "02:00:00:00:00:10", "name": "iPad-1", "vendor_name": False},
                   {"mac": "02:00:00:00:00:11", "name": "02:00:00:00:00:11", "vendor_name": True},  # eero shows the MAC
                   {"mac": "02:00:00:00:00:12", "name": "Watch", "vendor_name": True},
                   {"mac": "02:00:00:00:00:13", "name": " ", "vendor_name": False},
                   {"mac": "02:00:00:00:00:14", "name": None},
                   {"mac": "AC:00:00:00:00:15", "name": "office-pc", "vendor_name": False, "named": True},
                   "junk"]
        self.assertEqual(configd.eero_names(clients), {"02:00:00:00:00:10": "iPad-1", "ac:00:00:00:00:15": "office-pc"})
        self.assertEqual(configd.eero_names(None), {})

    def test_eero_types(self):
        clients = [{"mac": "02:00:00:00:00:10", "type": "tablet"}, {"mac": "02:00:00:00:00:11", "type": "generic"},
                   {"mac": "02:00:00:00:00:12", "type": "unknown"}, {"mac": "AC:00:00:00:00:13", "type": "Game Console"},
                   {"mac": "02:00:00:00:00:14"}, "junk"]
        self.assertEqual(configd.eero_types(clients), {"02:00:00:00:00:10": "tablet", "ac:00:00:00:00:13": "game console"})

    def test_no_configd(self):
        self.assertIsNone(configd.run("eero query units", os.path.join(self.fake.dir, "none.sock")))


class TestNoEero(unittest.TestCase):
    def test_without_the_plugin(self):
        fake = FakeConfigd({})
        self.addCleanup(fake.close)
        orig = configd.SOCKET
        configd.SOCKET = fake.path
        self.addCleanup(setattr, configd, "SOCKET", orig)
        self.assertEqual(configd.eero(), {})
        self.assertEqual(fake.asked, ["eero query units"])      # stops at the first answer


if __name__ == "__main__":
    unittest.main()
