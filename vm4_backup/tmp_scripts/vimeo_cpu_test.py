import os, sys, time
os.environ["PRAMANA_CAPTURE_OVERHEAD_S"] = "30"
os.environ["PRAMANA_DEFER_ANALYSIS"] = "1"
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana"))
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))
import pramana_helpers as H
cfg = H.ExperimentConfig(apps=["vimeo"], bandwidth_mbps=10, upload_mbps=10,
    latency_ms=50, loss_pct=0, aqm="pfifo", buffer_packets=1000, cca="cubic",
    duration_s=75, trial=1, tag="cputest")
rec = H.run_direct(cfg)
print("RUNDIR", (rec.get("artifacts") or {}).get("dir"))
