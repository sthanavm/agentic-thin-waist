#!/usr/bin/env python3
"""Step 2 regression: run all four apps and report which uniform fields populate.

Short solo runs, no shaping changes - this checks the shared-code change did not
break any app and shows per-app signal availability, which is the metric x app
table Jaber asked for, measured rather than predicted.
"""

import json, os, sys, time

os.environ["PRAMANA_CAPTURE_OVERHEAD_S"] = "30"
os.environ["PRAMANA_DEFER_ANALYSIS"] = "1"
sys.path.insert(
    0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana")
)
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))
import pramana_helpers as H

DUR = int(os.environ.get("DUR", "70"))
for app in json.loads(sys.argv[1]):
    print("=== %s ===" % app, flush=True)
    cfg = H.ExperimentConfig(
        apps=[app],
        bandwidth_mbps=10,
        upload_mbps=10,
        latency_ms=50,
        loss_pct=0,
        aqm="pfifo",
        buffer_packets=1000,
        cca="cubic",
        duration_s=DUR,
        trial=1,
        tag="regress_%s" % app,
    )
    try:
        rec = H.run_direct(cfg)
    except Exception as e:
        print("FAILED %s %s: %s" % (app, type(e).__name__, str(e)[:160]), flush=True)
        continue
    q = (rec.get("player_qoe") or {}).get(app) or {}
    u = q.get("uniform") or {}
    print(
        "REG %s %s"
        % (
            app,
            json.dumps(
                {
                    "status": q.get("status"),
                    "legacy_res_p": q.get("video_resolution_p"),
                    "legacy_fps": q.get("frame_rate_fps"),
                    "sampling_interval_s": u.get("sampling_interval_s"),
                    "startup_ms": u.get("startup_delay_ms"),
                    "buffer_mean": (u.get("buffer_level_secs") or {}).get("mean"),
                    "rebuffers": u.get("rebuffer_events"),
                    "vbitrate_mbps": u.get("delivered_video_bitrate_mbps"),
                    "abitrate_mbps": u.get("delivered_audio_bitrate_mbps"),
                    "switches": u.get("switch_count"),
                    "rendered_fps": u.get("rendered_fps"),
                    "worker": u.get("uses_worker_media"),
                    "rt_usable": u.get("rt_sizes_usable"),
                    "vmime": (u.get("mse_video_mime") or "")[:30],
                }
            ),
        ),
        flush=True,
    )
    print(
        "BASIS %s bitrate=%r startup=%r reb=%r fps=%r"
        % (
            app,
            u.get("bitrate_basis"),
            u.get("startup_basis"),
            u.get("rebuffer_basis"),
            u.get("rendered_fps_basis"),
        ),
        flush=True,
    )
print("REGRESS_DONE", flush=True)
