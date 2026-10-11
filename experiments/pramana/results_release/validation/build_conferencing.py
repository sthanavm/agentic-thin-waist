#!/usr/bin/env python3
"""Meet and Zoom, reported in their own terms.

Kept out of the video pipeline on purpose, and the two are not even alike:

  meet  takes the WebRTC path. Bitrate is inbound-RTP from getStats(), frame
        rate is DECODED, and there is a real time-to-first-decoded-frame.
  zoom  reports as html5_video, because its web client paints into a video
        element rather than exposing a peer connection our probe can read.
        It yields resolution, decoded frames and rebuffering -- and NO
        delivered bitrate at all.

Neither has MediaSource, so appended-byte bitrate and presented-frame rate do
not exist for either; forcing them through the video path would emit empty
charts that look like measurements.

One trap handled explicitly: Zoom's only startup figure is the legacy
`video_startup_time_ms`, which is the timestamp of the first sample where a
counter advanced. At a 1 s cadence that is floored at ~1 s by construction and
does NOT measure startup -- measured across 12 ladder runs it sat in a
1036-2086 ms band while real startup moved 7.6x. It is labelled as the
artifact it is rather than printed as a startup number.
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
seen: dict[str, int] = {}

for d in DIRS:
    rj = d / "record.json"
    if not rj.exists():
        continue
    rec = json.loads(rj.read_text())
    cfg = rec.get("config") or {}
    for app in cfg.get("apps") or []:
        q = (rec.get("player_qoe") or {}).get(app) or {}
        if q.get("status") != "ok" or not q.get("player_qoe_available"):
            continue
        kind = q.get("kind")
        cap = float(cfg.get("bandwidth_mbps") or 0)
        lat = float(cfg.get("latency_ms") or 0)
        base = "%s_%gMbps_%gms_%s" % (app, cap, lat, cfg.get("aqm") or "pfifo")
        # Several runs share (cap, latency, trial), so the trial number alone
        # does not identify one. The run directory's own suffix does.
        suffix = d.name.rsplit("_", 1)[-1][:8]
        name = "%s_%s" % (base, suffix)
        seen[base] = seen.get(base, 0) + 1
        rd = OUT / app / name
        rd.mkdir(parents=True, exist_ok=True)
        series = q.get("series") or {}

        made = []

        def chart(key, title, ylabel, fname, scale=1.0, color="#1565C0", step=False):
            pts = series.get(key) or []
            if not pts:
                return
            t = [p["t"] for p in pts]
            v = [(p["v"] or 0) * scale for p in pts]
            f, ax = plt.subplots(figsize=(10, 4.6))
            ax.set_title(
                "%s — %s @ %g Mbps, %gms" % (title, app, cap, lat), fontsize=11
            )
            ax.set_xlabel("Seconds from session start", fontsize=9)
            ax.set_ylabel(ylabel, fontsize=9)
            ax.grid(alpha=0.25, lw=0.5)
            if step:
                ax.step(t, v, where="post", color=color, lw=1.5)
            else:
                ax.plot(t, v, color=color, lw=1.4, marker="o", ms=2.6)
            ax.set_ylim(bottom=0)
            ax.set_xlim(0, max(t) if t else 1)
            f.tight_layout()
            f.savefig(rd / fname, dpi=140, bbox_inches="tight")
            plt.close(f)
            made.append(fname)

        chart(
            "inbound_bitrate_kbps",
            "Inbound RTP Bitrate",
            "Mbps",
            "conf_bitrate.png",
            scale=1 / 1000.0,
        )
        chart(
            "resolution_p",
            "Video Resolution",
            "Frame height (p)",
            "conf_resolution.png",
            color="#6A1B9A",
            step=True,
        )
        chart(
            "buffer_ahead_secs",
            "Buffer Ahead",
            "Seconds",
            "conf_buffer.png",
            color="#2E7D32",
        )

        metrics = {
            "resolution_p": q.get("video_resolution_p"),
            "resolutions_observed": q.get("resolutions_observed"),
            "resolution_changes": q.get("resolution_changes"),
            "rebuffer_events": q.get("rebuffer_events"),
            "rebuffer_duration_ms": q.get("rebuffer_duration_ms"),
            "decoded_frame_rate_fps": q.get("frame_rate_fps"),
            "raw_dropped_video_frames": q.get("dropped_video_frames"),
            "raw_total_video_frames": q.get("total_video_frames"),
            "session_seconds": q.get("session_seconds"),
            "total_samples": q.get("total_samples"),
        }
        if kind == "webrtc":
            metrics["inbound_bitrate_mbps"] = {
                "mean": q.get("mean_bitrate_mbps"),
                "min": q.get("min_bitrate_mbps"),
                "max": q.get("max_bitrate_mbps"),
            }
            metrics["startup_to_first_decoded_frame_ms"] = q.get(
                "video_startup_time_ms"
            )
            metrics["mean_jitter_secs"] = q.get("mean_jitter_secs")
            metrics["packet_loss_pct"] = q.get("packet_loss_pct")
        else:
            metrics["delivered_bitrate_mbps"] = None
            metrics["delivered_bitrate_reason"] = (
                "no MediaSource and no readable peer connection on this client, "
                "so there is no byte channel to measure"
            )
            metrics["startup_ms"] = None
            metrics["startup_reason"] = (
                "only the legacy counter-advance figure exists here "
                "(video_startup_time_ms=%s), which is floored at one sampling "
                "interval by construction and does not measure startup"
                % q.get("video_startup_time_ms")
            )

        summ = {
            "run": name,
            "app": app,
            "kind": kind,
            "run_dir_on_vm": str(d),
            "admission": "MANUAL — a human admitted the bot into the meeting",
            "shaping": {
                "bandwidth_mbps": cap,
                "latency_ms": lat,
                "aqm": cfg.get("aqm"),
                "loss_pct": cfg.get("loss_pct"),
            },
            "metrics": metrics,
            "charts": made,
        }
        (rd / "summary.json").write_text(json.dumps(summ, indent=2))
        rows.append(summ)

rows.sort(
    key=lambda r: (
        r["app"],
        r["shaping"]["bandwidth_mbps"],
        r["shaping"]["latency_ms"],
        r["run"],
    )
)
md = [
    "# Conferencing runs (Meet, Zoom) — manual admission",
    "",
    "Included for coverage. These are **not** comparable row-for-row with the",
    "video apps, and not with each other either.",
    "",
    "| | Meet | Zoom |",
    "|---|---|---|",
    "| path | WebRTC (`getStats()`) | reports as `html5_video` |",
    "| delivered bitrate | inbound-RTP, a different layer from appended bytes | **none available** |",
    "| frame rate | decoded | decoded |",
    "| presented-frame rate | not available (no MediaSource, no rVFC counter) | not available |",
    "| startup | time to first decoded frame | **only the legacy artifact** — see below |",
    "",
    "**Admission is manual**: a human admitted the bot into each meeting, so",
    "these runs were not unattended and the join instant was not under",
    "experiment control.",
    "",
    "**Zoom has no trustworthy startup figure here.** The only one its path",
    "produces is `video_startup_time_ms`, the timestamp of the first sample",
    "where a counter advanced. At a 1 s cadence that is floored at about one",
    "second by construction: measured across 12 ladder runs it stayed in a",
    "1036-2086 ms band while real startup moved by 7.6x. It is reported as",
    "`null` with that reason rather than printed as a startup number.",
    "",
    "| run | app | cap | latency | res | res changes | rebuffers | bitrate (Mbps) | decoded fps | dropped frames |",
    "|---|---|---|---|---|---|---|---|---|---|",
]
for r in rows:
    m = r["metrics"]
    br = m.get("inbound_bitrate_mbps")
    brs = (
        ("%.4f (inbound RTP)" % br["mean"])
        if (br and br.get("mean") is not None)
        else "**none**"
    )
    md.append(
        "| %s | %s | %g | %g ms | %sp | %s | %s | %s | %s | %s |"
        % (
            r["run"],
            r["app"],
            r["shaping"]["bandwidth_mbps"],
            r["shaping"]["latency_ms"],
            m.get("resolution_p"),
            m.get("resolution_changes"),
            m.get("rebuffer_events"),
            brs,
            m.get("decoded_frame_rate_fps"),
            m.get("raw_dropped_video_frames"),
        )
    )
md += [
    "",
    "Charts present per run where the series existed: `conf_bitrate.png`,",
    "`conf_resolution.png`, `conf_buffer.png`.",
]
(OUT / "README.md").write_text("\n".join(md) + "\n")
print("conferencing: %d run(s)" % len(rows))
for r in rows:
    print("   %-44s kind=%s" % (r["run"], r["kind"]))
