#!/usr/bin/env python3
"""Agreement between the generic channel and its reference, per metric.

Thresholds are read from the bounds committed before any of this data existed
(results/uniform_qoe_2026-10-06/THRESHOLDS.md); they are hard-coded here to the
same values so the check cannot drift from the published ones.

Time alignment needs no interpolation: the generic keys and the `sfn_*` keys are
produced by the SAME STATS_JS execution, so a sample's two readings share one
timestamp by construction.
"""

import glob
import json
import math
import os
import pathlib
import re
import sys

H_ = pathlib.Path.home() / "sthanav-agentic-thin-waist"
sys.path.insert(0, str(H_ / "experiments/pramana"))
sys.path.insert(0, str(H_))
import pramana_helpers as H  # noqa: E402

RB = H_ / "experiments/pramana/results/pramana_runs"

# Fixed in advance. See THRESHOLDS.md.
BUF_MAE_MAX = 1.0
BUF_R_MIN = 0.90
FRAME_MATCH_MIN = 0.90
FRAME_TOL = 2
FRAME_MAE_MAX = 2.0
BR_RATIO_LO, BR_RATIO_HI = 1.00, 1.25
EVENT_TOL = 1
FPS_OVERSHOOT_MAX = 1.05


def samples(d, app):
    p = pathlib.Path(d) / "qoe" / f"{app}_stats.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip() or '"meta"' in line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        st = r.get("stats")
        if isinstance(st, dict) and st:
            st["_ts"] = r.get("timestamp")
            out.append(st)
    return out


def f_num(v):
    """First number in a value that may be '20.18 s' or '1479 Kbps' or a float."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group(0)) if m else None


def sfn_total_frames(v):
    """'905x509 / 0 dropped of 115' -> 115."""
    if not v:
        return None
    m = re.search(r"of\s+(\d+)", str(v))
    return float(m.group(1)) if m else None


def sfn_itag(v):
    """'vp09.00.51.08.01.01.01.01.00 (247) / opus (251)' -> '247' (video itag)."""
    if not v:
        return None
    m = re.search(r"\((\d+)\)", str(v))
    return m.group(1) if m else None


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def analyse(d, app, bw):
    st = samples(d, app)
    rec = json.loads((pathlib.Path(d) / "record.json").read_text())
    q = (rec.get("player_qoe") or {}).get(app) or {}
    u = q.get("uniform") or {}
    res = {"cell": f"{app}@{bw}Mbps", "dir": pathlib.Path(d).name[-10:], "n": len(st)}

    # ── buffer level vs sfn_buffer_health_seconds ────────────────────────────
    pairs = [
        (f_num(s.get("buffer_ahead_secs")), f_num(s.get("sfn_buffer_health_seconds")))
        for s in st
    ]
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    if pairs:
        errs = [abs(a - b) for a, b in pairs]
        mae = sum(errs) / len(errs)
        r = pearson([a for a, _ in pairs], [b for _, b in pairs])
        res["buffer"] = {
            "n": len(pairs),
            "mae_s": round(mae, 3),
            "r": None if r is None else round(r, 4),
            "pass": bool(mae <= BUF_MAE_MAX and (r is not None and r >= BUF_R_MIN)),
        }
    else:
        res["buffer"] = {"n": 0, "pass": None, "why": "no sfn buffer field"}

    # ── decoded frames vs sfn_dims_and_frames ────────────────────────────────
    fp = [
        (
            f_num(s.get("total_video_frames")),
            sfn_total_frames(s.get("sfn_dims_and_frames")),
        )
        for s in st
    ]
    fp = [(a, b) for a, b in fp if a is not None and b is not None]
    if fp:
        errs = [abs(a - b) for a, b in fp]
        mae = sum(errs) / len(errs)
        match = sum(1 for e in errs if e <= FRAME_TOL) / len(errs)
        res["frames"] = {
            "n": len(fp),
            "match_rate": round(match, 4),
            "mae": round(mae, 3),
            "pass": bool(match >= FRAME_MATCH_MIN and mae <= FRAME_MAE_MAX),
        }
    else:
        res["frames"] = {"n": 0, "pass": None, "why": "no sfn frame field"}

    # ── delivered bitrate vs pcap bytes, same window ─────────────────────────
    pcap = pathlib.Path(d) / "capture.pcap"
    mse_v = u.get("mse_video_bytes")
    mse_a = u.get("mse_audio_bytes")
    if pcap.exists() and mse_v:
        at = H.attribute_capture(pcap, [app], float(bw))
        t = at.traffic(app, "download")
        pbytes = (t.total_mb * 1e6) if t else 0.0
        media = (mse_v or 0) + (mse_a or 0)
        ratio = (pbytes / media) if media else None
        res["bitrate"] = {
            "video_mbps": u.get("delivered_video_bitrate_mbps"),
            "audio_mbps": u.get("delivered_audio_bitrate_mbps"),
            "mse_media_mb": round(media / 1e6, 2),
            "pcap_mb": round(pbytes / 1e6, 2),
            "ratio": None if ratio is None else round(ratio, 3),
            "pass": bool(ratio is not None and BR_RATIO_LO <= ratio <= BR_RATIO_HI),
        }
        rtb = u.get("rt_media_bytes")
        if rtb:
            res["bitrate"]["rt_mb"] = round(rtb / 1e6, 2)
            res["bitrate"]["rt_vs_mse"] = round(rtb / media, 3) if media else None
    else:
        res["bitrate"] = {"pass": None, "why": "no pcap or no appended bytes"}

    # ── rebuffers vs sfn_player_state == 3 episodes ──────────────────────────
    states = [f_num(s.get("sfn_player_state")) for s in st]
    episodes, prev = 0, None
    for v in states:
        if v == 3 and prev != 3:
            episodes += 1
        prev = v
    ours = u.get("rebuffer_events")
    if any(v is not None for v in states) and ours is not None:
        res["rebuffers"] = {
            "ours": ours,
            "sfn_buffering_episodes": episodes,
            "pass": bool(abs(ours - episodes) <= EVENT_TOL),
        }
    else:
        res["rebuffers"] = {"ours": ours, "pass": None, "why": "no sfn player_state"}

    # ── switches vs sfn itag changes ─────────────────────────────────────────
    itags = [sfn_itag(s.get("sfn_codecs")) for s in st]
    itags = [i for i in itags if i]
    changes = sum(1 for a, b in zip(itags, itags[1:]) if a != b)
    ours_sw = u.get("switch_count")
    if itags and ours_sw is not None:
        res["switches"] = {
            "ours": ours_sw,
            "sfn_itag_changes": changes,
            "itags_seen": sorted(set(itags)),
            "pass": bool(abs(ours_sw - changes) <= EVENT_TOL),
        }
    else:
        res["switches"] = {"ours": ours_sw, "pass": None, "why": "no sfn codecs field"}

    # ── reported, not thresholded ────────────────────────────────────────────
    res["startup_ms"] = u.get("startup_delay_ms")
    res["startup_basis"] = u.get("startup_basis")
    dec_fps = q.get("frame_rate_fps")
    ren_fps = u.get("rendered_fps")
    res["fps"] = {
        "decoded": dec_fps,
        "rendered": ren_fps,
        "consistent": (
            None
            if (dec_fps in (None, 0) or ren_fps is None)
            else bool(ren_fps <= dec_fps * FPS_OVERSHOOT_MAX)
        ),
    }
    res["legacy_res_p"] = q.get("video_resolution_p")
    return res


if __name__ == "__main__":
    app = sys.argv[1]
    rungs = json.loads(sys.argv[2])
    out = []
    for bw in rungs:
        bwt = ("%g" % bw).replace(".", "p") if isinstance(bw, float) else str(bw)
        pats = [
            f"{app}_{bw:g}mbps_50ms_0pct_pfifo_cubic_solo_t1_*",
            f"{app}_{bwt}mbps_50ms_0pct_pfifo_cubic_solo_t1_*",
        ]
        ds = []
        for pat in pats:
            ds += glob.glob(str(RB / pat))
        if not ds:
            print("%s@%sMbps  NOT RUN" % (app, bw))
            continue
        d = max(ds, key=lambda p: os.path.getmtime(p))
        r = analyse(d, app, bw)
        out.append(r)
        print(json.dumps(r))
    pathlib.Path("/tmp/agreement_%s.json" % app).write_text(json.dumps(out, indent=2))
    print("wrote /tmp/agreement_%s.json" % app)
