#!/usr/bin/env python3
"""Join each freeze to what the host and the page were doing at that moment.

For every run: the page's worst clock gap, whether a long task covers it, and
how much CPU the Chrome renderers actually got during the window. A freeze with
a covering long task AND high CPU is a page doing work. A freeze with little
CPU is a page that was not being scheduled. The two call for opposite fixes, so
the distinction is the whole point of the probe.
"""
import glob
import json
import os
import sys


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


def page_to_wall(stats_rows):
    """Linear map from the page clock to wall time, from paired readings."""
    pts = []
    for r in stats_rows:
        pn = (r.get("stats") or {}).get("page_now_ms")
        if isinstance(pn, (int, float)) and isinstance(r.get("timestamp"), (int, float)):
            pts.append((pn / 1000.0, r["timestamp"]))
    if len(pts) < 2:
        return None
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    if x1 == x0:
        return None
    slope = (y1 - y0) / (x1 - x0)
    return lambda ps: y0 + (ps - x0) * slope


def cpu_in_window(probe_rows, t0, t1):
    """CPU ms per wall second, summed over renderers, inside [t0, t1]."""
    tot = {}
    n = 0
    for r in probe_rows:
        if not (t0 <= r["t"] <= t1):
            continue
        n += 1
        for pid, p in (r.get("procs") or {}).items():
            tot[p["k"]] = tot.get(p["k"], 0.0) + p["cpu_ms"]
    span = max(t1 - t0, 1e-6)
    return {k: round(v / span, 1) for k, v in sorted(tot.items())}, n


def main(dirs):
    for d in dirs:
        base = os.path.basename(d.rstrip("/"))
        print("== %s" % base)
        probe = None
        for cand in glob.glob("/tmp/cpu_probe_*.jsonl"):
            probe = probe  # resolved per-run below by the caller's pairing
        for ipf in sorted(glob.glob(os.path.join(d, "qoe", "*_inpage.json"))):
            app = os.path.basename(ipf).replace("_inpage.json", "")
            j = json.load(open(ipf))
            rep = j["stall_report"]
            sp = os.path.join(d, "qoe", "%s_stats.jsonl" % app)
            ss = [r for r in jrows(sp) if isinstance(r.get("timestamp"), (int, float))]
            ts = [r["timestamp"] for r in ss]
            gaps = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)] or [0]
            stalls = sorted(rep.get("stalls") or [], key=lambda g: -g["gap_ms"])
            worst = stalls[0] if stalls else None
            print(
                "   %-8s B1=%-4s sampler_gap=%6.2fs  page_gap=%7.2fs  stalls=%-3d "
                "longtasks=%-4s longest=%.0fms"
                % (
                    app,
                    "PASS" if max(gaps) <= 2.0 else "FAIL",
                    max(gaps),
                    rep["max_gap_ms"] / 1000.0,
                    len(rep.get("stalls") or []),
                    rep.get("longtasks", "-"),
                    rep.get("longest_task_ms", 0),
                )
            )
            if worst:
                lt = [
                    x
                    for x in (rep.get("top_longtasks") or [])
                    if x["at_page_ms"] is not None
                    and worst["at_page_ms"] <= x["at_page_ms"] <= worst["at_page_ms"] + worst["gap_ms"]
                ]
                print(
                    "            worst freeze at page %.1fs for %.2fs; long task(s) inside it: %s"
                    % (
                        worst["at_page_ms"] / 1000.0,
                        worst["gap_ms"] / 1000.0,
                        ([round(x["dur_ms"]) for x in lt] or "NONE"),
                    )
                )
                f = page_to_wall(ss)
                pr = os.environ.get("PROBE_FILE")
                if f and pr and os.path.exists(pr):
                    w0 = f(worst["at_page_ms"] / 1000.0)
                    w1 = f((worst["at_page_ms"] + worst["gap_ms"]) / 1000.0)
                    cpu, n = cpu_in_window(jrows(pr), w0, w1)
                    print(
                        "            during it: CPU ms/s by type %s  (%d probe samples)"
                        % (cpu, n)
                    )


if __name__ == "__main__":
    main(sys.argv[1:])
