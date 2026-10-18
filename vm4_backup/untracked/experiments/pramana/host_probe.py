#!/usr/bin/env python3
"""Whole-host sampling: who is on the CPU, and how hard is the box swapping.

cpu_probe.py only watched Chrome, which cannot answer "who took the CPU while
YouTube's page was stalled" -- the answer might be Xvfb, tcpdump, chromedriver,
the collector itself, or something else entirely. This records every process
that burned CPU in each interval, plus context switches, memory and swap, so
the freeze window can be looked up and the machine's whole state read off.

Reads /proc only. It does not signal, pin, or otherwise touch any process.
"""

import json
import os
import sys
import time

HZ = os.sysconf("SC_CLK_TCK")


def meminfo():
    out = {}
    try:
        for line in open("/proc/meminfo"):
            k, _, v = line.partition(":")
            if k in ("MemAvailable", "MemFree", "SwapFree", "SwapTotal", "Cached"):
                out[k] = int(v.split()[0]) // 1024  # MiB
    except Exception:
        pass
    return out


def sys_stat():
    """System-wide context switches and per-state CPU jiffies."""
    out = {}
    try:
        for line in open("/proc/stat"):
            if line.startswith("ctxt "):
                out["ctxt"] = int(line.split()[1])
            elif line.startswith("procs_running"):
                out["running"] = int(line.split()[1])
            elif line.startswith("procs_blocked"):
                out["blocked"] = int(line.split()[1])
            elif line.startswith("cpu "):
                f = [int(x) for x in line.split()[1:9]]
                out["cpu_jiffies"] = {
                    "user": f[0],
                    "nice": f[1],
                    "sys": f[2],
                    "idle": f[3],
                    "iowait": f[4],
                    "irq": f[5],
                    "softirq": f[6],
                    "steal": f[7],
                }
    except Exception:
        pass
    return out


def proc_snapshot():
    """pid -> (comm, ticks, state, vol_ctx, nonvol_ctx, rss_mb)."""
    snap = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % pid) as fh:
                raw = fh.read()
            head, _, rest = raw.partition("(")
            comm, _, tail = rest.rpartition(")")
            parts = tail.split()
            state = parts[0]
            ticks = int(parts[11]) + int(parts[12])
            rss = int(parts[21]) * (os.sysconf("SC_PAGE_SIZE") // 1024) // 1024
            vol = nonvol = 0
            try:
                for line in open("/proc/%s/status" % pid):
                    if line.startswith("voluntary_ctxt_switches"):
                        vol = int(line.split()[1])
                    elif line.startswith("nonvoluntary_ctxt_switches"):
                        nonvol = int(line.split()[1])
            except Exception:
                pass
            snap[pid] = (comm, ticks, state, vol, nonvol, rss)
        except Exception:
            continue
    return snap


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/host_probe.jsonl"
    interval = float(os.environ.get("PROBE_INTERVAL_S", "0.5"))
    duration = float(os.environ.get("PROBE_DURATION_S", "400"))
    # Below this many CPU ms in an interval a process is not worth a row.
    floor_ms = float(os.environ.get("PROBE_FLOOR_MS", "5"))
    end = time.time() + duration
    prev = proc_snapshot()
    prev_sys = sys_stat()
    with open(out_path, "a", buffering=1) as fh:
        while time.time() < end:
            t0 = time.time()
            cur = proc_snapshot()
            cs = sys_stat()
            row = {"t": round(t0, 3), "mem": meminfo()}
            try:
                with open("/proc/loadavg") as lf:
                    p = lf.read().split()
                    row["load1"] = float(p[0])
                    row["runnable"] = p[3]
            except Exception:
                pass
            row["ctxt_delta"] = cs.get("ctxt", 0) - prev_sys.get("ctxt", 0)
            row["running"] = cs.get("running")
            row["blocked"] = cs.get("blocked")
            a, b = prev_sys.get("cpu_jiffies") or {}, cs.get("cpu_jiffies") or {}
            if a and b:
                row["cpu_pct"] = {
                    k: round(
                        (b[k] - a[k]) * 1000.0 / HZ / max(t0 - (t0 - interval), 1e-6), 1
                    )
                    for k in b
                }
            procs = {}
            for pid, (comm, ticks, state, vol, nonvol, rss) in cur.items():
                p0 = prev.get(pid)
                if not p0:
                    continue
                dms = (ticks - p0[1]) * 1000.0 / HZ
                if dms < floor_ms:
                    continue
                procs[pid] = {
                    "c": comm,
                    "cpu_ms": round(dms, 1),
                    "st": state,
                    "rss": rss,
                    "vctx": vol - p0[3],
                    "nctx": nonvol - p0[4],
                }
            row["procs"] = procs
            fh.write(json.dumps(row) + "\n")
            prev, prev_sys = cur, cs
            time.sleep(max(0.0, interval - (time.time() - t0)))


if __name__ == "__main__":
    main()
