#!/usr/bin/env python3
"""Cross-check sampled switches against an unsampled witness.

Rendition switching is derived from the sampled resolution series, so a switch
that happened entirely inside a sampling gap would be invisible to it. The init
segments are an independent witness: the player appends one whenever it
reconfigures the decoder, and the probe records them with their own page
timestamps, not on our sampling clock.

If distinct init video configs exceed the sampled switch count by more than the
run's rendition count can explain, a switch was missed.
"""
import glob
import json
import pathlib

R = "/home/student/sthanav-agentic-thin-waist/experiments/pramana/results"
print("%-40s %6s %8s %9s %s" % ("run", "gaps", "switches", "init_cfgs", "verdict"))
for base in ("pramana_runs", "busy_host_runs"):
    for d in sorted(glob.glob("%s/%s/*" % (R, base))):
        dp = pathlib.Path(d)
        app = dp.name.split("_")[0]
        p = dp / "qoe" / ("%s_stats.jsonl" % app)
        rj = dp / "record.json"
        if not p.exists() or not rj.exists():
            continue
        last, ts = None, []
        for line in p.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("record") == "meta":
                continue
            ts.append(r["timestamp"])
            if r.get("stats"):
                last = r["stats"]
        if not last or len(ts) < 5:
            continue
        gaps = sum(1 for i in range(1, len(ts)) if ts[i] - ts[i - 1] > 2.0)
        rec = json.loads(rj.read_text())
        u = ((rec.get("player_qoe") or {}).get(app) or {}).get("uniform") or {}
        sc = u.get("switch_count")
        cfgs = last.get("init_video_configs")
        if cfgs is None or sc is None:
            verdict = "no witness available"
        elif cfgs - 1 <= sc:
            verdict = "consistent"
        else:
            verdict = "**POSSIBLE MISSED SWITCH** (%d configs vs %d switches)" % (cfgs, sc)
        print("%-40s %6d %8s %9s %s" % (dp.name[:40], gaps, sc, cfgs, verdict))
