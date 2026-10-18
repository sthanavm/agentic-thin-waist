"""Phase 2 validation: record.json vs raw JSONL vs pcap, for AV1-blocked cells.

Three layers per app:
  L1 record.json player_qoe + per_app_stats
  L2 the per-second JSONL the collector actually wrote
  L3 the capture, re-attributed from scratch

Plus the two run-level invariants: the shared cap must hold on COMBINED traffic
(summed on a 1s grid - summing per-app peaks is wrong, they land at different
instants), and the capture must cover the whole call.
"""

import collections
import json
import pathlib
import sys

H_ = pathlib.Path.home() / "sthanav-agentic-thin-waist"
sys.path.insert(0, str(H_ / "experiments/pramana"))
sys.path.insert(0, str(H_))
import pramana_helpers as H  # noqa: E402

BASE = H_ / "experiments/pramana/results/pramana_runs"
ORDER = [(6, 150), (10, 50), (3, 150), (3, 50), (6, 50), (10, 150)]


def newest(bw, lat):
    pat = "zoom+vimeo_%dmbps_%dms_0pct_pfifo_cubic_concurrent2_t1_nocvimeoav01_*" % (
        bw,
        lat,
    )
    c = sorted(BASE.glob(pat), key=lambda p: p.stat().st_mtime)
    return c[-1] if c else None


def samples(d, app):
    p = d / "qoe" / (app + "_stats.jsonl")
    if not p.exists():
        return []
    out = []
    for ln in open(p):
        if not ln.strip() or '"meta"' in ln:
            continue
        s = (json.loads(ln) or {}).get("stats") or {}
        if s:
            out.append(s)
    return out


def check(bw, lat):
    d = newest(bw, lat)
    if d is None:
        return None
    rec = json.load(open(d / "record.json"))
    at = H.attribute_capture(d / "capture.pcap", ["zoom", "vimeo"], float(bw))
    fails, warns, apps = [], [], {}

    # combined cap, from the summed 1s series
    grid = collections.defaultdict(float)
    for a in ("zoom", "vimeo"):
        t = at.traffic(a, "download")
        if t:
            for ts, mb in zip(t.times, t.mbps):
                grid[round(ts)] += mb
    cpeak = max(grid.values()) if grid else 0.0
    if cpeak > bw * 1.15:
        fails.append("combined peak %.2f > cap %d" % (cpeak, bw))

    # the AV1 block must be recorded, and must apply to vimeo only
    blocked = rec.get("app_blocked_codecs") or {}
    if blocked.get("vimeo") != ["av01"]:
        fails.append("vimeo AV1 block not recorded: %s" % blocked)
    if blocked.get("zoom"):
        fails.append("zoom unexpectedly codec-blocked: %s" % blocked)

    for app in ("zoom", "vimeo"):
        q = (rec.get("player_qoe") or {}).get(app) or {}
        st = samples(d, app)
        f = []
        # L3 vs L1
        dl = ((rec.get("per_app_stats") or {}).get(app) or {}).get("download") or {}
        t = at.traffic(app, "download")
        l3 = "n/a"
        if t:
            drift = abs((t.total_mb or 0) - (dl.get("total_mb") or 0))
            l3 = "%.2f MB (drift %.3f)" % (t.total_mb, drift)
            if drift > 0.05:
                f.append("pcap/record drift %.3f" % drift)
        # L2: frame continuity and the layout box
        tf = [s.get("total_video_frames") for s in st]
        resets = [i for i in range(1, len(tf)) if (tf[i] or 0) < (tf[i - 1] or 0)]
        if resets:
            warns.append("%s frame-counter reset @%s" % (app, resets[:3]))
        zero = sum(
            1
            for s in st
            if not ((s.get("player_elem_w") or 0) * (s.get("player_elem_h") or 0))
        )
        if st and zero / len(st) >= 0.5:
            fails.append(
                "%s measured an unrendered element in %d/%d samples"
                % (app, zero, len(st))
            )
        # tile census: Zoom must show exactly one remote tile
        ec = collections.Counter(s.get("video_element_count") for s in st)
        dc = collections.Counter(s.get("video_decoded_count") for s in st)
        if app == "zoom":
            decoded = [k for k in dc if k is not None]
            if not decoded:
                warns.append("zoom tile census absent (pre-census collector?)")
            elif set(decoded) != {1}:
                fails.append("zoom decoded tile count != 1: %s" % dc.most_common())
        # Zoom must not invent a bitrate
        if app == "zoom" and q.get("mean_bitrate_mbps") is not None:
            f.append("zoom bitrate invented")
        # capture must cover the call
        sess = q.get("session_start_epoch")
        shift = (sess - at.t0_epoch) if sess and at.t0_epoch else None
        if (
            shift is None
            or shift < -1
            or (shift + (q.get("session_seconds") or 0)) > at.duration_s + 2
        ):
            f.append("capture did not cover call")
        res = collections.Counter(
            s.get("resolution")
            for s in st
            if s.get("resolution") and s.get("resolution") != "0x0"
        )
        cod = collections.Counter((s.get("codec") or "?") for s in st)
        apps[app] = dict(
            res=q.get("video_resolution_p"),
            fps=q.get("frame_rate_fps"),
            drop=q.get("dropped_frame_pct"),
            reb=q.get("rebuffer_events"),
            w=q.get("watched_seconds"),
            br=q.get("mean_bitrate_mbps"),
            status=q.get("status"),
            ladder=res.most_common(4),
            codec=cod.most_common(1),
            tiles=ec.most_common(2),
            decoded=dc.most_common(2),
            l3=l3,
            peak=dl.get("peak_throughput_mbps"),
            avg=dl.get("avg_throughput_mbps"),
        )
        fails += ["%s: %s" % (app, x) for x in f]

    return dict(
        dir=d.name,
        bw=bw,
        lat=lat,
        apps=apps,
        fails=fails,
        warns=warns,
        cpeak=cpeak,
        pngs=sorted(p.name for p in d.glob("*.png")),
        pcaps=sorted(p.name for p in d.glob("*.pcap")),
    )


if __name__ == "__main__":
    want = json.loads(sys.argv[1]) if len(sys.argv) > 1 else ORDER
    for bw, lat in want:
        r = check(bw, lat)
        if r is None:
            print("%2dM/%3dms : NOT RUN" % (bw, lat))
            continue
        cls = "VERIFIED" if not r["fails"] else "PIPELINE-BUG"
        if not r["fails"] and r["warns"]:
            cls = "VERIFIED (caveat)"
        print("\n### %2dM/%3dms -> %s  [%s]" % (r["bw"], r["lat"], cls, r["dir"][-10:]))
        print(
            "    combined peak %.2f / cap %d   pcaps=%d charts=%d"
            % (r["cpeak"], r["bw"], len(r["pcaps"]), len(r["pngs"]))
        )
        for a, v in r["apps"].items():
            print(
                "    %-6s res=%-5s fps=%-6s drop=%-7s reb=%-3s watched=%-7s br=%-5s"
                % (a, v["res"], v["fps"], v["drop"], v["reb"], v["w"], v["br"])
            )
            print(
                "           peak/avg %.2f/%.2f | pcap %s | tiles=%s decoded=%s"
                % (v["peak"] or 0, v["avg"] or 0, v["l3"], v["tiles"], v["decoded"])
            )
            print("           ladder %s | codec %s" % (v["ladder"], v["codec"]))
        if r["fails"]:
            print("    FAILS: %s" % r["fails"])
        if r["warns"]:
            print("    WARN : %s" % r["warns"])
