#!/usr/bin/env python3
"""Item 4: actual video and audio byte totals per rung, with codec and itag.

Reads only record.json and the samples JSONL; no capture is touched, so this
costs nothing while runs are in flight. itag comes from YouTube's own
sfn_codecs string, which carries it in parentheses; Vimeo has no itag.
"""
import json
import pathlib
import re
import sys

sel = json.loads(pathlib.Path("/tmp/ladder_dirs.json").read_text())


def samples(d, app):
    p = pathlib.Path(d) / "qoe" / f"{app}_stats.jsonl"
    out = []
    if not p.exists():
        return out
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip() or '"meta"' in line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        st = r.get("stats")
        if st:
            out.append(st)
    return out


def itags(st):
    seen = []
    for s in st:
        c = s.get("sfn_codecs")
        if not c:
            continue
        for m in re.findall(r"\((\d+)\)", str(c)):
            if m not in seen:
                seen.append(m)
    return seen


print(
    "%-7s %-5s %13s %13s %7s %7s  %-24s %-14s %s"
    % (
        "app",
        "rung",
        "video_bytes",
        "audio_bytes",
        "v/total",
        "aud/vid",
        "video codec",
        "audio codec",
        "itags",
    )
)
for app in ("youtube", "vimeo"):
    for bw in (0.5, 1, 1.5, 3, 6, 10):
        key = f"{app}_{bw:g}"
        if key not in sel:
            continue
        d = pathlib.Path(sel[key])
        rec = json.loads((d / "record.json").read_text())
        q = (rec.get("player_qoe") or {}).get(app) or {}
        u = q.get("uniform") or {}
        v = u.get("mse_video_bytes")
        a = u.get("mse_audio_bytes")
        st = samples(d, app)
        vm = u.get("mse_video_mime") or ""
        am = u.get("mse_audio_mime") or ""

        def cod(m):
            mm = re.search(r'codecs="?([^";]+)', m)
            return mm.group(1) if mm else (m or "-")

        tot = (v or 0) + (a or 0)
        print(
            "%-7s %-5s %13s %13s %7s %7s  %-24s %-14s %s"
            % (
                app,
                f"{bw:g}M",
                v,
                a,
                ("%.3f" % (v / tot)) if tot and v else "-",
                ("%.3f" % (a / v)) if v and a else "-",
                cod(vm)[:24],
                cod(am)[:14],
                ",".join(itags(st)) or "-",
            )
        )
