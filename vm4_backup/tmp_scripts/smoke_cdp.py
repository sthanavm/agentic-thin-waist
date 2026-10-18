#!/usr/bin/env python3
"""One short run to prove the two new channels work on a real page.

Proves or refutes, specifically:
  * Chrome's performance log delivers Network.* events under OUR driver config
    (uc=True, undetectable=True). If it does not, cdp_events_seen stays 0 and
    the CDP reference is unavailable -- which is a reportable UNKNOWN, not
    something to paper over.
  * init_segment_log populates on a real player, giving the decoding codec.
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, "/home/student/sthanav-agentic-thin-waist/experiments/pramana")
sys.path.insert(0, "/home/student/sthanav-agentic-thin-waist")
import pramana_helpers as H  # noqa: E402

os.environ["PRAMANA_COLLECTOR_SRC"] = (
    "/home/student/sthanav-agentic-thin-waist/services/orchestration/scripts/"
    "selenium_video_qoe/collect.py"
)
os.environ["PRAMANA_DEFER_ANALYSIS"] = "1"

cfg = H.ExperimentConfig(
    apps=["youtube"],
    bandwidth_mbps=5.0,
    latency_ms=50,
    loss_pct=0.0,
    aqm="pfifo",
    cca="cubic",
    duration_s=75,
    trial=901,
)
rec = H.run_direct(cfg)
print("RUNDIR", rec.get("run_dir") if isinstance(rec, dict) else rec)
