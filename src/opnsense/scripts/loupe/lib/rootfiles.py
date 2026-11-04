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


"""The one thing that stays root: a small process that reads a fixed list of files (DHCP servers' leases and static
hosts, some only root can read) and passes their bytes to loupd over a pipe. It parses nothing and takes no input: loupd
can only make it exit. It sandboxes itself with Capsicum, so it reaches those directories and nothing else. A directory
that appears after it started (ISC DHCP installed later) is only seen after a restart."""
import ctypes
import os
import select
import struct

EVERY = 60                  # seconds between reads
MAX_FILE = 64 << 20         # more than any lease file; a bigger one is passed on cut short
MISSING = 0xFFFFFFFF
_FRAME = struct.Struct("!BI")     # index in the list of files, length (MISSING: no such file)
_keep = None                # our end of the pipe the process watches: it exits when this closes


def _read(folders, path):
    folder, name = os.path.split(path)
    if folder not in folders:
        return None
    try:
        fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC, dir_fd=folders[folder])
    except OSError:
        return None
    try:
        out = []
        left = MAX_FILE
        while left:
            data = os.read(fd, min(left, 1 << 20))
            if not data:
                break
            out.append(data)
            left -= len(data)
        return b"".join(out)
    except OSError:
        return None
    finally:
        os.close(fd)


def _sandbox():
    """Capability mode, for good: only what is already open, by paths below it. False where it isn't available."""
    try:
        return ctypes.CDLL(None, use_errno=True).cap_enter() == 0
    except (OSError, AttributeError):
        return False


def _write(fd, data):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def serve(paths, out, parent, every=EVERY):
    """Read the files whenever they may have changed, and send what did, until the parent is gone."""
    folders = {}
    for folder in {os.path.dirname(p) for p in paths}:
        try:
            folders[folder] = os.open(folder, os.O_SEARCH | os.O_DIRECTORY | os.O_CLOEXEC)
        except OSError:
            pass
    _sandbox()
    sent = {}
    while True:
        for i, path in enumerate(paths):
            data = _read(folders, path)
            if i not in sent or sent[i] != data:
                _write(out, _FRAME.pack(i, MISSING if data is None else len(data)) + (data or b""))
                sent[i] = data
        if select.select([parent], [], [], every)[0]:
            return


def start(paths, every=EVERY):
    """Fork the process (before anything else is open: it keeps no other descriptor). Returns the pipe to read."""
    global _keep
    rd, wr = os.pipe()
    alive_rd, alive_wr = os.pipe()
    if os.fork() == 0:
        try:
            for fd in range(3, 256):
                if fd not in (wr, alive_rd):
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            serve(paths, wr, alive_rd, every)
        except BaseException:   # the parent went away while writing, or anything else: it has nothing to clean up
            pass
        finally:
            os._exit(0)
    os.close(wr)
    os.close(alive_rd)
    _keep = alive_wr
    return rd


def _exactly(fd, n):
    out = bytearray()
    while len(out) < n:
        data = os.read(fd, min(n - len(out), 1 << 20))
        if not data:
            return None
        out += data
    return bytes(out)


def feed(fd, paths, store):
    """Pass on what arrives: store(path, text, None when the file is missing). Returns when the process has gone."""
    while True:
        head = _exactly(fd, _FRAME.size)
        if head is None:
            return
        i, n = _FRAME.unpack(head)
        if n == MISSING:
            store(paths[i], None)
            continue
        data = _exactly(fd, n) if n else b""
        if data is None:
            return
        store(paths[i], data.decode("utf-8", "replace"))
