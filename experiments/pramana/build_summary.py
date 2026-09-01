#!/usr/bin/env python3
"""Flatten every record.json under a results dir into qoe_summary.{csv,json}.

    python3 build_summary.py [results_dir]

`results_dir` defaults to the Pramana results tree beside this file. Pass a path
to summarize runs pulled somewhere else.
"""
import csv, json, os, sys
from pathlib import Path

_DEFAULT = Path(
    os.environ.get("PRAMANA_RESULTS_DIR", str(Path(__file__).resolve().parent / "results"))
) / "pramana_runs"
ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else _DEFAULT
FIELDS = ["run","app","kind","status","bandwidth_mbps","latency_ms","aqm","concurrency",
          "video_resolution_p","resolutions_observed","video_startup_time_ms",
          "rebuffer_events","rebuffer_duration_ms","dropped_frame_pct","frame_rate_fps",
          "mean_bitrate_mbps","bitrate_basis","connection_speed_estimate_mbps",
          "mean_buffer_ahead_secs","min_buffer_ahead_secs","watched_seconds",
          "video_duration_secs","delivered_fraction_of_video","is_live",
          "avg_throughput_mbps","peak_throughput_mbps","total_mb","classification","reason"]


def build() -> list[dict]:
    rows = []
    for rec_path in sorted(ROOT.glob("*/record.json")):
        r = json.loads(rec_path.read_text())
        cfg = r.get("config", {})
        for app, q in (r.get("player_qoe") or {}).items():
            pas = (r.get("per_app_stats") or {}).get(app) or {}
            net = pas.get("download") or {}
            rows.append({
                "run": rec_path.parent.name, "app": app, "kind": q.get("kind"),
                "status": q.get("status"),
                "bandwidth_mbps": cfg.get("bandwidth_mbps"), "latency_ms": cfg.get("latency_ms"),
                "aqm": cfg.get("aqm"), "concurrency": cfg.get("concurrency"),
                "video_resolution_p": q.get("video_resolution_p"),
                "resolutions_observed": "|".join(map(str, q.get("resolutions_observed") or [])) or None,
                "video_startup_time_ms": q.get("video_startup_time_ms"),
                "rebuffer_events": q.get("rebuffer_events"),
                "rebuffer_duration_ms": q.get("rebuffer_duration_ms"),
                "dropped_frame_pct": q.get("dropped_frame_pct"),
                "frame_rate_fps": q.get("frame_rate_fps"),
                "mean_bitrate_mbps": q.get("mean_bitrate_mbps"),
                "bitrate_basis": (q.get("derivation") or {}).get("bitrate_basis"),
                "connection_speed_estimate_mbps": q.get("connection_speed_estimate_mbps"),
                "mean_buffer_ahead_secs": q.get("mean_buffer_ahead_secs"),
                "min_buffer_ahead_secs": q.get("min_buffer_ahead_secs"),
                "watched_seconds": q.get("watched_seconds"),
                "video_duration_secs": q.get("video_duration_secs"),
                "delivered_fraction_of_video": q.get("delivered_fraction_of_video"),
                "is_live": q.get("is_live"),
                "avg_throughput_mbps": net.get("avg_throughput_mbps"),
                "peak_throughput_mbps": net.get("peak_throughput_mbps"),
                "total_mb": net.get("total_mb"),
                "classification": pas.get("classification"),
                "reason": q.get("reason"),
            })
    return rows


if __name__ == "__main__":
    if not ROOT.is_dir():
        sys.exit(f"no such results dir: {ROOT}")
    rows = build()
    if not rows:
        sys.exit(f"no record.json files found under {ROOT}")
    with open(ROOT / "qoe_summary.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader(); w.writerows(rows)
    compact = {}
    for row in rows:
        compact.setdefault(row["run"], {})[row["app"]] = {
            k: v for k, v in row.items() if k not in ("run", "app") and v is not None}
    (ROOT / "qoe_summary.json").write_text(json.dumps(compact, indent=2))
    print(f"{len(rows)} app-rows across {len(compact)} runs "
          f"-> {ROOT}/qoe_summary.csv | qoe_summary.json")
