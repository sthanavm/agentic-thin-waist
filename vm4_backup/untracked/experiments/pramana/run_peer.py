import os, sys, json, time
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana"))
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))
import pramana_helpers as H
URL = "http://10.200.15.104:8090/"
cfg = H.ExperimentConfig(
    apps=["webrtc_peer"], bandwidth_mbps=10, upload_mbps=10,
    latency_ms=50, loss_pct=0, aqm="pfifo", buffer_packets=1000, cca="cubic",
    duration_s=60, trial=1, tag="peer_e2e", app_urls={"webrtc_peer": URL},
)
rec = H.run_direct(cfg)
print("SLUG", cfg.slug)
