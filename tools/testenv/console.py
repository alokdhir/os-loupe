#!/usr/bin/env python3
"""Drive a libvirt guest's serial console (no expect needed).

console.py DOMAIN STEP...   where STEP is one of
  wait:REGEX[@SECONDS]   wait for REGEX in the output (default 300 s)
  send:TEXT              send TEXT (\\n, \\r escapes allowed)
  sleep:SECONDS
Everything the guest prints is echoed to stdout. Exits 1 on a timeout.
"""
import os
import re
import select
import subprocess
import sys
import termios
import time
import tty


def pty_of(dom):
    out = subprocess.run(['virsh', 'ttyconsole', dom], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def main():
    dom, steps = sys.argv[1], sys.argv[2:]
    fd = os.open(pty_of(dom), os.O_RDWR | os.O_NOCTTY)
    tty.setraw(fd)
    termios.tcflush(fd, termios.TCIFLUSH)
    buf = ''

    def read(timeout):
        nonlocal buf
        r, _, _ = select.select([fd], [], [], timeout)
        if r:
            data = os.read(fd, 65536).decode('utf-8', 'replace')
            sys.stdout.write(data)
            sys.stdout.flush()
            buf = (buf + data)[-20000:]

    for step in steps:
        kind, _, arg = step.partition(':')
        if kind == 'wait':
            pat, _, secs = arg.rpartition('@') if re.search(r'@\d+$', arg) else (arg, '', '300')
            deadline = time.time() + float(secs)
            rx = re.compile(pat)
            while not rx.search(buf):
                if time.time() > deadline:
                    print('\n[console.py] timeout waiting for %r' % pat, file=sys.stderr)
                    sys.exit(1)
                read(1)
            buf = buf[rx.search(buf).end():]
        elif kind == 'send':
            os.write(fd, arg.encode().decode('unicode_escape').encode())
            read(0.5)
        elif kind == 'sleep':
            end = time.time() + float(arg)
            while time.time() < end:
                read(0.5)
    read(1)


if __name__ == '__main__':
    main()
