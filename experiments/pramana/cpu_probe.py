#!/usr/bin/env python3
"""Per-process CPU sampling, to tell a busy renderer from a starved one.

The in-page log says WHEN the page's main thread stopped. It cannot say why.
Two very different causes produce the same gap:

  * the thread was running something for the whole window -- it should have
    burned CPU, and a long task should cover the window;
  * the thread was runnable but not scheduled -- it burned almost no CPU, and
    the long task (if any) is only long because wall time passed while it was
    off-CPU.

This samples utime+stime per Chrome process from /proc, with wall timestamps,
so the freeze window can be looked up afterwards and the two told apart. It
reads /proc only; it does not touch the processes.
"""

import json
import os
import sys
import time

HZ = os.sysconf("SC_CLK_TCK")


def chrome_procs():
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
            if "chrome" not in cmd.lower():
                continue
            kind = "browser"
            if "--type=renderer" in cmd:
                kind = "renderer"
            elif "--type=gpu-process" in cmd:
                kind = "gpu"
            elif "--type=" in cmd:
                kind = "other"
            # Which app this belongs to, via its display.
            disp = None
            for tok in cmd.split():
                if tok.startswith("--display="):
                    disp = tok.split("=", 1)[1]
            out[pid] = {"kind": kind, "display": disp}
        except Exception:
            continue
    return out


def cpu_ticks(pid):
    try:
        with open("/proc/%s/stat" % pid) as fh:
            parts = fh.read().rsplit(")", 1)[1].split()
        # after the comm field: state is parts[0]; utime/stime are fields 14/15
        return int(parts[11]) + int(parts[12]), parts[0]
    except Exception:
        return None, None


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/cpu_probe.jsonl"
    interval = float(os.environ.get("PROBE_INTERVAL_S", "0.2"))
    duration = float(os.environ.get("PROBE_DURATION_S", "300"))
    end = time.time() + duration
    prev = {}
    procs = chrome_procs()
    last_rescan = time.time()
    with open(out_path, "a", buffering=1) as fh:
        while time.time() < end:
            t = time.time()
            if t - last_rescan > 5:
                procs = chrome_procs()
                last_rescan = t
            row = {"t": round(t, 3), "procs": {}}
            try:
                with open("/proc/loadavg") as lf:
                    row["load1"] = float(lf.read().split()[0])
            except Exception:
                pass
            for pid, meta in procs.items():
                ticks, state = cpu_ticks(pid)
                if ticks is None:
                    continue
                p0 = prev.get(pid)
                prev[pid] = ticks
                if p0 is None:
                    continue
                row["procs"][pid] = {
                    "k": meta["kind"],
                    "d": meta["display"],
                    "cpu_ms": round((ticks - p0) * 1000.0 / HZ, 1),
                    "st": state,
                }
            fh.write(json.dumps(row) + "\n")
            time.sleep(max(0.0, interval - (time.time() - t)))


if __name__ == "__main__":
    main()
