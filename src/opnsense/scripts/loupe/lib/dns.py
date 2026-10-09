"""DNS / mDNS message parsing (answers only; enough for name<->address and mDNS metadata)."""
import ipaddress
import struct

T_A, T_CNAME, T_PTR, T_TXT, T_AAAA, T_SRV = 1, 5, 12, 16, 28, 33


def _name(msg, off, depth=0):
    labels = []
    jumped_end = None
    while True:
        if off >= len(msg) or depth > 20:
            raise ValueError("bad name")
        n = msg[off]
        if n == 0:
            off += 1
            break
        if n & 0xC0 == 0xC0:
            ptr = struct.unpack_from("!H", msg, off)[0] & 0x3FFF
            if jumped_end is None:
                jumped_end = off + 2
            off = ptr
            depth += 1
            continue
        labels.append(msg[off + 1:off + 1 + n].decode("utf-8", "replace"))
        off += 1 + n
    return ".".join(labels).lower(), (jumped_end if jumped_end is not None else off)


def parse(msg):
    """Return (is_response, questions[(name, type)], records[(section, name, type, ttl, value)])."""
    if len(msg) < 12:
        raise ValueError("short")
    _id, flags, qd, an, ns, ar = struct.unpack_from("!HHHHHH", msg, 0)
    off = 12
    questions = []
    for _ in range(qd):
        name, off = _name(msg, off)
        qtype = struct.unpack_from("!H", msg, off)[0]
        off += 4
        questions.append((name, qtype))
    records = []
    for section, count in (("an", an), ("ns", ns), ("ar", ar)):
        for _ in range(count):
            name, off = _name(msg, off)
            rtype, _cls, ttl, rdlen = struct.unpack_from("!HHIH", msg, off)
            off += 10
            rd = msg[off:off + rdlen]
            value = None
            if rtype == T_A and rdlen == 4:
                value = str(ipaddress.IPv4Address(rd))
            elif rtype == T_AAAA and rdlen == 16:
                value = str(ipaddress.IPv6Address(rd))
            elif rtype in (T_CNAME, T_PTR):
                value = _name(msg, off)[0]
            elif rtype == T_SRV and rdlen >= 6:
                value = (struct.unpack_from("!H", rd, 4)[0], _name(msg, off + 6)[0])
            elif rtype == T_TXT:
                kv, p = {}, 0
                while p < len(rd):
                    s = rd[p + 1:p + 1 + rd[p]].decode("utf-8", "replace")
                    p += 1 + rd[p]
                    k, _, v = s.partition("=")
                    if k:
                        kv[k.lower()] = v
                value = kv
            records.append((section, name, rtype, ttl, value))
            off += rdlen
    return bool(flags & 0x8000), questions, records


def answers(msg):
    """For a unicast DNS response: (query name, [addresses], [cname chain], min ttl), or None."""
    is_resp, qs, recs = parse(msg)
    if not is_resp or not qs:
        return None
    qname = qs[0][0]
    addrs, chain, ttl = [], [], None
    for section, _name_, rtype, rttl, value in recs:
        if section != "an":
            continue
        if rtype in (T_A, T_AAAA):
            addrs.append(value)
            ttl = rttl if ttl is None else min(ttl, rttl)
        elif rtype == T_CNAME:
            chain.append(value)
    if not addrs:
        return None
    return qname, addrs, chain, ttl
