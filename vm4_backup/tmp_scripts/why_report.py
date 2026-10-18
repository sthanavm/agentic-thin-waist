#!/usr/bin/env python3
"""Per run and per app: the freeze, and what the whole host was doing in it.

Answers, from measurement rather than inference:
  * did this app freeze, when in the run, and for how long;
  * renderer CPU over the first 60 s against steady state, because an app that
    is still starting up is not comparable with one that has settled;
  * long tasks, and whether a long task covers the freeze;
  * whether an ad was on screen during the window;
  * which processes on the whole host -- not only Chrome -- held the CPU while
    the page was stalled, with context switches and memory.
"""
import glob
import json
import os
import statistics
import sys

BAR = 2.0


def jr(p):
    out = []
    for line in open(p):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def page_to_wall(ss):
    pts = [
        ((r.get("stats") or {}).get("page_now_ms"), r["timestamp"])
        for r in ss
        if (r.get("stats") or {}).get("page_now_ms")
    ]
    if len(pts) < 2:
        return None
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    if x1 == x0:
        return None
    sl = (y1 - y0) / ((x1 - x0) / 1000.0)
    return lambda ps: y0 + (ps - x0 / 1000.0) * sl


def host_window(hp, t0, t1, topn=6):
    if not (hp and os.path.exists(hp)):
        return None
    rows = [r for r in jr(hp) if t0 <= r["t"] <= t1]
    if not rows:
        return None
    span = max(t1 - t0, 1e-6)
    by = {}
    for r in rows:
        for pid, p in (r.get("procs") or {}).items():
            k = "%s[%s]" % (p["c"], pid)
            d = by.setdefault(k, {"cpu": 0.0, "nctx": 0, "vctx": 0})
            d["cpu"] += p["cpu_ms"]
            d["nctx"] += p.get("nctx", 0)
            d["vctx"] += p.get("vctx", 0)
    top = sorted(by.items(), key=lambda kv: -kv[1]["cpu"])[:topn]
    load = [r["load1"] for r in rows if "load1" in r]
    mem = [r["mem"].get("MemAvailable") for r in rows if r.get("mem", {}).get("MemAvailable")]
    ctx = [r.get("ctxt_delta", 0) for r in rows]
    run = [r.get("running") or 0 for r in rows]
    return {
        "top": [(k, round(v["cpu"] / span, 1), v["nctx"]) for k, v in top],
        "load1": round(statistics.mean(load), 2) if load else None,
        "mem_avail_mb": int(statistics.mean(mem)) if mem else None,
        "ctxt_per_s": int(statistics.mean(ctx) / max(0.5, 0.5)) if ctx else None,
        "runnable": round(statistics.mean(run), 1) if run else None,
        "samples": len(rows),
    }


def renderer_cpu(hp, t0, t1):
    if not (hp and os.path.exists(hp)):
        return None
    tot = 0.0
    rows = [r for r in jr(hp) if t0 <= r["t"] <= t1]
    for r in rows:
        for _pid, p in (r.get("procs") or {}).items():
            if "chrome" in p["c"].lower():
                tot += p["cpu_ms"]
    return round(tot / max(t1 - t0, 1e-6), 1) if rows else None


def main(spec):
    for exp, trial, d, hp in spec:
        print("== %s trial %s  (%s)" % (exp, trial, os.path.basename(d)[:48]))
        for sp in sorted(glob.glob(os.path.join(d, "qoe", "*_stats.jsonl"))):
            app = os.path.basename(sp).replace("_stats.jsonl", "")
            ss = [r for r in jr(sp) if isinstance(r.get("timestamp"), (int, float))]
            if len(ss) < 3:
                continue
            ts = [r["timestamp"] for r in ss]
            t0 = ts[0]
            gaps = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
            wi = max(range(len(gaps)), key=lambda i: gaps[i])
            iv = statistics.median(gaps)
            bar = BAR if iv < 1.5 else 2 * round(iv)
            early = renderer_cpu(hp, t0, t0 + 60)
            steady = renderer_cpu(hp, t0 + 90, ts[-1])
            ipf = os.path.join(d, "qoe", "%s_inpage.json" % app)
            pg = lt = longest = ads = pe = "-"
            if os.path.exists(ipf):
                j = json.load(open(ipf))
                rep = j["stall_report"]
                pg = "%.2f" % (rep["max_gap_ms"] / 1000.0)
                lt = rep.get("longtasks", 0)
                longest = "%.0f" % rep.get("longest_task_ms", 0)
                ev = j.get("events") or []
                ads = sum(1 for e in ev if e.get("ad") == 1)
                ci = [e for e in ev if e.get("k") == "codecinfo" and e.get("sup")]
                pe = ("hw" if any(e.get("pe") for e in ci) else "sw") if ci else "-"
            print(
                "   %-8s B1=%-4s max_gap=%6.2fs at t=%5.1fs | page_gap=%6ss longtask=%-4s "
                "longest=%6sms | rendCPU 0-60s=%-7s steady=%-7s | decode=%s ads=%s"
                % (app, "PASS" if max(gaps) <= bar else "FAIL", max(gaps), ts[wi] - t0,
                   pg, lt, longest, early, steady, pe, ads)
            )
            if max(gaps) > bar:
                hw = host_window(hp, ts[wi], ts[wi + 1])
                if hw:
                    print("            host during the gap: load1=%s runnable=%s ctxt/s=%s mem_avail=%sMB"
                          % (hw["load1"], hw["runnable"], hw["ctxt_per_s"], hw["mem_avail_mb"]))
                    for k, cpu, nctx in hw["top"]:
                        print("              %-34s %7.1f ms/s  involuntary_ctx=%d" % (k, cpu, nctx))


if __name__ == "__main__":
    main(json.load(open(sys.argv[1])))
