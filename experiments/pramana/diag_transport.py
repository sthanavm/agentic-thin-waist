#!/usr/bin/env python3
"""Where do a run's captured bytes actually go, by transport and port?

Item 1: the YouTube bitrate check failed and googlevideo.com was absent from
host attribution at 3/6/10 Mbps. The hypothesis was QUIC. This proves or
disproves it from the capture itself: per-protocol and per-port byte totals, a
count of QUIC long-header Initial packets, and whether a TLS/QUIC SNI is
recoverable. No conclusion is drawn here that the bytes do not support.
"""

import collections
import pathlib
import sys

H_ = pathlib.Path.home() / "sthanav-agentic-thin-waist"
sys.path.insert(0, str(H_ / "experiments/pramana"))
sys.path.insert(0, str(H_))
import pramana_helpers as H  # noqa: E402

ETH_HDR = 14


def ip_payload(data, linktype):
    """Return (proto, src, dst, sport, dport, payload) or None."""
    off = ETH_HDR if linktype == 1 else 0
    if linktype == 113:  # Linux SLL
        off = 16
    if len(data) < off + 20:
        return None
    # VLAN / ethertype
    if linktype == 1:
        et = int.from_bytes(data[12:14], "big")
        if et == 0x8100:
            off += 4
            et = int.from_bytes(data[16:18], "big")
        if et != 0x0800:
            return None
    ver_ihl = data[off]
    if ver_ihl >> 4 != 4:
        return None
    ihl = (ver_ihl & 0xF) * 4
    proto = data[off + 9]
    src = ".".join(str(b) for b in data[off + 12 : off + 16])
    dst = ".".join(str(b) for b in data[off + 16 : off + 20])
    l4 = off + ihl
    if len(data) < l4 + 4:
        return None
    sport = int.from_bytes(data[l4 : l4 + 2], "big")
    dport = int.from_bytes(data[l4 + 2 : l4 + 4], "big")
    if proto == 6:  # TCP
        if len(data) < l4 + 13:
            return None
        doff = (data[l4 + 12] >> 4) * 4
        payload = data[l4 + doff :]
    elif proto == 17:  # UDP
        payload = data[l4 + 8 :]
    else:
        payload = b""
    return proto, src, dst, sport, dport, payload


def quic_long_header(payload):
    """(is_long_header, is_initial) for a UDP payload, per RFC 9000 §17.2."""
    if not payload:
        return False, False
    b0 = payload[0]
    if not (b0 & 0x80):  # short header
        return False, False
    # version-negotiation has version 0; v1 is 0x00000001
    if len(payload) < 5:
        return True, False
    ver = int.from_bytes(payload[1:5], "big")
    if ver == 0:
        return True, False
    ptype = (b0 & 0x30) >> 4  # 0=Initial 1=0-RTT 2=Handshake 3=Retry
    return True, ptype == 0


def tls_sni(payload):
    """Extract SNI from a TCP TLS ClientHello, or None."""
    if len(payload) < 45 or payload[0] != 0x16:
        return None
    try:
        # skip record(5) + handshake(4) + version(2) + random(32)
        p = 5 + 4 + 2 + 32
        sid = payload[p]
        p += 1 + sid
        cs = int.from_bytes(payload[p : p + 2], "big")
        p += 2 + cs
        comp = payload[p]
        p += 1 + comp
        ext_len = int.from_bytes(payload[p : p + 2], "big")
        p += 2
        end = p + ext_len
        while p + 4 <= end:
            etype = int.from_bytes(payload[p : p + 2], "big")
            elen = int.from_bytes(payload[p + 2 : p + 4], "big")
            if etype == 0:
                q = p + 4 + 2 + 1
                nlen = int.from_bytes(payload[q : q + 2], "big")
                return payload[q + 2 : q + 2 + nlen].decode("ascii", "replace")
            p += 4 + elen
    except Exception:
        return None
    return None


def analyse(path):
    proto_bytes = collections.Counter()
    port_bytes = collections.Counter()
    peer_bytes = collections.Counter()
    quic_long = quic_initial = udp443_pkts = 0
    snis = collections.Counter()
    total = 0
    for ts, orig_len, linktype, data in H.iter_capture(str(path)):
        total += orig_len
        r = ip_payload(data, linktype)
        if not r:
            proto_bytes["non-ipv4"] += orig_len
            continue
        proto, src, dst, sport, dport, payload = r
        name = {6: "TCP", 17: "UDP"}.get(proto, "other-%d" % proto)
        proto_bytes[name] += orig_len
        # remote port = whichever isn't ephemeral
        rport = dport if dport < sport else sport
        port_bytes["%s/%d" % (name, rport)] += orig_len
        peer = dst if sport > dport else src
        peer_bytes[peer] += orig_len
        if proto == 17 and (sport == 443 or dport == 443):
            udp443_pkts += 1
            lh, ini = quic_long_header(payload)
            quic_long += 1 if lh else 0
            quic_initial += 1 if ini else 0
        if proto == 6:
            s = tls_sni(payload)
            if s:
                snis[s] += 1
    return {
        "total_mb": total / 1e6,
        "proto": proto_bytes,
        "ports": port_bytes,
        "peers": peer_bytes,
        "udp443_pkts": udp443_pkts,
        "quic_long": quic_long,
        "quic_initial": quic_initial,
        "snis": snis,
    }


if __name__ == "__main__":
    for path in sys.argv[1:]:
        p = pathlib.Path(path)
        r = analyse(p)
        print("== %s  (%.2f MB captured)" % (p.parent.name[-34:], r["total_mb"]))
        print(
            "   transport:",
            ", ".join("%s=%.2fMB" % (k, v / 1e6) for k, v in r["proto"].most_common(4)),
        )
        print(
            "   top ports:",
            ", ".join("%s=%.2fMB" % (k, v / 1e6) for k, v in r["ports"].most_common(5)),
        )
        print(
            "   UDP/443 packets=%d  QUIC long-header=%d  QUIC Initial=%d"
            % (r["udp443_pkts"], r["quic_long"], r["quic_initial"])
        )
        print(
            "   TLS SNI seen (TCP ClientHello): %s"
            % (
                ", ".join("%s x%d" % (k, v) for k, v in r["snis"].most_common(6))
                or "NONE"
            )
        )
        print(
            "   top peers by bytes:",
            ", ".join("%s=%.1fMB" % (k, v / 1e6) for k, v in r["peers"].most_common(4)),
        )
