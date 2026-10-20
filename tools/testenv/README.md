# Test bench

A throwaway OPNsense with VLANs, IPv6 and made-up traffic, on a Linux host with
libvirt and Incus. Nothing here touches the host's own bridges or guests: two
new libvirt networks, one VM and five containers, all named `loupe-*`.

```
 host NAT ── loupe-wan 10.98.0.0/24, 2001:db8:98::/64 ── loupe-fakenet (.100, sites .101-.108)
                 │ vtnet1 (WAN 10.98.0.2, 2001:db8:98::2)
           loupe-opnsense
                 │ vtnet0 (trunk)
 loupe-trunk ── untagged LAN 10.99.0.0/24   2001:db8:1::/64     loupe-c1
             ── VLAN 20     10.99.20.0/24  2001:db8:1:20::/64  loupe-c2, loupe-c4
             ── VLAN 30     10.99.30.0/24  2001:db8:1:30::/64  loupe-c3, loupe-c4
```

- **loupe-fakenet** is the "internet" for IPv6 (the host may have none) and for
  big transfers (so they never leave the host): eight `*.fake` sites, each with
  its own v4 and v6 address, over HTTP, HTTPS/HTTP2 and HTTP/3. `fake-dl.py`
  streams random bytes (`/dl?bytes=500M`) and discards uploads (`/ul`). The test
  router's Unbound forwards `fake.` to it.
- **Clients** (Debian containers) run `bakwas.sh`: small requests to real sites
  (v4, some over HTTP/3), small and big transfers to `*.fake` (v4/v6, HTTP/2 or
  HTTP/3), uploads, and pulls from each other across VLANs. Every transfer is
  logged in `/var/log/bakwas.log` with curl's byte counts, to check Loupe against.
  `loupe-c4` sits on VLAN 20 and 30 with one MAC. Each client has three global
  v6 addresses per network (stable, privacy, DHCPv6).

## Build (on the host, as root, from a copy of this directory)

`push.sh root@HOST [VMS-TO-CHECK]` copies this directory to `/root/loupe-testenv`.

1. Networks: `virsh net-define net/loupe-wan.xml`, `net/loupe-trunk.xml`, `net-start` both.
2. VM: OPNsense nano image (check its sha256), converted to qcow2 and resized
   to 20G, `virt-install --import` with vtnet0 on loupe-trunk
   (52:54:00:99:00:01) and vtnet1 on loupe-wan (52:54:00:98:00:02, reserved
   as 10.98.0.2), `--graphics none --console pty,target_type=serial`.
3. First boot: `console.py` drives the serial console (log in, shell, add
   `key.pub` to root's authorized_keys, start sshd, `pfctl -d`).
4. `r.sh python3 - < router-config.py`, then reboot the VM.
5. `loupe-fakenet`: `incus init images:debian/13` with a nic bridged on
   `loupewan0`, push `fakenet/*`, run `fakenet/setup.sh`.
6. `clients.sh`.
7. Loupe: from a copy of the repo, `ROUTER=root@10.98.0.2 LOUPE_SSH="ssh -i
   /root/loupe-testenv/key ..." tools/deploy.sh`; enable it on lan, opt1, opt2.

`r.sh CMD` runs a command on the test router.

## Remove

`teardown.sh` (or `undo.sh`, written by `push.sh`, which also checks that the
VMs named to `push.sh` still run). It removes only the names it lists and is
safe to run at any point of the build, any number of times.
