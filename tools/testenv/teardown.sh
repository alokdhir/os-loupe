#!/bin/sh
# Remove the Loupe test bench from the libvirt/Incus host (run there as root).
# Safe to run at any time, any number of times: every step checks first, and
# only the names listed here are touched. Updated BEFORE each thing is created.
set -u

say() { echo "teardown: $*"; }

vm_rm() {   # libvirt domain + its storage
	if virsh dominfo "$1" >/dev/null 2>&1; then
		say "removing VM $1"
		virsh destroy "$1" >/dev/null 2>&1
		virsh undefine "$1" --nvram --remove-all-storage >/dev/null 2>&1 \
			|| virsh undefine "$1" --remove-all-storage
	fi
}

net_rm() {  # libvirt network
	if virsh net-info "$1" >/dev/null 2>&1; then
		say "removing network $1"
		virsh net-destroy "$1" >/dev/null 2>&1
		virsh net-undefine "$1"
	fi
}

ct_rm() {   # Incus instance
	if incus info "$1" >/dev/null 2>&1; then
		say "removing container $1"
		incus delete --force "$1"
	fi
}

file_rm() {
	if [ -e "$1" ]; then say "removing $1"; rm -rf -- "$1"; fi
}

# --- resources, newest first (added as they are created) ---
for c in loupe-c4 loupe-c3 loupe-c2 loupe-c1 loupe-fakenet; do ct_rm $c; done
vm_rm loupe-opnsense
file_rm /var/lib/libvirt/images/loupe-opnsense.qcow2
file_rm /var/lib/libvirt/images/loupe-opnsense-nano.img
file_rm /var/lib/libvirt/images/loupe-opnsense-nano.img.bz2
net_rm loupe-trunk
net_rm loupe-wan

# --- checks: the host's own VMs and networks are untouched ---
# CHECK_VMS="vm1 vm2" (set on the host) lists VMs that must still be running.
for vm in ${CHECK_VMS:-}; do
	virsh domstate "$vm" 2>/dev/null | grep -q running \
		&& say "$vm running" || say "WARNING: $vm not running"
done
virsh net-info default >/dev/null 2>&1 && say "libvirt default network present"
# last: this directory itself (scripts, the test router's ssh key, the Loupe copy)
file_rm /root/loupe-testenv
say "done"
