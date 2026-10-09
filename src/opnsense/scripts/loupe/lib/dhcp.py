"""DHCPv4 client messages: MAC, hostname, vendor class, parameter request list."""
import struct

MAGIC = b"\x63\x82\x53\x63"
MSG_TYPES = {1: "DISCOVER", 3: "REQUEST", 4: "DECLINE", 7: "RELEASE", 8: "INFORM"}


def parse(payload):
    """Return a dict for a client->server message (op=1), else None."""
    if len(payload) < 240 or payload[0] != 1 or payload[236:240] != MAGIC:
        return None
    hlen = payload[2]
    mac = ":".join(f"{b:02x}" for b in payload[28:28 + min(hlen, 16)])
    info = {"mac": mac}
    off = 240
    while off < len(payload):
        code = payload[off]
        if code == 255:
            break
        if code == 0:
            off += 1
            continue
        if off + 1 >= len(payload):
            break
        olen = payload[off + 1]
        val = payload[off + 2:off + 2 + olen]
        off += 2 + olen
        if code == 53 and val:
            info["type"] = MSG_TYPES.get(val[0], str(val[0]))
        elif code == 12:
            info["hostname"] = val.decode("utf-8", "replace")
        elif code == 60:
            info["vendor_class"] = val.decode("utf-8", "replace")
        elif code == 55:
            info["params"] = ",".join(str(b) for b in val)
        elif code == 50 and olen == 4:
            info["requested_ip"] = ".".join(str(b) for b in val)
        elif code == 81 and olen > 3:
            info["fqdn"] = val[3:].decode("utf-8", "replace")
    return info
