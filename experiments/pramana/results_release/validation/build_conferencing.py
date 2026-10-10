#!/usr/bin/env python3
"""Meet and Zoom runs, reported in WebRTC terms rather than HTML5 terms.

Kept out of the main per-app pipeline on purpose. A call has no MSE and no
seekable media clock, so appended-byte bitrate and presented-frame rate do not
exist for it; forcing these runs through the video path would emit empty charts
that look like measurements. What a call does expose -- inbound-RTP bitrate,
jitter, decoded frames, resolution -- is charted from the series already stored
in each record.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT = Path(sys.argv[1])
DIRS = [Path(x) for x in sys.argv[2:]]
OUT.mkdir(parents=True, exist_ok=True)
rows = []

for d in DIRS:
    rj = d / "record.json"
    if not rj.exists():
        continue
    rec = json.loads(rj.read_text())
    cfg = rec.get("config") or {}
    apps = cfg.get("apps") or []
    for app in apps:
        q = (rec.get("player_qoe") or {}).get(app) or {}
        if q.get("kind") != "webrtc" or q.get("status") != "ok":
            continue
        cap = float(cfg.get("bandwidth_mbps") or 0)
        lat = float(cfg.get("latency_ms") or 0)
        name = "%s_%gMbps_%gms_%s_trial%s" % (
            app,
            cap,
            lat,
            cfg.get("aqm") or "pfifo",
            cfg.get("trial"),
        )
        rd = OUT / app / name
        rd.mkdir(parents=True, exist_ok=True)
        series = q.get("series") or {}

        def chart(key, title, ylabel, fname, scale=1.0, color="#1565C0"):
            pts = series.get(key) or []
            if not pts:
                return False
            t = [p["t"] for p in pts]
            v = [(p["v"] or 0) * scale for p in pts]
            f, ax = plt.subplots(figsize=(10, 4.6))
            ax.set_title(
                "%s — %s @ %g Mbps, %gms" % (title, app, cap, lat), fontsize=11
            )
            ax.set_xlabel("Seconds from session start", fontsize=9)
            ax.set_ylabel(ylabel, fontsize=9)
            ax.grid(alpha=0.25, lw=0.5)
            ax.plot(t, v, color=color, lw=1.4, marker="o", ms=2.6)
            ax.set_ylim(bottom=0)
            ax.set_xlim(0, max(t) if t else 1)
            f.tight_layout()
            f.savefig(rd / fname, dpi=140, bbox_inches="tight")
            plt.close(f)
            return True

        made = []
        if chart(
            "inbound_bitrate_kbps",
            "Inbound RTP Bitrate",
            "Mbps",
            "webrtc_bitrate.png",
            scale=1 / 1000.0,
        ):
            made.append("webrtc_bitrate.png")
        if chart(
            "resolution_p",
            "Video Resolution",
            "Frame height (p)",
            "webrtc_resolution.png",
            color="#6A1B9A",
        ):
            made.append("webrtc_resolution.png")
        if chart(
            "jitter_buffer_secs",
            "Jitter Buffer Delay",
            "Seconds",
            "webrtc_jitter.png",
            color="#2E7D32",
        ):
            made.append("webrtc_jitter.png")

        summ = {
            "run": name,
            "app": app,
            "kind": "webrtc",
            "admission": "MANUAL — a human admitted the bot into the meeting",
            "shaping": {
                "bandwidth_mbps": cap,
                "latency_ms": lat,
                "aqm": cfg.get("aqm"),
                "loss_pct": cfg.get("loss_pct"),
            },
            "metrics_webrtc": {
                "startup_to_first_decoded_frame_ms": q.get("video_startup_time_ms"),
                "rebuffer_events": q.get("rebuffer_events"),
                "rebuffer_duration_ms": q.get("rebuffer_duration_ms"),
                "inbound_bitrate_mbps": {
                    "mean": q.get("mean_bitrate_mbps"),
                    "min": q.get("min_bitrate_mbps"),
                    "max": q.get("max_bitrate_mbps"),
                },
                "resolution_p": q.get("video_resolution_p"),
                "resolutions_observed": q.get("resolutions_observed"),
                "resolution_changes": q.get("resolution_changes"),
                "decoded_frame_rate_fps": q.get("frame_rate_fps"),
                "mean_jitter_secs": q.get("mean_jitter_secs"),
                "packet_loss_pct": q.get("packet_loss_pct"),
                "raw_dropped_video_frames": q.get("dropped_video_frames"),
                "raw_total_video_frames": q.get("total_video_frames"),
                "session_seconds": q.get("session_seconds"),
                "total_samples": q.get("total_samples"),
            },
            "limits": [
                "Delivered bitrate is inbound-RTP from getStats(), NOT appended "
                "MSE bytes: a call uses no MediaSource, so it is a different "
                "layer and is not comparable to the video apps' figure.",
                "Frame rate is DECODED frames; a call exposes no "
                "requestVideoFrameCallback presentation count here, so there is "
                "no presented-frame rate.",
                "Startup is time to first decoded frame, measured from session "
                "start, not from a navigation.",
                "Admission is manual, so these runs are not unattended and the "
                "join moment is not under experiment control.",
                "The post-join bitrate spike is excluded from mean/min/max "
                "(recorded in the run's own derivation note).",
            ],
            "charts": made,
        }
        (rd / "summary.json").write_text(json.dumps(summ, indent=2))
        rows.append(summ)

rows.sort(key=lambda r: (r["app"], r["shaping"]["bandwidth_mbps"]))
md = [
    "# Conferencing runs (Meet, Zoom) — WebRTC, manual admission",
    "",
    "These are included for coverage and are **not** comparable to the video",
    "apps row for row. A call has no MediaSource and no seekable media clock,",
    "so two of the five metrics exist only in a different form here:",
    "delivered bitrate is inbound-RTP from `getStats()` rather than appended",
    "bytes, and frame rate is decoded rather than presented.",
    "",
    "**Admission is manual**: a human admitted the bot into each meeting, so",
    "these runs were not unattended and the join instant was not under",
    "experiment control.",
    "",
    "| run | cap | latency | res | startup (ms) | rebuffers | inbound Mbps (mean) | decoded fps | jitter (s) | loss % |",
    "|---|---|---|---|---|---|---|---|---|---|",
]
for r in rows:
    m = r["metrics_webrtc"]
    b = m["inbound_bitrate_mbps"]
    md.append(
        "| %s | %g | %g ms | %sp | %s | %s | %s | %s | %s | %s |"
        % (
            r["run"],
            r["shaping"]["bandwidth_mbps"],
            r["shaping"]["latency_ms"],
            m["resolution_p"],
            m["startup_to_first_decoded_frame_ms"],
            m["rebuffer_events"],
            b["mean"],
            m["decoded_frame_rate_fps"],
            m["mean_jitter_secs"],
            m["packet_loss_pct"],
        )
    )
md += [
    "",
    "## Known limits, in full",
    "",
]
seen = set()
for r in rows:
    for lim in r["limits"]:
        if lim not in seen:
            seen.add(lim)
            md.append("- " + lim)
md += [
    "",
    "Per-run charts are `webrtc_bitrate.png`, `webrtc_resolution.png` and",
    "`webrtc_jitter.png` where the series existed in the record.",
]
(OUT / "README.md").write_text("\n".join(md) + "\n")
print("conferencing: %d run(s) written to %s" % (len(rows), OUT))
