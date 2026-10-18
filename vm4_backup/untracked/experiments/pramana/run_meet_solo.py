import os, sys, time, json
os.environ["PRAMANA_CAPTURE_OVERHEAD_S"] = "120"
os.environ["PRAMANA_PLOT_YMAX"] = "11"
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana"))
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))
import pramana_helpers as H
from qoe_report import build_report
from pathlib import Path

ROOM = sys.argv[1]
DUR = int(os.environ.get("MEET_DURATION", "180"))
CLIP = os.path.expanduser("~/pramana-assets/bbb_720p24_10s.y4m")
COMBOS = [(bw, lat) for bw in (3, 6, 10) for lat in (50, 150)]
for bw, lat in COMBOS:
    print("=== RUN bw=%dMbps lat=%dms ===" % (bw, lat), flush=True)
    cfg = H.ExperimentConfig(
        apps=["meet"], bandwidth_mbps=bw, upload_mbps=bw,
        latency_ms=lat, loss_pct=0, aqm="pfifo", buffer_packets=1000, cca="cubic",
        duration_s=DUR, trial=1, tag="meet_solo_%dm_%dms" % (bw, lat),
        app_urls={"meet": ROOM},
        peers={"meet": {"join_url": ROOM, "name": "pramana-bot", "y4m": CLIP}},
        peer_wait_s=300,
    )
    try:
        rec = H.run_direct(cfg)
        q = (rec.get("player_qoe") or {}).get("meet") or {}
        rd = rec.get("run_dir")
        if q.get("status") == "ok" and rd:
            try:
                build_report(Path(rd))
            except Exception as e:
                print("  report failed: %s" % e, flush=True)
        print("RESULT %dM_%dms %s" % (bw, lat, json.dumps({k: q.get(k) for k in
              ("status","video_resolution_p","frame_rate_fps","mean_bitrate_mbps",
               "dropped_frame_pct","rebuffer_events","mean_jitter_secs",
               "watched_seconds")})), flush=True)
    except Exception as e:
        print("FAILED %dM_%dms %s: %s" % (bw, lat, type(e).__name__, str(e)[:160]), flush=True)
print("SWEEP_DONE", flush=True)
