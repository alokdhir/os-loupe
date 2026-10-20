#!/usr/local/bin/python3
"""Configure a fresh OPNsense (nano image) as the Loupe test router.

Runs on the router; edits /conf/config.xml (backup next to it) and leaves the
reboot to the caller. Idempotent: running it again rewrites the same settings.

  LAN    vtnet0 untagged  10.99.0.1/24   2001:db8:1::1/64
  VLAN20 vlan0.20         10.99.20.1/24  2001:db8:1:20::1/64
  VLAN30 vlan0.30         10.99.30.1/24  2001:db8:1:30::1/64
  WAN    vtnet1           DHCP (v4)      2001:db8:98::2/64, gw 2001:db8:98::100
DHCPv4 + SLAAC on all three, allow-all out, root ssh by key (keys from
/root/.ssh/authorized_keys), ssh/https from the host (10.98.0.1) on WAN, and
Unbound forwarding the made-up "fake." TLD to fakenet (10.98.0.100).
"""
import base64
import shutil
import time
import uuid
import xml.etree.ElementTree as ET

CONF = '/conf/config.xml'
HOST = '10.98.0.1'
FAKENET4, FAKENET6 = '10.98.0.100', '2001:db8:98::100'
NETS = [  # (name, device, descr, v4 net, v6 prefix)
    ('lan', 'vtnet0', 'LAN', '10.99.0', '2001:db8:1:'),
    ('opt1', 'vlan0.20', 'VLAN20', '10.99.20', '2001:db8:1:20:'),
    ('opt2', 'vlan0.30', 'VLAN30', '10.99.30', '2001:db8:1:30:'),
]
MARK = 'loupe-testenv'  # description on everything we add, so reruns replace it


def sub(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, tag, attrs)
    if text is not None:
        el.text = str(text)
    return el


def child(parent, tag):
    el = parent.find(tag)
    return el if el is not None else sub(parent, tag)


def setf(parent, tag, text):
    child(parent, tag).text = None if text is None else str(text)


def drop(parent, tag):
    for el in parent.findall(tag):
        parent.remove(el)


def drop_marked(parent, tag, field):
    for el in parent.findall(tag):
        if (el.findtext(field) or '') == MARK:
            parent.remove(el)


def item(parent, elname, **fields):
    el = sub(parent, elname, uuid=str(uuid.uuid4()))
    for k, v in fields.items():
        sub(el, k, v)
    return el


def main():
    shutil.copy(CONF, '%s.%d.bak' % (CONF, time.time()))
    tree = ET.parse(CONF)
    root = tree.getroot()
    drop(root, 'trigger_initial_wizard')

    system = root.find('system')
    setf(system, 'hostname', 'loupe-test')
    setf(system, 'prefer_ipv4', 1)      # the v6 "internet" is fakenet only: firmware/pkg must not try real sites over v6
    ssh = child(system, 'ssh')
    setf(ssh, 'enabled', 'enabled')
    setf(ssh, 'permitrootlogin', 1)
    setf(ssh, 'noauto', None)
    keys = open('/root/.ssh/authorized_keys', 'rb').read()
    for user in system.findall('user'):
        if user.findtext('name') == 'root':
            setf(user, 'authorizedkeys', base64.b64encode(keys).decode())

    ifs = root.find('interfaces')
    wan = ifs.find('wan')
    drop(wan, 'blockpriv')
    drop(wan, 'blockbogons')
    setf(wan, 'ipaddrv6', FAKENET6.replace('::100', '::2'))
    setf(wan, 'subnetv6', 64)
    setf(wan, 'gatewayv6', 'WAN_GW6')
    for name, dev, descr, v4, v6 in NETS:
        el = child(ifs, name)
        for t in ('track6-interface', 'track6-prefix-id'):
            drop(el, t)
        for k, v in (('enable', 1), ('if', dev), ('descr', descr), ('ipaddr', v4 + '.1'), ('subnet', 24),
                     ('ipaddrv6', v6 + ':1'), ('subnetv6', 64)):
            setf(el, k, v)

    vlans = root.find('vlans')
    drop_marked(vlans, 'vlan', 'descr')
    for _, dev, _, _, _ in NETS[1:]:
        item(vlans, 'vlan', **{'if': 'vtnet0', 'tag': dev.split('.')[1], 'pcp': 0, 'proto': '',
                                'descr': MARK, 'vlanif': dev})

    gws = root.find('OPNsense/Gateways')
    drop_marked(gws, 'gateway_item', 'descr')
    item(gws, 'gateway_item', disabled=0, name='WAN_GW6', descr=MARK, interface='wan', ipprotocol='inet6',
         gateway=FAKENET6, defaultgw=1, monitor_disable=1, priority=255, weight=1)

    dm = root.find('dnsmasq')
    setf(dm, 'interface', ','.join(n[0] for n in NETS))
    for r in dm.findall('dhcp_ranges'):
        dm.remove(r)
    for name, _, _, v4, _ in NETS:
        item(dm, 'dhcp_ranges', interface=name, start_addr=v4 + '.100', end_addr=v4 + '.199',
             domain_type='range', nosync=0, description=MARK)
        item(dm, 'dhcp_ranges', interface=name, start_addr='::1000', end_addr='::2000', constructor=name,
             domain_type='range', nosync=0, ra_mode='slaac', description=MARK)

    rules = root.find('OPNsense/Firewall/Filter/rules')
    drop_marked(rules, 'rule', 'description')
    seq = 100
    allow = []
    for name, *_ in NETS[1:]:
        for proto in ('inet', 'inet6'):
            allow.append(dict(interface=name, ipprotocol=proto, protocol='any', source_net=name,
                              destination_net='any', destination_port=''))
    for port in ('22', '443'):
        allow.append(dict(interface='wan', ipprotocol='inet', protocol='TCP', source_net=HOST,
                          destination_net='(self)', destination_port=port))
    for r in allow:
        seq += 1
        item(rules, 'rule', enabled=1, statetype='keep', sequence=seq, action='pass', quick=1,
             interfacenot=0, direction='in', source_not=0, destination_not=0, log=0, description=MARK, **r)

    ub = root.find('OPNsense/unboundplus')
    dots = child(ub, 'dots')
    drop_marked(dots, 'dot', 'description')
    item(dots, 'dot', enabled=1, type='forward', domain='fake', server=FAKENET4, port=53,
         forward_tcp_upstream=0, forward_first=0, description=MARK)

    ET.indent(tree, '  ')
    tree.write(CONF, xml_declaration=True, encoding='unicode')
    print('config written; reboot to apply')


if __name__ == '__main__':
    main()
