#!/usr/bin/env python3
"""Why host attribution found googlevideo on one rung and not the others.

Measured already: media rides QUIC (UDP/443) on every rung, and no
googlevideo SNI appears in any TCP ClientHello. So QUIC alone cannot explain the
difference. The remaining candidate is DNS visibility: the attributor maps
IP -> hostname from DNS answers seen IN THE SAME capture, and a warm resolver
cache means no answer is re-sent.

This checks that directly (does a googlevideo A-record appear in each capture?)
and then recomputes the bitrate comparison against a media figure that does not
depend on names at all: bytes to/from the dominant remote peer IP.
"""

import collections
import json
import pathlib
import sys

H_ = pathlib.Path.home() / "sthanav-agentic-thin-waist"
sys.path.insert(0, str(H_ / "experiments/pramana"))
sys.path.insert(0, str(H_))
import pramana_helpers as H  # noqa: E402


def ip_hdr(data, linktype):
    off = 14 if linktype == 1 else (16 if linktype == 113 else 0)
    if len(data) < off + 20:
        return None
    if linktype == 1:
        et = int.from_bytes(data[12:14], "big")
        if et == 0x8100:
            off += 4
            et = int.from_bytes(data[16:18], "big")
        if et != 0x0800:
            return None
    if data[off] >> 4 != 4:
        return None
    ihl = (data[off] & 0xF) * 4
    proto = data[off + 9]
    src = ".".join(str(b) for b in data[off + 12 : off + 16])
    dst = ".".join(str(b) for b in data[off + 16 : off + 20])
    l4 = off + ihl
    if len(data) < l4 + 8:
        return None
    sport = int.from_bytes(data[l4 : l4 + 2], "big")
    dport = int.from_bytes(data[l4 + 2 : l4 + 4], "big")
    payload = data[l4 + 8 :] if proto == 17 else b""
    return proto, src, dst, sport, dport, payload


def dns_names(payload):
    """Yields (name, a_record_ip) for each A record in a DNS response.

    `name` is the QUERIED name, not the answer record's owner name. This
    matters: media CDNs answer through a CNAME chain, so the final A record's
    owner is the chain's tail (`rr2---sn-jxopj-n5oe.c.youtube.com`) while the
    app-identifying name is the question (`rr2---sn-jxopj-n5oe.googlevideo.com`).
    An earlier version of this function returned the owner and so reported no
    googlevideo record in captures that plainly contained one. The attributor
    itself prefers the question (see _harvest_dns), so this now matches it.
    """
    out = []
    try:
        if len(payload) < 12:
            return out
        qd = int.from_bytes(payload[4:6], "big")
        an = int.from_bytes(payload[6:8], "big")
        if an == 0:
            return out

        def rd_name(p):
            parts = []
            seen = 0
            while p < len(payload) and seen < 40:
                ln = payload[p]
                seen += 1
                if ln == 0:
                    p += 1
                    break
                if ln & 0xC0 == 0xC0:
                    ptr = int.from_bytes(payload[p : p + 2], "big") & 0x3FFF
                    sub, _ = rd_name(ptr)
                    parts.append(sub)
                    p += 2
                    break
                parts.append(payload[p + 1 : p + 1 + ln].decode("ascii", "replace"))
                p += 1 + ln
            return ".".join(x for x in parts if x), p

        p = 12
        qname = None
        for _ in range(qd):
            nm, p = rd_name(p)
            if qname is None:
                qname = nm
            p += 4
        for _ in range(an):
            owner, p = rd_name(p)
            nm = qname or owner
            if p + 10 > len(payload):
                break
            rtype = int.from_bytes(payload[p : p + 2], "big")
            rdlen = int.from_bytes(payload[p + 8 : p + 10], "big")
            rd = payload[p + 10 : p + 10 + rdlen]
            if rtype == 1 and rdlen == 4:
                out.append((nm, ".".join(str(b) for b in rd)))
            p += 10 + rdlen
    except Exception:
        return out
    return out


def analyse(pcap):
    peer_bytes = collections.Counter()
    peer_proto = {}
    dns = []
    total = 0
    for ts, orig_len, linktype, data in H.iter_capture(str(pcap)):
        total += orig_len
        r = ip_hdr(data, linktype)
        if not r:
            continue
        proto, src, dst, sport, dport, payload = r
        peer = dst if sport > dport else src
        peer_bytes[peer] += orig_len
        peer_proto.setdefault(peer, collections.Counter())[proto] += orig_len
        if proto == 17 and (sport == 53 or dport == 53):
            dns += dns_names(payload)
    return total, peer_bytes, peer_proto, dns


if __name__ == "__main__":
    sel = json.loads(pathlib.Path("/tmp/ladder_dirs.json").read_text())
    app = sys.argv[1]
    print(
        "%-8s %9s %9s %9s %9s %8s %8s  %s"
        % (
            "rung",
            "mse_MB",
            "pcap_all",
            "peerIP_MB",
            "hostattr",
            "peer/mse",
            "all/mse",
            "googlevideo DNS?",
        )
    )
    for bw in (0.5, 1, 1.5, 3, 6, 10):
        key = f"{app}_{bw:g}"
        if key not in sel:
            print("%-8s NOT RUN" % f"{bw:g}M")
            continue
        d = pathlib.Path(sel[key])
        rec = json.loads((d / "record.json").read_text())
        q = (rec.get("player_qoe") or {}).get(app) or {}
        u = q.get("uniform") or {}
        mse = (u.get("mse_video_bytes") or 0) + (u.get("mse_audio_bytes") or 0)
        total, peers, pproto, dns = analyse(d / "capture.pcap")
        # the media peer: the single remote IP carrying the most bytes
        top_ip, top_bytes = peers.most_common(1)[0]
        at = H.attribute_capture(d / "capture.pcap", [app], float(bw))
        t = at.traffic(app, "download")
        pall = (t.total_mb * 1e6) if t else 0
        hb = at.host_bytes or {}
        needle = "googlevideo" if app == "youtube" else "vimeocdn"
        hostattr = sum(v for k, v in hb.items() if needle in str(k))
        gv_dns = [f"{n}->{ip}" for n, ip in dns if needle in n]
        print(
            "%-8s %9.2f %9.2f %9.2f %9.2f %8.3f %8.3f  %s"
            % (
                f"{bw:g}M",
                mse / 1e6,
                pall / 1e6,
                top_bytes / 1e6,
                hostattr / 1e6,
                (top_bytes / mse) if mse else 0,
                (pall / mse) if mse else 0,
                ("YES " + gv_dns[0][:40]) if gv_dns else "no",
            )
        )
        pr = pproto.get(top_ip, {})
        print(
            "            media peer %s  UDP=%.1fMB TCP=%.1fMB  (DNS answers in capture: %d)"
            % (top_ip, pr.get(17, 0) / 1e6, pr.get(6, 0) / 1e6, len(dns))
        )
