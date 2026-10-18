#!/usr/bin/env python3
"""B1 and the slowest stats call, per app, for every concurrent trial.

B1 is the pre-registered cadence bar: every inter-sample gap <= 2x the 1.0 s
interval. It is recomputed here from the samples themselves rather than copied
from summary.json, so the gap and the work that produced it are read off the
same rows. The 2.0 s bar is not changed anywhere in this file.
"""
import glob
import json
import os
import statistics
import sys

BAR = 2.0


def rows(path):
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def summarize(app, rows_in, gate):
    # Keep rows and timestamps in ONE list, so every index below refers to the
    # same row. Filtering the timestamps alone and then indexing the unfiltered
    # rows silently misattributes loop_ms to the wrong sample.
    ss = [s for s in rows_in if isinstance(s.get("timestamp"), (int, float))]
    dropped = len(rows_in) - len(ss)
    ts = [s["timestamp"] for s in ss]
    if len(ts) < 2:
        return None
    gaps = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
    sc = [(s.get("loop_ms") or {}).get("script") for s in ss]
    sc = [x for x in sc if isinstance(x, (int, float))]
    tw = [(s.get("loop_ms") or {}).get("total_work") for s in ss]
    tw = [x for x in tw if isinstance(x, (int, float))]
    l1 = [s.get("host_load1") for s in ss]
    l1 = [x for x in l1 if isinstance(x, (int, float))]
    worst = max(range(len(gaps)), key=lambda i: gaps[i])
    wlm = ss[worst].get("loop_ms") or {}
    return {
        "app": app,
        "n": len(ts),
        "dropped": dropped,
        "max_gap": max(gaps),
        "mean_gap": statistics.mean(gaps),
        "b1": "PASS" if max(gaps) <= BAR else "FAIL",
        "worst_i": worst,
        "worst_script": wlm.get("script"),
        "worst_work": wlm.get("total_work"),
        "worst_load1": ss[worst].get("host_load1"),
        "script_max": max(sc) if sc else None,
        "script_med": statistics.median(sc) if sc else None,
        "work_max": max(tw) if tw else None,
        "load1_mean": statistics.mean(l1) if l1 else None,
        "load1_max": max(l1) if l1 else None,
        "gate": gate,
    }


def main(dirs):
    print(
        "%-6s %-8s %4s %-5s %8s %8s %10s %10s %9s %9s %s"
        % (
            "trial",
            "app",
            "n",
            "B1",
            "max_gap",
            "mean_gap",
            "script_max",
            "script_med",
            "load1_av",
            "load1_mx",
            "gate(load1@start)",
        )
    )
    for d in dirs:
        trial = "t?"
        base = os.path.basename(d.rstrip("/"))
        for part in base.split("_"):
            if part.startswith("t") and part[1:].isdigit():
                trial = part
        gate = ""
        hc = os.path.join(d, "host_conditions.json")
        if os.path.exists(hc):
            h = json.load(open(hc))
            gate = "%s l1=%s wait=%ss" % (
                h.get("host_load_gate"),
                h.get("load1_at_start"),
                h.get("host_load_gate_wait_s"),
            )
        for sp in sorted(glob.glob(os.path.join(d, "**", "*_stats.jsonl"), recursive=True)):
            app = os.path.basename(sp).replace("_stats.jsonl", "")
            r = summarize(app, rows(sp), gate)
            if not r:
                continue
            print(
                "%-6s %-8s %4d %-5s %8.3f %8.3f %10s %10s %9s %9s %s"
                % (
                    trial,
                    r["app"],
                    r["n"],
                    r["b1"],
                    r["max_gap"],
                    r["mean_gap"],
                    "%.1f" % r["script_max"] if r["script_max"] is not None else "-",
                    "%.1f" % r["script_med"] if r["script_med"] is not None else "-",
                    "%.2f" % r["load1_mean"] if r["load1_mean"] is not None else "-",
                    "%.2f" % r["load1_max"] if r["load1_max"] is not None else "-",
                    r["gate"] + ("" if not r["dropped"] else "  [%d row(s) without timestamp]" % r["dropped"]),
                )
            )
            if r["b1"] == "FAIL" or r["max_gap"] > 1.5:
                print(
                    "        -> worst gap at i=%d: %.3f s = script %s ms + rest, "
                    "total_work %s ms, load1 %s"
                    % (
                        r["worst_i"],
                        r["max_gap"],
                        r["worst_script"],
                        r["worst_work"],
                        r["worst_load1"],
                    )
                )


if __name__ == "__main__":
    main(sys.argv[1:])
