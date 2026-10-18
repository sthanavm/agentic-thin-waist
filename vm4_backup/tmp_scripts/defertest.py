import os, sys, time, json
os.environ["PRAMANA_CAPTURE_OVERHEAD_S"] = "30"
os.environ["PRAMANA_DEFER_ANALYSIS"] = "1"
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana"))
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))
import pramana_helpers as H
t0 = time.time()
cfg = H.ExperimentConfig(apps=["vimeo"], bandwidth_mbps=10, upload_mbps=10,
    latency_ms=50, loss_pct=0, aqm="pfifo", buffer_packets=1000, cca="cubic",
    duration_s=60, trial=1, tag="defertest")
rec = H.run_direct(cfg)
print("DEFER_WALLCLOCK_S %.1f" % (time.time() - t0))
print("DEFER_FLAG", rec.get("analysis_deferred"))
print("PER_APP_STATS_EMPTY", not (rec.get("per_app_stats") or {}).get("vimeo", {}).get("download", {}).get("total_mb"))
print("RUNDIR", (rec.get("artifacts") or {}).get("dir"))
