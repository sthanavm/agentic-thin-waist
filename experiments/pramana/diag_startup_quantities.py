#!/usr/bin/env python3
"""Item 2: every startup quantity, per rung, with its exact start point.

No capture is read, so this is safe to run while other runs are in flight.
The pcap leg of the cross-check is done separately in diag_startup_xcheck.py.
"""
import json
import pathlib

sel = json.loads(pathlib.Path("/tmp/ladder_dirs.json").read_text())


def last_sample(d, app):
    p = pathlib.Path(d) / "qoe" / f"{app}_stats.jsonl"
    out = None
    if not p.exists():
        return {}
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip() or '"meta"' in line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("stats"):
            out = r["stats"]
    return out or {}


print(
    "%-7s %-5s %9s %9s %9s %9s %9s  %s"
    % (
        "app",
        "rung",
        "legacy",
        "probe_t0",
        "fp_ms",
        "navStart",
        "initbuf",
        "legacy_basis",
    )
)
print(
    "%-7s %-5s %9s %9s %9s %9s %9s"
    % ("", "", "(artifact)", "offset", "stored", "=fp+t0", "(fill)")
)
for app in ("youtube", "vimeo"):
    for bw in (0.5, 1, 1.5, 3, 6, 10):
        key = f"{app}_{bw:g}"
        if key not in sel:
            continue
        d = sel[key]
        rec = json.loads((pathlib.Path(d) / "record.json").read_text())
        q = (rec.get("player_qoe") or {}).get(app) or {}
        u = q.get("uniform") or {}
        s = last_sample(d, app)
        fp = s.get("first_presentation_ms")
        t0 = s.get("probe_t0_page_ms")
        spans = s.get("waiting_spans") or []
        ib = sum(
            float(w.get("dur_ms") or 0)
            for w in spans
            if isinstance(w, dict) and float(w.get("ct") or 0) <= 0
        )
        nav = (fp + t0) if (fp is not None and t0 is not None) else None
        print(
            "%-7s %-5s %9s %9s %9s %9s %9.0f  %s"
            % (
                app,
                f"{bw:g}M",
                q.get("video_startup_time_ms"),
                t0,
                fp,
                nav,
                ib,
                q.get("startup_basis") or u.get("startup_basis") or "-",
            )
        )
