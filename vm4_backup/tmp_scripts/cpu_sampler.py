#!/usr/bin/env python3
"""Per-process CPU during a cell, attributed to the thing that owns the process.

Vimeo at 10Mbps beside Zoom hit a decode ceiling on this 2-vCPU host: it held
1440x1080 but advanced only 95.5s of content in 142s. Whether that is the link
or the host is not arguable from QoE numbers alone, so measure the host.

Attribution is read from /proc, not guessed:
  * the container a process belongs to comes from /proc/<pid>/cgroup;
  * which app a collector Chrome serves comes from DISPLAY in
    /proc/<pid>/environ, because each job gets its own Xvfb display (:99 for the
    first app, :100 for the second) and Chrome inherits it.

Usage: cpu_sampler.py <out.jsonl> <seconds> [interval] [display_map_json]
  display_map_json e.g. '{"99":"zoom","100":"vimeo"}'
"""

import json
import os
import pathlib
import subprocess
import sys
import time

CLK = os.sysconf("SC_CLK_TCK")
NCPU = os.cpu_count() or 1


def container_names() -> dict[str, str]:
    """docker id prefix -> container name."""
    out = {}
    try:
        r = subprocess.run(
            ["docker", "ps", "--format", "{{.ID}} {{.Names}}"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2:
                out[parts[0]] = parts[1]
    except Exception:
        pass
    return out


def read_proc(pid: str):
    """(comm, ticks, cgroup, environ, ppid) or None."""
    base = pathlib.Path("/proc") / pid
    try:
        stat = (base / "stat").read_text()
        # comm can contain spaces and parens; fields after ')' are stable
        close = stat.rindex(")")
        comm = stat[stat.index("(") + 1 : close]
        fields = stat[close + 2 :].split()
        ticks = int(fields[11]) + int(fields[12])  # utime, stime
        ppid = fields[1]  # field 4 of /proc/pid/stat, 0-based here
    except Exception:
        return None
    try:
        cg = (base / "cgroup").read_text()
    except Exception:
        cg = ""
    env = ""
    try:
        env = (base / "environ").read_bytes().decode("utf8", "replace")
    except Exception:
        pass
    return comm, ticks, cg, env, ppid


def bucket(comm: str, cg: str, disp: str, names: dict[str, str], dmap: dict) -> str:
    """Which measured component owns this process.

    DISPLAY is checked FIRST and the container map second. The map is rebuilt
    every snapshot, but the collector container is created after the sampler
    starts, so an early-bound map misses it entirely - which is exactly why the
    first sweep reported peer CPU and nothing for either collector. DISPLAY is
    set per job (:99 for the first app, :100 for the second) and inherited by
    Chrome, so it identifies a collector browser without the container needing
    to be known yet.

    Browserless also runs ~180 `chrome-headless` processes on this host with no
    DISPLAY, belonging to no measured component; they must not be counted.
    """
    cname = ""
    for cid, nm in names.items():
        if cid in cg:
            cname = nm
            break
    low = comm.lower()
    chrome = "chrome" in low or "chromium" in low
    # The peer is identified by its container: it drives its own display and
    # must never be confused with a collector browser.
    if "peer" in cname:
        return "peer_chrome" if chrome else "peer_other"
    app = dmap.get(disp)
    if app:
        return f"collector_chrome_{app}" if chrome else f"collector_other_{app}"
    if chrome and "headless" in low:
        return ""  # browserless pool, not part of this measurement
    if cname and chrome:
        return "collector_chrome_unknown"
    return ""


def snapshot(names, dmap):
    """Per-bucket CPU ticks, with Chrome's children attributed to their parent.

    Chrome forks renderer and GPU processes that do the actual decoding, and
    those children do NOT carry DISPLAY in their environ - only the browser
    process launched under Xvfb does. Attributing on each process's own environ
    therefore credited ~1.6% of a core to a collector while 146.6% sat in an
    "unknown" bucket, which made the per-app split useless precisely where it
    mattered. Walking the ppid chain to the nearest ancestor that does have
    DISPLAY puts the children where they belong.
    """
    procs = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        got = read_proc(pid)
        if got:
            procs[pid] = got

    disp_cache: dict[str, str] = {}

    def display_of(pid, depth=0):
        if pid in disp_cache:
            return disp_cache[pid]
        if depth > 12 or pid not in procs:
            return ""
        comm, ticks, cg, env, ppid = procs[pid]
        own = ""
        for part in env.split("\x00"):
            if part.startswith("DISPLAY="):
                own = part.split("=", 1)[1].lstrip(":")
                break
        if not own and ppid and ppid != "0" and ppid != pid:
            own = display_of(ppid, depth + 1)
        disp_cache[pid] = own
        return own

    acc, counts = {}, {}
    for pid, (comm, ticks, cg, env, ppid) in procs.items():
        b = bucket(comm, cg, display_of(pid), names, dmap)
        if not b:
            continue
        acc[b] = acc.get(b, 0) + ticks
        counts[b] = counts.get(b, 0) + 1
    return acc, counts


def main() -> int:
    out_path = pathlib.Path(sys.argv[1])
    duration = float(sys.argv[2])
    interval = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
    dmap = (
        json.loads(sys.argv[4]) if len(sys.argv) > 4 else {"99": "zoom", "100": "vimeo"}
    )
    names = container_names()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prev, prev_t = snapshot(names, dmap)[0], time.time()
    end = time.time() + duration
    with out_path.open("w") as fh:
        fh.write(
            json.dumps(
                {
                    "record": "meta",
                    "ncpu": NCPU,
                    "clk_tck": CLK,
                    "display_map": dmap,
                    "started": prev_t,
                }
            )
            + "\n"
        )
        while time.time() < end:
            time.sleep(interval)
            names = container_names()  # the collector container appears late
            cur, counts = snapshot(names, dmap)
            now = time.time()
            dt = now - prev_t
            if dt <= 0:
                continue
            row = {
                "timestamp": now,
                "interval_s": round(dt, 2),
                "cpu_pct": {},
                "procs": counts,
            }
            for b, ticks in cur.items():
                d = ticks - prev.get(b, 0)
                if d < 0:
                    continue  # pid reuse / process gone
                # percent of ONE core, as `top` reports it
                row["cpu_pct"][b] = round(100.0 * (d / CLK) / dt, 1)
            row["cpu_pct_total_of_host"] = round(sum(row["cpu_pct"].values()) / NCPU, 1)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            prev, prev_t = cur, now
    return 0


if __name__ == "__main__":
    sys.exit(main())
