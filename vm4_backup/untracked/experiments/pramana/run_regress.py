import os, sys
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana"))
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))
import pramana_helpers as H
cfg = H.ExperimentConfig(apps=["vimeo"], bandwidth_mbps=10, upload_mbps=10,
    latency_ms=50, loss_pct=0, aqm="pfifo", buffer_packets=1000, cca="cubic",
    duration_s=40, trial=1, tag="driver_split_regression")
rec = H.run_direct(cfg)
q = (rec.get("player_qoe") or {}).get("vimeo") or {}
print("REGRESSION", {k: q.get(k) for k in ("status","video_resolution_p","frame_rate_fps",
      "rebuffer_events","dropped_frame_pct","watched_seconds")})
