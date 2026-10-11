"""Phase 2: Zoom + Vimeo concurrent, with Vimeo's AV1 hidden from the page.

Cells run in a caller-supplied priority order so that if the 40-minute meeting
window expires partway through, the cells that matter most are already done. A
cell whose Zoom side comes back with no live tile means the meeting ended: that
is reported as EXPIRED and the sweep stops rather than writing more empty runs.
"""

import collections
import json
import os
import sys

os.environ["PRAMANA_CAPTURE_OVERHEAD_S"] = "120"
os.environ["PRAMANA_PLOT_YMAX"] = "11"
sys.path.insert(
    0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana")
)
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))
import pramana_helpers as H  # noqa: E402

ROOM = sys.argv[1]
COMBOS = json.loads(sys.argv[2])
DUR = int(os.environ.get("DUR", "180"))
CLIP = os.path.expanduser("~/pramana-assets/bbb_720p24_10s.y4m")
KEYS = (
    "status",
    "video_resolution_p",
    "frame_rate_fps",
    "dropped_frame_pct",
    "rebuffer_events",
    "watched_seconds",
    "total_samples",
    "mean_bitrate_mbps",
)


def sample_census(run_dir, app):
    """Tile census + resolution histogram, straight from the stored samples."""
    p = os.path.join(str(run_dir), "qoe", app + "_stats.jsonl")
    if not os.path.exists(p):
        return {}
    st = []
    with open(p) as fh:
        for ln in fh:
            if not ln.strip() or '"meta"' in ln:
                continue
            s = (json.loads(ln) or {}).get("stats") or {}
            if s:
                st.append(s)
    if not st:
        return {}
    return {
        "element_count": collections.Counter(
            s.get("video_element_count") for s in st
        ).most_common(),
        "decoded_count": collections.Counter(
            s.get("video_decoded_count") for s in st
        ).most_common(),
        "res": collections.Counter(
            s.get("resolution") for s in st if s.get("resolution")
        ).most_common(5),
        "codec": collections.Counter((s.get("codec") or "?") for s in st).most_common(
            1
        ),
    }


for bw, lat in COMBOS:
    print("=== RUN zoom+vimeo bw=%dMbps lat=%dms ===" % (bw, lat), flush=True)
    cfg = H.ExperimentConfig(
        apps=["zoom", "vimeo"],
        bandwidth_mbps=bw,
        upload_mbps=bw,
        latency_ms=lat,
        loss_pct=0,
        aqm="pfifo",
        buffer_packets=1000,
        cca="cubic",
        duration_s=DUR,
        trial=1,
        tag="zv2_%dm_%dms" % (bw, lat),
        app_urls={"zoom": ROOM},
        peers={"zoom": {"join_url": ROOM, "name": "pramana-peer", "y4m": CLIP}},
        peer_wait_s=300,
        # Hide AV1 from Vimeo only. Measured: Vimeo's own quality= parameter
        # forces one rendition and switches ABR off entirely, and a smaller
        # viewport does not bound its ladder at all, so this is the only control
        # that leaves resolution free to respond to the network.
        app_block_codecs={"vimeo": ["av01"]},
    )
    try:
        rec = H.run_direct(cfg)
    except Exception as e:  # noqa: BLE001 - one bad cell must not kill the sweep
        print(
            "FAILED %dM_%dms %s: %s" % (bw, lat, type(e).__name__, str(e)[:160]),
            flush=True,
        )
        continue
    pq = rec.get("player_qoe") or {}
    out = {a: {k: (pq.get(a) or {}).get(k) for k in KEYS} for a in ("zoom", "vimeo")}
    print("RESULT %dM_%dms %s" % (bw, lat, json.dumps(out)), flush=True)
    print(
        "MARK %dM_%dms blocked=%s caps=%s"
        % (bw, lat, rec.get("app_blocked_codecs"), rec.get("app_window_caps")),
        flush=True,
    )
    rd = (rec.get("artifacts") or {}).get("dir") or ""
    for a in ("zoom", "vimeo"):
        print(
            "CENSUS %dM_%dms %s %s" % (bw, lat, a, json.dumps(sample_census(rd, a))),
            flush=True,
        )
    print("RUNDIR %dM_%dms %s" % (bw, lat, rd), flush=True)
    # A Zoom side with no live tile means the meeting is gone; stop cleanly.
    zres = [
        r for r, _ in (sample_census(rd, "zoom").get("res") or []) if r and r != "0x0"
    ]
    if (out["zoom"]["status"] != "ok") or not zres:
        print(
            "EXPIRED after %dM_%dms - zoom has no live tile; stopping cleanly"
            % (bw, lat),
            flush=True,
        )
        break
print("BATCH_DONE", flush=True)
