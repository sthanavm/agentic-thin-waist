import os, sys, json
os.environ["PRAMANA_CAPTURE_OVERHEAD_S"] = "120"
os.environ["PRAMANA_PLOT_YMAX"] = "11"
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana"))
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))
import pramana_helpers as H
ROOM = sys.argv[1]
cfg = H.ExperimentConfig(
    apps=["zoom"], bandwidth_mbps=10, upload_mbps=10,
    latency_ms=50, loss_pct=0, aqm="pfifo", buffer_packets=1000, cca="cubic",
    duration_s=60, trial=1, tag="zoom_audio_verify",
    app_urls={"zoom": ROOM},
    peers={"zoom": {"join_url": ROOM, "name": "pramana-peer",
                    "y4m": os.path.expanduser("~/pramana-assets/bbb_720p24_10s.y4m")}},
    peer_wait_s=300,
)
rec = H.run_direct(cfg)
q = (rec.get("player_qoe") or {}).get("zoom") or {}
print("RESULT", json.dumps({k: q.get(k) for k in
      ("status","video_resolution_p","frame_rate_fps","dropped_frame_pct",
       "rebuffer_events","watched_seconds","session_seconds","total_samples",
       "mean_bitrate_mbps","video_startup_time_ms")}))
print("SLUG", cfg.slug)
