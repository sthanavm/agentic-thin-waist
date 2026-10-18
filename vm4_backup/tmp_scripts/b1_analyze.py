#!/usr/bin/env python3
"""Where does each sampling interval go, and does B1 hold?

Reads the per-phase timers the collector now records (loop_ms) so a cadence
failure is attributed to a phase rather than guessed at.
"""
import glob
import json
import pathlib
import statistics
import sys

R = "/home/student/sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
BAR = 2.0

print("%-26s %7s %7s %6s %8s %8s %8s %8s" % (
    "run", "maxgap", "B1", "n>2s", "work_max", "work_p50", "script_mx", "cdp_mx"))
for pat in ("youtube_1mbps_*_t[12]_*", "youtube_3mbps_*_t[12]_*",
            "youtube_6mbps_*_t[12]_*", "vimeo_*_t[12]_*"):
    for d in sorted(glob.glob(R + "/" + pat)):
        app = pathlib.Path(d).name.split("_")[0]
        p = pathlib.Path(d) / "qoe" / (app + "_stats.jsonl")
        if not p.exists():
            continue
        ts, work, scr, cdp = [], [], [], []
        for line in p.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("record") == "meta" or not r.get("stats"):
                continue
            ts.append(r["timestamp"])
            lm = r.get("loop_ms") or {}
            if lm:
                work.append(lm.get("total_work") or 0)
                scr.append(lm.get("script") or 0)
                cdp.append(lm.get("cdp") or 0)
        if len(ts) < 5:
            continue
        gaps = [ts[i] - ts[i - 1] for i in range(1, len(ts))]
        mx = max(gaps)
        nbig = sum(1 for g in gaps if g > BAR)
        print("%-26s %7.2f %7s %6d %8s %8s %8s %8s" % (
            pathlib.Path(d).name[:26], mx, "PASS" if mx <= BAR else "FAIL", nbig,
            ("%.0f" % max(work)) if work else "-",
            ("%.0f" % statistics.median(work)) if work else "-",
            ("%.0f" % max(scr)) if scr else "-",
            ("%.0f" % max(cdp)) if cdp else "-"))
