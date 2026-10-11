#!/usr/bin/env python3
"""Step A sweep: YouTube and Vimeo, 1/3/6 Mbps, 2 trials, one cell at a time.

Serial by construction. Two runs at once would share the 2 vCPUs and the shaped
link, so neither would measure what it claims to, and one app per run keeps a
capture and a chart attributable to a single application.

Checkpointed: a tag already recorded in the checkpoint file is skipped, so an
interrupted sweep resumes instead of repeating work.

    DUR=180 python run_matrix.py
"""
import json
import os
import sys
import time
import traceback

HOME = os.path.expanduser("~/sthanav-agentic-thin-waist")
sys.path.insert(0, os.path.join(HOME, "experiments/pramana"))
sys.path.insert(0, HOME)
import pramana_helpers as H  # noqa: E402

os.environ.setdefault(
    "PRAMANA_COLLECTOR_SRC",
    os.path.join(HOME, "services/orchestration/scripts/selenium_video_qoe/collect.py"),
)
os.environ.setdefault("PRAMANA_DEFER_ANALYSIS", "1")

APPS = ("youtube", "vimeo")
BANDWIDTHS = (1, 3, 6)
TRIALS = (1, 2)
LATENCY_MS = 50  # baseline for every solo run in this project
DUR = int(os.environ.get("DUR", "180"))
CKPT = os.environ.get("PRAMANA_CKPT", "/tmp/stepa_done.json")


def done():
    try:
        return set(json.load(open(CKPT)))
    except Exception:
        return set()


def mark(tag):
    d = done()
    d.add(tag)
    json.dump(sorted(d), open(CKPT, "w"))


def run_cell(app, bw, trial):
    tag = "%s_%gM_t%d" % (app, bw, trial)
    if tag in done():
        print("SKIP %s (already done)" % tag, flush=True)
        return
    print("=== %s ===" % tag, flush=True)
    try:
        cfg = H.ExperimentConfig(
            apps=[app],
            bandwidth_mbps=float(bw),
            latency_ms=LATENCY_MS,
            loss_pct=0.0,
            aqm="pfifo",
            cca="cubic",
            duration_s=DUR,
            trial=trial,
        )
        rec = H.run_direct(cfg)
    except Exception:
        print("FAILED %s" % tag, flush=True)
        traceback.print_exc()
        return
    if isinstance(rec, dict):
        q = (rec.get("player_qoe") or {}).get(app) or {}
        u = q.get("uniform") or {}
        print(
            "ROW %s %s"
            % (
                tag,
                json.dumps(
                    {
                        "status": q.get("status"),
                        "res_p": q.get("video_resolution_p"),
                        "startup_delay_ms": u.get("startup_delay_ms"),
                        "rebuffer_events": u.get("rebuffer_events"),
                        "video_mbps": u.get("delivered_video_bitrate_mbps"),
                        "audio_mbps": u.get("delivered_audio_bitrate_mbps"),
                        "switch_count": u.get("switch_count"),
                        "rendered_fps": u.get("rendered_fps"),
                    }
                ),
            ),
            flush=True,
        )
    mark(tag)
    time.sleep(5)


if __name__ == "__main__":
    for app in APPS:
        for bw in BANDWIDTHS:
            for trial in TRIALS:
                run_cell(app, bw, trial)
    print("STEP_A_DONE", flush=True)
