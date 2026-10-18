import os, sys, json
from pathlib import Path
os.environ["PRAMANA_CAPTURE_OVERHEAD_S"] = "120"
os.environ["PRAMANA_PLOT_YMAX"] = "11"
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana"))
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))
import pramana_helpers as H
from qoe_report import build_report

ROOM = sys.argv[1]
COMBOS = json.loads(sys.argv[2])           # [[bw,lat],...]
DUR = int(os.environ.get("ZOOM_DURATION", "180"))
CLIP = os.path.expanduser("~/pramana-assets/bbb_720p24_10s.y4m")
for bw, lat in COMBOS:
    print("=== RUN bw=%dMbps lat=%dms ===" % (bw, lat), flush=True)
    cfg = H.ExperimentConfig(
        apps=["zoom"], bandwidth_mbps=bw, upload_mbps=bw,
        latency_ms=lat, loss_pct=0, aqm="pfifo", buffer_packets=1000, cca="cubic",
        duration_s=DUR, trial=1, tag="zoom_solo_%dm_%dms" % (bw, lat),
        app_urls={"zoom": ROOM},
        peers={"zoom": {"join_url": ROOM, "name": "pramana-peer", "y4m": CLIP}},
        peer_wait_s=300,
    )
    try:
        rec = H.run_direct(cfg)
        q = (rec.get("player_qoe") or {}).get("zoom") or {}
        print("RESULT %dM_%dms %s" % (bw, lat, json.dumps({k: q.get(k) for k in
              ("status","video_resolution_p","frame_rate_fps","dropped_frame_pct",
               "rebuffer_events","watched_seconds","session_seconds","total_samples",
               "mean_bitrate_mbps")})), flush=True)
    except Exception as e:
        print("FAILED %dM_%dms %s: %s" % (bw, lat, type(e).__name__, str(e)[:150]), flush=True)
print("SWEEP_DONE", flush=True)
