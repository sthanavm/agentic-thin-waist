#!/usr/bin/env python3
"""Step 3: bandwidth ladder for one app, uniform probe, deferred analysis.

Solo, 50 ms, pfifo/cubic/0% loss, 180 s per rung. merge_vendor_stats stays off
so a vendor's own stats never reach the generic channel being validated.
"""

import json, os, sys, time

os.environ["PRAMANA_CAPTURE_OVERHEAD_S"] = os.environ.get("OVERHEAD", "40")
os.environ["PRAMANA_DEFER_ANALYSIS"] = "1"
os.environ["PRAMANA_PLOT_YMAX"] = "11"
sys.path.insert(
    0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana")
)
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))
import pramana_helpers as H

APP = sys.argv[1]
RUNGS = json.loads(sys.argv[2])
DUR = int(os.environ.get("DUR", "180"))
KEYS = (
    "startup_delay_ms",
    "rebuffer_events",
    "rebuffer_duration_ms",
    "delivered_video_bitrate_mbps",
    "delivered_audio_bitrate_mbps",
    "switch_count",
    "rendered_fps",
    "uses_worker_media",
    "rt_sizes_usable",
    "mse_video_bytes",
    "mse_audio_bytes",
    "rt_media_bytes",
    "sampling_interval_s",
)

for bw in RUNGS:
    print("=== %s @ %sMbps ===" % (APP, bw), flush=True)
    cfg = H.ExperimentConfig(
        apps=[APP],
        bandwidth_mbps=bw,
        upload_mbps=bw,
        latency_ms=50,
        loss_pct=0,
        aqm="pfifo",
        buffer_packets=1000,
        cca="cubic",
        duration_s=DUR,
        trial=1,
        tag="uqoe_%s_%sm" % (APP, bw),
        merge_vendor_stats=False,
    )
    try:
        rec = H.run_direct(cfg)
    except Exception as e:
        print(
            "FAILED %s %s %s: %s" % (APP, bw, type(e).__name__, str(e)[:150]),
            flush=True,
        )
        continue
    q = (rec.get("player_qoe") or {}).get(APP) or {}
    u = q.get("uniform") or {}
    row = {k: u.get(k) for k in KEYS}
    row["status"] = q.get("status")
    row["legacy_res_p"] = q.get("video_resolution_p")
    row["legacy_fps"] = q.get("frame_rate_fps")
    row["buffer_mean"] = (u.get("buffer_level_secs") or {}).get("mean")
    print("ROW %s %s %s" % (APP, bw, json.dumps(row)), flush=True)
    print(
        "DIR %s %s %s" % (APP, bw, (rec.get("artifacts") or {}).get("dir")), flush=True
    )
print("LADDER_DONE", flush=True)
