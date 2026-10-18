#!/usr/bin/env python3
"""Top functions by self time inside the freeze window, from a V8 CPU profile.

The profile is a sampling profile: each sample names the function on top of the
stack, so self time is just how many samples landed on a node. V8 emits
synthetic nodes -- "(garbage collector)", "(program)", "(idle)" -- which is how
GC is separated from script without a timeline trace.

What this cannot attribute: work outside V8. Layout, paint, raster and media
decode do not appear as named frames; they land in "(program)" or produce no
sample at all. So the verdict this supports is "script vs GC vs neither", and
"neither" is reported as such rather than guessed at.

Samples are located against the freeze window using the anchor recorded when
the profiler started (wall clock and performance.now together).
"""
import json
import os
import sys
from collections import defaultdict


def load(path):
    with open(path) as fh:
        return json.load(fh)


def window_self_times(prof, w0_ms, w1_ms):
    nodes = {n["id"]: n for n in prof.get("nodes") or []}
    samples = prof.get("samples") or []
    deltas = prof.get("timeDeltas") or []
    anchor = prof.get("_anchor") or {}
    p0 = anchor.get("page_ms_at_start")
    start_us = prof.get("startTime") or 0
    if p0 is None or not samples:
        return None, None, None
    self_us = defaultdict(float)
    total_in = 0.0
    t_us = start_us
    for i, sid in enumerate(samples):
        d = deltas[i] if i < len(deltas) else 0
        t_us += d
        page_ms = p0 + (t_us - start_us) / 1000.0
        if page_ms < w0_ms or page_ms > w1_ms:
            continue
        total_in += d
        n = nodes.get(sid)
        if not n:
            continue
        cf = n.get("callFrame") or {}
        name = cf.get("functionName") or "(anonymous)"
        url = cf.get("url") or ""
        self_us[(name, url)] += d
    return self_us, total_in, len(samples)


def classify(name):
    if name == "(garbage collector)":
        return "GC"
    if name in ("(program)", "(idle)", "(root)"):
        return "non-script"
    return "script"


def main(argv):
    run_dir, app = argv[0], argv[1]
    prof_p = os.path.join(run_dir, "qoe", "%s_profile.json" % app)
    ip_p = os.path.join(run_dir, "qoe", "%s_inpage.json" % app)
    if not (os.path.exists(prof_p) and os.path.exists(ip_p)):
        print("  missing profile or in-page log for %s" % app)
        return
    ip = load(ip_p)
    stalls = sorted(ip["stall_report"].get("stalls") or [], key=lambda x: -x["gap_ms"])
    if not stalls:
        print("  %s: no stall to analyse (max gap %.2f s)"
              % (app, ip["stall_report"]["max_gap_ms"] / 1000.0))
        return
    w = stalls[0]
    w0, w1 = w["at_page_ms"], w["at_page_ms"] + w["gap_ms"]
    prof = load(prof_p)
    self_us, total_in, nsamp = window_self_times(prof, w0, w1)
    if self_us is None:
        print("  %s: profile has no anchor/samples" % app)
        return
    print("  %s: freeze %.2f s at page %.1f s | %d profile samples in run, "
          "%.0f ms of profile time inside the window"
          % (app, w["gap_ms"] / 1000.0, w0 / 1000.0, nsamp, total_in / 1000.0))
    if total_in <= 0:
        print("    NO PROFILE SAMPLES INSIDE THE FREEZE WINDOW -- the profiler")
        print("    itself was not scheduled either, which is evidence the whole")
        print("    thread was off-CPU rather than running script.")
        return
    buckets = defaultdict(float)
    for (name, _url), us in self_us.items():
        buckets[classify(name)] += us
    print("    by kind: " + ", ".join(
        "%s %.0f ms (%.0f%%)" % (k, v / 1000.0, 100.0 * v / total_in)
        for k, v in sorted(buckets.items(), key=lambda kv: -kv[1])))
    top = sorted(self_us.items(), key=lambda kv: -kv[1])[:8]
    for (name, url), us in top:
        short = url.rsplit("/", 1)[-1][:46] if url else "-"
        print("      %7.0f ms  %-34s %s" % (us / 1000.0, name[:34], short))


if __name__ == "__main__":
    main(sys.argv[1:])
