#!/usr/bin/env python3
"""Were rebuffer or switch events lost inside a sampling gap?

Rebuffering comes from the page's own media-event log, which the probe keeps
from document-start and which does not depend on our sampling. Switches come
from the sampled resolution series, which DOES. So a gap can lose a switch but
should not lose a rebuffer. This checks both against the evidence rather than
assuming it.
"""
import glob
import json
import pathlib
import sys

R = "/home/student/sthanav-agentic-thin-waist/experiments/pramana/results"
BAR = 2.0


def samples(p):
    out = []
    for line in pathlib.Path(p).read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("record") == "meta":
            continue
        out.append(r)
    return out


print("%-40s %7s %6s %9s %9s %s" % (
    "run", "maxgap", "gaps", "reb_evts", "switches", "events inside gaps"))
for base in ("pramana_runs", "busy_host_runs"):
    for d in sorted(glob.glob("%s/%s/*" % (R, base))):
        dp = pathlib.Path(d)
        app = dp.name.split("_")[0]
        p = dp / "qoe" / ("%s_stats.jsonl" % app)
        if not p.exists():
            continue
        rows = samples(p)
        if len(rows) < 5:
            continue
        ts = [r["timestamp"] for r in rows]
        gaps = [(i, ts[i] - ts[i - 1]) for i in range(1, len(ts))]
        big = [(i, g) for i, g in gaps if g > BAR]
        last = None
        for r in rows:
            if r.get("stats"):
                last = r["stats"]
        spans = (last or {}).get("waiting_spans") or []
        page0 = None
        for r in rows:
            s_ = r.get("stats") or {}
            if s_.get("page_now_ms") is not None:
                page0 = s_["page_now_ms"]
                break
        # a rebuffer is "inside a gap" if its page-time window overlaps one
        inside = 0
        if page0 is not None and big:
            for w in spans:
                try:
                    if float(w.get("ct") or 0) <= 0:
                        continue
                    a = (float(w["start"]) - page0) / 1000.0
                    b = (float(w["end"]) - page0) / 1000.0
                except Exception:
                    continue
                for i, g in big:
                    gs, ge = ts[i - 1] - ts[0], ts[i] - ts[0]
                    if a < ge and b > gs:
                        inside += 1
                        break
        # resolution changes that the sampled series could not have seen
        heights = []
        for r in rows:
            s_ = r.get("stats") or {}
            h = s_.get("video_height")
            if h is None:
                res = s_.get("resolution")
                if isinstance(res, str) and "x" in res:
                    try:
                        h = int(res.split("x")[1])
                    except Exception:
                        h = None
            heights.append(h)
        straddle = 0
        for i, g in big:
            a, b = heights[i - 1], heights[i]
            if a is not None and b is not None and a != b:
                straddle += 1
        nreb = len([w for w in spans if float(w.get("ct") or 0) > 0])
        print("%-40s %7.2f %6d %9d %9s %s" % (
            dp.name[:40], max(g for _, g in gaps), len(big), nreb,
            "-", "reb_in_gap=%d res_changed_across_gap=%d" % (inside, straddle)))
