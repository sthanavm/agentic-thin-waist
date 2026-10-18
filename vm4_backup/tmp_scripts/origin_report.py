#!/usr/bin/env python3
"""Per run: B1, in-page timer gap, renderer CPU in the longest gap, host load.

The in-page figure exists only where the in-page log was enabled (condition A).
For B, C and D it is reported as "off", because the condition is defined by
having that instrumentation removed -- not as 0, which would read as "no gap".
"""
import glob
import json
import os
import statistics
import sys

BAR = 2.0


def jrows(p):
    out = []
    for line in open(p):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def cpu_during(probe, t0, t1):
    tot = 0.0
    load = []
    n = 0
    if not (probe and os.path.exists(probe)):
        return None, None
    for r in jrows(probe):
        if t0 <= r["t"] <= t1:
            n += 1
            if "load1" in r:
                load.append(r["load1"])
            for _pid, p in (r.get("procs") or {}).items():
                if p["k"] == "renderer":
                    tot += p["cpu_ms"]
    if not n:
        return None, None
    return round(tot / max(t1 - t0, 1e-6), 1), (
        round(statistics.mean(load), 2) if load else None
    )


def main(spec):
    print(
        "%-4s %-3s %-8s %5s %-5s %9s %10s %12s %9s"
        % ("cond", "t", "app", "n", "B1", "max_gap", "inpage_gap", "rend_cpu_ms/s", "load1")
    )
    for cond, trial, d, probe in spec:
        for sp in sorted(glob.glob(os.path.join(d, "qoe", "*_stats.jsonl"))):
            app = os.path.basename(sp).replace("_stats.jsonl", "")
            ss = [r for r in jrows(sp) if isinstance(r.get("timestamp"), (int, float))]
            if len(ss) < 3:
                continue
            ts = [r["timestamp"] for r in ss]
            gaps = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
            wi = max(range(len(gaps)), key=lambda i: gaps[i])
            cpu, load = cpu_during(probe, ts[wi], ts[wi + 1])
            ipf = os.path.join(d, "qoe", "%s_inpage.json" % app)
            if os.path.exists(ipf):
                ip = "%.2f s" % (json.load(open(ipf))["stall_report"]["max_gap_ms"] / 1000.0)
            else:
                ip = "off"
            # B1 is defined against the run's own interval; D samples at 2 s and
            # its bar is therefore 4 s, not 2 s. Report both honestly.
            iv = statistics.median(gaps)
            bar = BAR if iv < 1.5 else 2 * round(iv)
            print(
                "%-4s %-3s %-8s %5d %-5s %9.3f %10s %12s %9s"
                % (cond, trial, app, len(ss),
                   "PASS" if max(gaps) <= bar else "FAIL",
                   max(gaps), ip,
                   ("%.1f" % cpu) if cpu is not None else "-",
                   ("%.2f" % load) if load is not None else "-")
            )


if __name__ == "__main__":
    main(json.load(open(sys.argv[1])))
