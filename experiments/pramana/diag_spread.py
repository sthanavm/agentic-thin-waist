#!/usr/bin/env python3
"""Item 6: repeat-trial spread, re-derived with the corrected rebuffer metric.

The batch that produced these runs imported qoe.py at 05:45; the rebuffer fix
landed at 05:58, so every rebuffer figure the batch printed is the pre-fix one
(inflated by one, because the initial buffer fill was counted as a rebuffer).
This re-derives from the saved per-second samples in a fresh process, so the
numbers here are the fixed ones. The batch's own rows are not used.

Also carries the Resource Timing accounting per run (item 5) and the codec
per run (item 7), both of which only exist per-sample and are read from there.
"""

import json
import pathlib
import re
import statistics
import sys

import os

HOME = pathlib.Path(
    os.environ.get(
        "PRAMANA_REPO", str(pathlib.Path.home() / "sthanav-agentic-thin-waist")
    )
)
sys.path.insert(0, str(HOME / "experiments/pramana"))
sys.path.insert(0, str(HOME))
sys.path.insert(0, str(HOME / "shared"))
import qoe as qoelib  # noqa: E402
import pramana_helpers as H  # noqa: E402


def harvest_names(pcap, peer_ip=None):
    """IP -> hostname exactly as attribute_capture learns it, so a naming
    failure can be told apart from a DNS-visibility failure.

    Uses the attributor's own _harvest_dns/_harvest_sni rather than a
    re-implementation. An earlier re-implementation of mine read the ANSWER's
    owner name instead of the QUERIED name; with a CNAME chain those differ,
    and it wrongly concluded no googlevideo record existed in any capture.
    """
    import socket as _s

    dns_host: dict[str, str] = {}
    sni_host: dict[str, str] = {}
    ch_to_peer = 0
    for _ts, _ol, lt, data in H.iter_capture(str(pcap)):
        off = 14 if lt == 1 else (16 if lt == 113 else 0)
        if len(data) < off + 20 or data[off] >> 4 != 4:
            continue
        ihl = (data[off] & 0xF) * 4
        proto = data[off + 9]
        dst = _s.inet_ntoa(data[off + 16 : off + 20])
        l4 = data[off + ihl :]
        if len(l4) < 8:
            continue
        sport = int.from_bytes(l4[0:2], "big")
        dport = int.from_bytes(l4[2:4], "big")
        if proto == 17 and (sport == 53 or dport == 53):
            H._harvest_dns(l4, dns_host)
        elif proto == 6:
            before = len(sni_host)
            H._harvest_sni(l4, dst, sni_host)
            if len(sni_host) > before and dst == peer_ip:
                ch_to_peer += 1
    merged = dict(dns_host)
    merged.update(sni_host)
    return merged, dns_host, sni_host, ch_to_peer


def dirs_from_log(log):
    out = []
    for line in pathlib.Path(log).read_text(errors="replace").splitlines():
        if line.startswith("DIR "):
            _, tag, d = line.split(None, 2)
            out.append((tag, d.strip()))
    return out


def last(st, key):
    for s in reversed(st):
        if st and s.get(key) is not None:
            return s.get(key)
    return None


def one(tag, d, app, bw):
    p = pathlib.Path(d)
    sp = p / "qoe" / f"{app}_stats.jsonl"
    if not sp.exists():
        return None
    summ = qoelib.summarize(str(sp), app)
    u = summ.get("uniform") or {}
    st = [s for s in qoelib.load_samples(str(sp)) if s.get("record") != "meta"]
    st = [s.get("stats") or {} for s in st]
    st = [s for s in st if s]
    row = {
        "tag": tag,
        "app": app,
        "bw": bw,
        "n_samples": len(st),
        "startup_delay_ms": u.get("startup_delay_ms"),
        "initial_buffering_ms": u.get("initial_buffering_ms"),
        "rebuffer_events": u.get("rebuffer_events"),
        "rebuffer_duration_ms": u.get("rebuffer_duration_ms"),
        "video_mbps": u.get("delivered_video_bitrate_mbps"),
        "audio_mbps": u.get("delivered_audio_bitrate_mbps"),
        "switch_count": u.get("switch_count"),
        "rendered_fps": u.get("rendered_fps"),
        "res_p": summ.get("video_resolution_p"),
        "mse_v": u.get("mse_video_bytes"),
        "mse_a": u.get("mse_audio_bytes"),
        "codec": last(st, "codec"),
        "vmime": u.get("mse_video_mime"),
        "rt_all": last(st, "rt_all_bytes"),
        "rt_media": last(st, "rt_media_bytes"),
        "rt_all_e": last(st, "rt_all_entries"),
        "rt_media_e": last(st, "rt_media_entries"),
        "rt_drop_init": last(st, "rt_dropped_by_initiator"),
        "rt_drop_floor": last(st, "rt_dropped_by_size_floor"),
        "rt_drop_bytes": last(st, "rt_dropped_bytes"),
        "rt_full": last(st, "rt_buffer_full_events"),
        "fp_ms": last(st, "first_presentation_ms"),
    }
    # bitrate check against name-independent media-peer bytes
    pcap = p / "capture.pcap"
    if pcap.exists():
        try:
            sys.path.insert(0, str(HOME / "experiments/pramana"))
            import diag_attribution as DA

            total, peers, pproto, dns = DA.analyse(pcap)
            top_ip, top_bytes = peers.most_common(1)[0]
            media = (row["mse_v"] or 0) + (row["mse_a"] or 0)
            row["peer_ip"] = top_ip
            row["peer_mb"] = round(top_bytes / 1e6, 2)
            row["ratio_peer_over_mse"] = round(top_bytes / media, 4) if media else None
            row["pcap_all_mb"] = round(total / 1e6, 2)
            # Did the attributor have a name for the media peer at all, and
            # did that name reach host_bytes? These separate "no DNS was
            # visible" from "the name was visible and not used".
            names, dnsmap, snimap, ch_to_peer = harvest_names(pcap, top_ip)
            needle = "googlevideo" if app == "youtube" else "vimeocdn"
            row["peer_name"] = names.get(top_ip)
            row["peer_name_from"] = (
                "SNI" if top_ip in snimap else ("DNS" if top_ip in dnsmap else None)
            )
            row["names_with_needle"] = sum(1 for v in names.values() if needle in v)
            row["dns_needle"] = sum(1 for v in dnsmap.values() if needle in v)
            row["sni_needle"] = sum(1 for v in snimap.values() if needle in v)
            row["tcp_clienthello_to_peer"] = ch_to_peer
            at = H.attribute_capture(pcap, [app], float(bw))
            hb = at.host_bytes or {}
            row["hostattr_needle_mb"] = round(
                sum(v for k, v in hb.items() if needle in str(k)) / 1e6, 2
            )
            row["hostattr_top"] = list(hb.items())[:3]
            row["adopted"] = getattr(at, "adopted_ips", None)
        except Exception as e:  # noqa: BLE001
            row["peer_err"] = repr(e)[:90]
    return row


def spread(name, rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return "%-26s n=0  (unavailable)" % key
    mn, mx = min(vals), max(vals)
    mean = statistics.mean(vals)
    sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
    cv = (sd / mean * 100.0) if mean else 0.0
    return (
        "%-26s n=%d  min=%-10.4g max=%-10.4g mean=%-10.4g sd=%-9.4g cv=%5.1f%%  range/mean=%5.1f%%"
        % (
            key,
            len(vals),
            mn,
            mx,
            mean,
            sd,
            cv,
            (mx - mn) / mean * 100.0 if mean else 0.0,
        )
    )


if __name__ == "__main__":
    log = sys.argv[1] if len(sys.argv) > 1 else "/tmp/it36b.log"
    cells = {}
    allrows = []
    for tag, d in dirs_from_log(log):
        m = re.match(r"(youtube|vimeo)_([\d.]+)M_t(\w+)", tag)
        if not m:
            continue
        app, bw = m.group(1), float(m.group(2))
        if "probeoff" in tag:
            continue  # the A/B pair is item 3, not a repeat trial
        r = one(tag, d, app, bw)
        if r is None:
            print("MISSING SAMPLES %s" % tag)
            continue
        allrows.append(r)
        cells.setdefault((app, bw), []).append(r)

    pathlib.Path("/tmp/spread_rows.json").write_text(json.dumps(allrows, indent=2))
    print("=" * 108)
    print("PER-RUN (re-derived with the corrected rebuffer metric)")
    print("=" * 108)
    hdr = "%-26s %4s %7s %7s %4s %8s %7s %7s %4s %6s %14s %7s" % (
        "run",
        "res",
        "start",
        "initbuf",
        "reb",
        "vid_mbps",
        "aud",
        "fps",
        "sw",
        "ratio",
        "codec",
        "rt/mse",
    )
    print(hdr)

    def codec_short(r):
        m = re.search(
            r"(av01[\w.]*|vp0?9[\w.]*|avc1[\w.]*|hev1[\w.]*|vp8)",
            str(r.get("vmime") or r.get("codec") or ""),
        )
        return m.group(1)[:14] if m else "?"

    for (app, bw), rows in sorted(cells.items()):
        for r in rows:
            media = (r["mse_v"] or 0) + (r["mse_a"] or 0)
            rtr = (r["rt_all"] / media) if (r.get("rt_all") and media) else None
            print(
                "%-26s %4s %7s %7s %4s %8s %7s %7s %4s %6s %14s %7s"
                % (
                    r["tag"][:26],
                    r["res_p"],
                    r["startup_delay_ms"],
                    r["initial_buffering_ms"],
                    r["rebuffer_events"],
                    r["video_mbps"],
                    r["audio_mbps"],
                    r["rendered_fps"],
                    r["switch_count"],
                    r.get("ratio_peer_over_mse"),
                    codec_short(r),
                    ("%.3f" % rtr) if rtr else "-",
                )
            )
    print()
    for (app, bw), rows in sorted(cells.items()):
        print("=" * 108)
        print("SPREAD  %s @ %gMbps   trials=%d" % (app, bw, len(rows)))
        print("=" * 108)
        for key in (
            "startup_delay_ms",
            "initial_buffering_ms",
            "rebuffer_events",
            "rebuffer_duration_ms",
            "video_mbps",
            "audio_mbps",
            "rendered_fps",
            "switch_count",
            "res_p",
            "ratio_peer_over_mse",
        ):
            print("  " + spread("%s@%g" % (app, bw), rows, key))
        codecs = sorted({(r["codec"] or "?") for r in rows})
        vmimes = sorted({(r["vmime"] or "?") for r in rows})
        print("  codecs across trials: %s" % codecs)
        print("  video mime across trials: %s" % vmimes)
        res = [r["res_p"] for r in rows if r["res_p"] is not None]
        print(
            "  resolutions: %s  %s"
            % (res, "IDENTICAL" if len(set(res)) == 1 else "DIFFER")
        )
    print()
    print("=" * 108)
    print("MEDIA-PEER NAMING (item 1 remaining unknown), per run")
    print("=" * 108)
    print(
        "%-24s %-16s %4s %4s %4s %5s %8s %-30s"
        % (
            "run",
            "media peer",
            "dns",
            "sni",
            "CH",
            "from",
            "hostattr",
            "name attributor had",
        )
    )
    for r in allrows:
        print(
            "%-24s %-16s %4s %4s %4s %5s %8s %-30s"
            % (
                r["tag"][:24],
                r.get("peer_ip"),
                r.get("dns_needle"),
                r.get("sni_needle"),
                r.get("tcp_clienthello_to_peer"),
                str(r.get("peer_name_from")),
                r.get("hostattr_needle_mb"),
                str(r.get("peer_name"))[:30],
            )
        )
    print("  dns/sni = names containing the media needle from each source;")
    print("  CH = TCP ClientHellos to the media peer; from = which source named it.")
    print()
    print("=" * 108)
    print("RESOURCE TIMING ACCOUNTING (item 5), per run")
    print("=" * 108)
    print(
        "%-26s %11s %11s %11s %6s %6s %6s %6s %5s %9s"
        % (
            "run",
            "mse",
            "rt_all",
            "rt_media",
            "all_e",
            "med_e",
            "dr_in",
            "dr_fl",
            "full",
            "all/mse",
        )
    )
    for r in allrows:
        media = (r["mse_v"] or 0) + (r["mse_a"] or 0)
        print(
            "%-26s %11s %11s %11s %6s %6s %6s %6s %5s %9s"
            % (
                r["tag"][:26],
                media,
                r["rt_all"],
                r["rt_media"],
                r["rt_all_e"],
                r["rt_media_e"],
                r["rt_drop_init"],
                r["rt_drop_floor"],
                r["rt_full"],
                (
                    ("%.4f" % (r["rt_all"] / media))
                    if (r.get("rt_all") and media)
                    else "-"
                ),
            )
        )
    print()
    print("wrote /tmp/spread_rows.json")
