"""Load complete or partial YouTube/Vimeo experiment data for the report."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_report_context(bandwidth_mbps: int) -> dict:
    candidates = [
        Path(f"youtube_vimeo_experiment/{bandwidth_mbps}mbps"),
        REPO_ROOT / f"experiments/youtube_vimeo_experiment/{bandwidth_mbps}mbps",
    ]
    result_dir = next((path.resolve() for path in candidates if path.is_dir()), None)
    if result_dir is None:
        raise FileNotFoundError(
            f"Could not locate the {bandwidth_mbps} Mbps result directory"
        )

    def load_qoe(app: str) -> list[dict]:
        path = result_dir / f"{app}_stats.jsonl"
        if not path.is_file():
            return []
        with path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    youtube_qoe = load_qoe("youtube")
    vimeo_qoe = load_qoe("vimeo")
    all_qoe = youtube_qoe + vimeo_qoe
    start_time = min((row["timestamp"] for row in all_qoe), default=None)
    youtube_seconds = (
        [row["timestamp"] - start_time for row in youtube_qoe]
        if start_time is not None else []
    )
    vimeo_seconds = (
        [row["timestamp"] - start_time for row in vimeo_qoe]
        if start_time is not None else []
    )
    plt.style.use("seaborn-v0_8-whitegrid")

    def summary_row(label: str, rows: list[dict]) -> dict:
        stats = [row.get("stats") or {} for row in rows]
        times = [
            item.get("current_time_secs")
            for item in stats
            if item.get("current_time_secs") is not None
        ]
        return {
            "Application": label,
            "Status": (
                "advanced"
                if times and max(times) > times[0]
                else "no samples" if not rows else "did not advance"
            ),
            "Samples": len(rows),
            "Video seconds advanced": max(times) - min(times) if times else None,
            "Final resolution": stats[-1].get("resolution") if stats else None,
            "Dropped-frame increase": (
                (stats[-1].get("dropped_video_frames", 0) or 0)
                - (stats[0].get("dropped_video_frames", 0) or 0)
                if stats
                else None
            ),
            "Final total frames": (
                stats[-1].get("total_video_frames") if stats else None
            ),
        }

    summary = pd.DataFrame(
        [
            summary_row("YouTube", youtube_qoe),
            summary_row("Vimeo", vimeo_qoe),
        ]
    ).set_index("Application")

    return {
        "json": json,
        "math": math,
        "subprocess": subprocess,
        "plt": plt,
        "pd": pd,
        "BANDWIDTH_MBPS": bandwidth_mbps,
        "RESULT_DIR": result_dir,
        "youtube_qoe": youtube_qoe,
        "vimeo_qoe": vimeo_qoe,
        "all_qoe": all_qoe,
        "youtube_seconds": youtube_seconds,
        "vimeo_seconds": vimeo_seconds,
        "YOUTUBE_COLOR": "#ff0000",
        "VIMEO_COLOR": "#0f4bff",
        "ys": [row.get("stats") or {} for row in youtube_qoe],
        "ts": [row.get("stats") or {} for row in vimeo_qoe],
        "summary": summary,
    }
