#!/usr/bin/env python3
"""The run matrix for the results release.

Serial by construction: two runs at once would share the 2 vCPUs and the shaped
link, so neither would measure what it claims to.

Cells, and which question each answers:
  A  12 cells (YouTube and Vimeo over the ladder) -- startup vs pcap on one
     clock, which needs the page-clock fields the 2026-10-08 ladder lacked.
     Also supplies the pre-registered CDP comparison at 1 and 3 Mbps.
  B  5 trials at YouTube 3 Mbps with the player box ENFORCED and logged --
     the rendition there was measured to be bimodal (480p AV1 vs 720p VP9) at
     an identical box, so this separates "YouTube's choice varies" from "our
     box varied".
  C  Tubi and Twitch, for app coverage.
"""
import json
import os
import sys
import time
import traceback

sys.path.insert(0, "/home/student/sthanav-agentic-thin-waist/experiments/pramana")
sys.path.insert(0, "/home/student/sthanav-agentic-thin-waist")
import pramana_helpers as H  # noqa: E402

os.environ["PRAMANA_COLLECTOR_SRC"] = (
    "/home/student/sthanav-agentic-thin-waist/services/orchestration/scripts/"
    "selenium_video_qoe/collect.py"
)
os.environ["PRAMANA_DEFER_ANALYSIS"] = "1"

DUR = int(os.environ.get("DUR", "180"))
CKPT = "/tmp/matrix_done.json"


def done_set():
    try:
        return set(json.load(open(CKPT)))
    except Exception:
        return set()


def mark(tag):
    d = done_set()
    d.add(tag)
    json.dump(sorted(d), open(CKPT, "w"))


def run(app, bw, trial, window=None, label=""):
    tag = "%s_%gM_t%d%s" % (app, bw, trial, label)
    if tag in done_set():
        print("SKIP (already done) %s" % tag, flush=True)
        return
    print("=== %s ===" % tag, flush=True)
    kw = dict(
        apps=[app],
        bandwidth_mbps=float(bw),
        latency_ms=50,
        loss_pct=0.0,
        aqm="pfifo",
        cca="cubic",
        duration_s=DUR,
        trial=trial,
    )
    if window:
        kw["app_window"] = {app: window}
    try:
        cfg = H.ExperimentConfig(**kw)
        rec = H.run_direct(cfg)
    except Exception:
        print("FAILED %s" % tag, flush=True)
        traceback.print_exc()
        return
    d = None
    if isinstance(rec, dict):
        d = rec.get("run_dir") or rec.get("dir")
        q = (rec.get("player_qoe") or {}).get(app) or {}
        u = q.get("uniform") or {}
        row = {
            k: u.get(k)
            for k in (
                "startup_delay_ms",
                "initial_buffering_ms",
                "rebuffer_events",
                "delivered_video_bitrate_mbps",
                "delivered_audio_bitrate_mbps",
                "switch_count",
                "rendered_fps",
                "mse_video_bytes",
                "mse_audio_bytes",
            )
        }
        row["res_p"] = q.get("video_resolution_p")
        row["status"] = q.get("status")
        print("ROW %s %s" % (tag, json.dumps(row)), flush=True)
    if d:
        print("DIR %s %s" % (tag, d), flush=True)
    mark(tag)
    time.sleep(5)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "A"):
        for bw in (0.5, 1, 1.5, 3, 6, 10):
            run("youtube", bw, 10)
        for bw in (0.5, 1, 1.5, 3, 6, 10):
            run("vimeo", bw, 10)
    if which in ("all", "B"):
        for t in range(21, 26):
            run("youtube", 3, t, window="1280x720", label="_box")
    if which in ("all", "C"):
        run("tubi", 6, 10)
        run("twitch", 6, 10)
    print("MATRIX_DONE", flush=True)
