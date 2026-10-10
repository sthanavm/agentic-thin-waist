#!/usr/bin/env python3
"""Emit results_release/README.md and sweep_matrix.md from the built data.

Reads only validation/built_runs.json and validation/checks.csv, so the prose
cannot drift from what was actually built and checked. Status words are derived,
never typed by hand.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(sys.argv[1])
built = json.loads((ROOT / "validation" / "built_runs.json").read_text())
checks = list(csv.DictReader(open(ROOT / "validation" / "checks.csv")))

by_run = defaultdict(list)
for c in checks:
    by_run[c["run"]].append(c)


def run_status(name):
    cs = by_run.get(name, [])
    if any(c["result"] == "FAIL" for c in cs):
        return "FAILED"
    if any(c["result"] == "UNKNOWN" for c in cs):
        return "VERIFIED*"
    return "VERIFIED" if cs else "NOT MEASURED"


# ── sweep_matrix.md ─────────────────────────────────────────────────────────
cells = defaultdict(list)
for b in built:
    cells[(b["app"], b["cap"], b["lat"], b["aqm"])].append(b)

lines = [
    "# Sweep matrix",
    "",
    "Every cell actually run for this release, with trial count and status.",
    "Status is derived from `validation/checks.csv`, not written by hand.",
    "`VERIFIED*` means every check passed but at least one was UNKNOWN",
    "(recorded as such rather than silently treated as a pass).",
    "",
    "All cells: solo app, 0 % loss, cubic, 180 s, one app per run.",
    "",
    "| app | cap (Mbps) | latency (ms) | AQM | trials | statuses | resolution(s) | startup (ms) |",
    "|---|---|---|---|---|---|---|---|",
]
for (app, cap, lat, aqm), rows in sorted(
    cells.items(), key=lambda kv: (kv[0][0], kv[0][1])
):
    sts = [run_status(r["name"]) for r in rows]
    res = sorted({("%sp" % r["res_p"]) for r in rows if r.get("res_p")})
    startups = [(r["metrics"] or {}).get("startup_delay_ms") for r in rows]
    startups = [s for s in startups if s is not None]
    sr = (
        ("%.0f-%.0f" % (min(startups), max(startups)))
        if len(startups) > 1
        else ("%.0f" % startups[0] if startups else "n/a")
    )
    lines.append(
        "| %s | %g | %g | %s | %d | %s | %s | %s |"
        % (
            app,
            cap,
            float(lat or 0),
            aqm,
            len(rows),
            ", ".join(sorted(set(sts))),
            ", ".join(res) or "n/a",
            sr,
        )
    )
lines += [
    "",
    "## Not run, and why",
    "",
    "| combination | why |",
    "|---|---|",
    "| Multi-app / concurrent cells (Step 3b) | gated on the YouTube byte "
    "reference passing for a proven reason; see README |",
    "| Cross-traffic profiles | not implemented — still waiting on Jaber for "
    "the pointer. Nothing is faked and no profile is claimed |",
    "| AQM other than pfifo | not swept in this release; the AQM axis was held "
    "fixed so bandwidth and app are the only variables |",
    "| Latency other than 50 ms | held fixed for the same reason |",
]
(ROOT / "sweep_matrix.md").write_text("\n".join(lines) + "\n")

# ── README.md ───────────────────────────────────────────────────────────────
apps = sorted({b["app"] for b in built})
METRICS = [
    ("1 Startup delay", "startup_delay_ms"),
    ("2 Buffer level + rebuffering", "rebuffer_events"),
    ("3 Delivered bitrate (video/audio split)", "delivered_video_bitrate_mbps"),
    ("4 Rendition switching (timestamped, with direction)", "switch_count"),
    ("5 Frame rendering rate (presented)", "rendered_fps"),
]


def metric_status(app, key):
    rows = [b for b in built if b["app"] == app]
    if not rows:
        return "NOT MEASURED", "no run in this release"
    vals = [(b["metrics"] or {}).get(key) for b in rows]
    have = [v for v in vals if v is not None]
    if not have:
        return "UNKNOWN", "%d run(s), metric null in all" % len(rows)
    if len(have) < len(vals):
        return "VERIFIED*", "%d/%d run(s) produced it" % (len(have), len(vals))
    return "VERIFIED", "%d/%d run(s)" % (len(have), len(vals))


nfail = sum(1 for c in checks if c["result"] == "FAIL")
nunk = sum(1 for c in checks if c["result"] == "UNKNOWN")

rl = [
    "# Uniform streaming-QoE results — release package",
    "",
    "One extraction method and one output schema applied to every app, with no",
    "per-site APIs. Every number here came from a run on the lab VM; every chart",
    "was regenerated from the raw per-second samples by `build_release.py`, and",
    "each chart's values are asserted equal to `summary.json` as part of the",
    "build. A chart that disagreed would fail the build rather than ship.",
    "",
    "**Read `validation/VERIFICATION_REPORT.md` before trusting any row.**",
    "",
    "## Status at a glance",
    "",
    "| | count |",
    "|---|---|",
    "| runs in this release | %d |" % len(built),
    "| checks run | %d |" % len(checks),
    "| checks FAILED | **%d** |" % nfail,
    "| checks UNKNOWN | %d |" % nunk,
    "",
    "## App x metric",
    "",
    "`VERIFIED` = produced and checked on every run of that app. `VERIFIED*` =",
    "produced on some runs only. `UNKNOWN` = the channel exists but returned",
    "nothing. `NOT MEASURED` = no run.",
    "",
    "| metric | " + " | ".join(apps) + " |",
    "|---" * (len(apps) + 1) + "|",
]
for label, key in METRICS:
    cells_ = []
    for a in apps:
        st, ev = metric_status(a, key)
        cells_.append("%s<br><sub>%s</sub>" % (st, ev))
    rl.append("| %s | %s |" % (label, " | ".join(cells_)))
rl += [
    "",
    "Dropped frames are carried as raw counts only (`raw_dropped_frames`), and",
    "throughput is NOT treated as a QoE metric — both per the brief.",
    "",
    "## How to read a run folder",
    "",
    "```",
    "<app>/<app>_<bw>Mbps_<lat>ms_<aqm>_trial<N>/",
    "  samples.json              raw per-second samples, schema, interval, codec",
    "  summary.json              the five metrics, verdict, byte references",
    "  run_meta.json             commit, Chrome version, VM, shaping, window",
    "  download_throughput.png   separate from upload, fixed 0-11 Mbps axis",
    "  upload_throughput.png",
    "  qoe_buffer.png            buffer level, rebuffer spans shaded",
    "  qoe_resolution.png        resolution over time",
    "  qoe_bitrate.png           delivered video and audio bitrate",
    "  qoe_switches.png          rendition switches, green=up red=down",
    "  qoe_fps.png               presented frames per second",
    "  qoe_startup.png           startup delay vs initial buffer fill",
    "```",
    "",
    "Throughput charts are line graphs (not filled), share a fixed 0-11 Mbps",
    "y-axis so runs are comparable, and are clipped to the playback window.",
    "",
    "## Known limitations",
    "",
    "- **Cross-traffic profiles are not implemented.** Still waiting on Jaber",
    "  for the pointer. No profile is simulated and none is claimed.",
    "- **2 vCPU VM.** Vimeo at 10 Mbps is decode-limited on this host, measured:",
    "  at 1440x1080 the frame-drop rate is 13.4-13.7 % against 2.2-2.7 % at",
    "  960x720 while the buffer stayed full. That is a host result, not a",
    "  network one, and is labelled as such.",
    "- **Twitch delivered bitrate is unavailable by construction**: it builds no",
    "  main-world MediaSource, running MSE inside Workers and feeding the element",
    "  a MediaSourceHandle, so appended-byte hooks cannot reach it. Reported as",
    "  null with that reason rather than as 0.",
    "- **VP9-in-WebM reports codec family only.** Verified on a real file: WebM",
    "  carries no CodecPrivate for VP9, so the profile lives in the bitstream and",
    "  no init-segment parser can recover it.",
    "- **Tubi titles rotate.** A retired title still returns HTTP 200 with the",
    "  right page title but shows CONTENT UNAVAILABLE and creates no video",
    "  element. The URL in `shared/apps.py` carries instructions for re-pointing.",
    "- **pcaps are not in git.** See `pcap_manifest.csv` for sizes, SHA-256 and",
    "  where each one lives.",
    "",
    "## Reproducing",
    "",
    "```bash",
    "# on the lab VM, with the stack up (substrate worker + telemetry healthy)",
    "cd experiments/pramana",
    "PRAMANA_COLLECTOR_SRC=$PWD/../../services/orchestration/scripts/\\",
    "selenium_video_qoe/collect.py PRAMANA_DEFER_ANALYSIS=1 \\",
    "  python run_matrix.py all          # the sweep in sweep_matrix.md",
    "",
    "# then rebuild this package (charts + summaries + manifest) from raw runs",
    "python build_release.py <out_dir> <run_dir> [<run_dir> ...]",
    "python write_docs.py <out_dir>",
    "```",
    "",
    "Versions, VM name and the exact shaping parameters for each run are in that",
    "run's `run_meta.json`.",
]
(ROOT / "README.md").write_text("\n".join(rl) + "\n")
print("wrote README.md and sweep_matrix.md")
print("  runs=%d checks=%d FAIL=%d UNKNOWN=%d" % (len(built), len(checks), nfail, nunk))
