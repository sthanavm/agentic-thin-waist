#!/usr/bin/env python3
"""Per run: B1, the page's own clock gaps, and whether the page itself blocked.

B1 is recomputed from the sampler's own rows against the unchanged 2.0 s bar.
The in-page figures come from the page's self-written log, which is the point:
a gap there means a timer the page could not run, which only happens when the
main thread is occupied. A read that timed out with NO gap in the page clock
would mean the opposite -- the page was fine and the read path was blocked.
"""
import glob
import json
import os
import statistics
import sys

BAR = 2.0


def jrows(path):
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


def main(dirs):
    hdr = (
        "%-5s %-8s %5s %-5s %8s | %6s %9s %7s | %6s %7s | %s"
        % ("trial", "app", "n", "B1", "max_gap", "ticks", "page_gap", "stalls",
           "reads", "timeout", "page blocked?")
    )
    print(hdr)
    print("-" * len(hdr))
    for d in dirs:
        trial = "?"
        for part in os.path.basename(d.rstrip("/")).split("_"):
            if part.startswith("t") and part[1:].isdigit():
                trial = part
        for app in ("youtube", "vimeo"):
            sp = os.path.join(d, "qoe", "%s_stats.jsonl" % app)
            ip = os.path.join(d, "qoe", "%s_inpage.json" % app)
            if not os.path.exists(sp):
                continue
            ss = [r for r in jrows(sp) if isinstance(r.get("timestamp"), (int, float))]
            ts = [r["timestamp"] for r in ss]
            gaps = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)] or [0]
            b1 = "PASS" if max(gaps) <= BAR else "FAIL"
            ticks = pgap = nst = reads = tmo = "-"
            blocked = "no in-page log"
            if os.path.exists(ip):
                j = json.load(open(ip))
                rep = j.get("stall_report") or {}
                rd = j.get("reads") or {}
                ticks = rep.get("ticks", 0)
                pgap = rep.get("max_gap_ms", 0)
                nst = len(rep.get("stalls") or [])
                reads = rd.get("reads", 0)
                tmo = rd.get("timeouts", 0)
                tick_ms = j.get("tick_ms", 200)
                # The page is called blocked only if its own timer missed by
                # more than a sampling interval; timer jitter is not a stall.
                blocked = "YES %.1f s" % (pgap / 1000.0) if pgap > 1000 else "no"
            print(
                "%-5s %-8s %5d %-5s %8.3f | %6s %9s %7s | %6s %7s | %s"
                % (trial, app, len(ss), b1, max(gaps), ticks,
                   ("%.0f ms" % pgap) if pgap != "-" else "-", nst, reads, tmo, blocked)
            )


if __name__ == "__main__":
    main(sys.argv[1:])
