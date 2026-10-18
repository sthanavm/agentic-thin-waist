#!/usr/bin/env python3
"""Items 4+7: the full codec history per run, not just the last mime."""
import json
import pathlib
import re

sel = json.loads(pathlib.Path("/tmp/ladder_dirs.json").read_text())


def last_stat(d, app, key):
    p = pathlib.Path(d) / "qoe" / f"{app}_stats.jsonl"
    val = None
    if not p.exists():
        return None
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip() or '"meta"' in line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        st = r.get("stats") or {}
        if st.get(key) is not None:
            val = st[key]
    return val


def short(m):
    mm = re.search(r"(av01[\w.]*|vp0?9[\w.]*|avc1[\w.]*|opus|mp4a[\w.]*)", str(m))
    return mm.group(1) if mm else str(m)


for app in ("youtube", "vimeo"):
    for bw in (0.5, 1, 1.5, 3, 6, 10):
        key = f"{app}_{bw:g}"
        if key not in sel:
            continue
        d = sel[key]
        log = last_stat(d, app, "mse_mime_switch_log")
        codec = last_stat(d, app, "codec")
        hist = []
        if isinstance(log, dict):
            for sbkey, mimes in log.items():
                hist.append("%s: %s" % (sbkey[:5], " -> ".join(short(m) for m in mimes)))
        elif isinstance(log, list):
            hist = [short(m) for m in log]
        print("%-7s %-5s codec=%-26s switch_log=%s" % (
            app, f"{bw:g}M", short(codec)[:26], ("; ".join(hist) if hist else "(none recorded)")))
