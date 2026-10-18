#!/usr/bin/env python3
"""B1 and host load per run.

Counts EVERY written sample, including ones whose stats came back null. The
previous version skipped those, which hid the very iterations that caused the
cadence failures: a 39.1 s script-timeout sample vanished from the work column
while its elapsed time still showed up as a gap.
"""
import glob
import json
import pathlib
import statistics
import sys

R = "/home/student/sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
BAR = 2.0

print("%-34s %7s %6s %5s %9s %9s %7s %7s %6s" % (
    "run", "maxgap", "B1", "n>2s", "work_max", "script_mx", "load_md", "load_mx", "gate"))
rows_out = []
for pat in ("youtube_1mbps_*", "youtube_3mbps_*", "youtube_6mbps_*",
            "vimeo_1mbps_*", "vimeo_3mbps_*", "vimeo_6mbps_*"):
    for d in sorted(glob.glob(R + "/" + pat)):
        dp = pathlib.Path(d)
        app = dp.name.split("_")[0]
        p = dp / "qoe" / (app + "_stats.jsonl")
        if not p.exists():
            continue
        ts, work, scr, load = [], [], [], []
        nerr = 0
        for line in p.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("record") == "meta":
                continue
            ts.append(r["timestamp"])          # every sample, stats or not
            lm = r.get("loop_ms") or {}
            if lm:
                work.append(lm.get("total_work") or 0)
                scr.append(lm.get("script") or 0)
            if r.get("host_load1") is not None:
                load.append(r["host_load1"])
            if r.get("sample_error"):
                nerr += 1
        if len(ts) < 5:
            continue
        gaps = [ts[i] - ts[i - 1] for i in range(1, len(ts))]
        mx = max(gaps)
        gate = "?"
        hc = dp / "host_conditions.json"
        if hc.exists():
            try:
                gate = json.loads(hc.read_text()).get("host_load_gate", "?")
            except Exception:
                pass
        print("%-34s %7.2f %6s %5d %9s %9s %7s %7s %6s" % (
            dp.name[:34], mx, "PASS" if mx <= BAR else "FAIL",
            sum(1 for g in gaps if g > BAR),
            ("%.0f" % max(work)) if work else "-",
            ("%.0f" % max(scr)) if scr else "-",
            ("%.1f" % statistics.median(load)) if load else "-",
            ("%.1f" % max(load)) if load else "-", gate))
        rows_out.append((dp.name, mx, mx <= BAR, nerr))
print()
nf = sum(1 for _, _, ok, _ in rows_out if not ok)
ne = sum(n for _, _, _, n in rows_out)
print("B1: %d of %d runs pass; %d sample(s) carried an error across all runs"
      % (len(rows_out) - nf, len(rows_out), ne))
