#!/usr/bin/env python3
"""Run a deterministic Pramana video experiment and render its HTML report."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DIRECT_RUNNER = REPO_ROOT / "services/orchestration/scripts/run_direct_youtube_vimeo_experiment.py"
REPORT_NOTEBOOK = Path(__file__).with_name("pramana_report.ipynb")
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "experiments/pramana/results/deterministic"

APP_URLS = {
    "youtube": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&autoplay=1&mute=1",
    "vimeo": "https://player.vimeo.com/video/911411289?autoplay=1&muted=1",
    "tubi": "https://tubitv.com/movies/100016098/the-legend-of-tarzan",
}


def load_direct_runner() -> Any:
    spec = importlib.util.spec_from_file_location("pramana_direct_core", DIRECT_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {DIRECT_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_report(run_dir: Path, report_path: Path) -> None:
    env = {
        **os.environ,
        "PRAMANA_RUN_DIR": str(run_dir.resolve()),
        "PRAMANA_REPORT_TITLE": f"Pramana deterministic report — {run_dir.name}",
    }
    subprocess.run(
        [
            sys.executable,
            "-m",
            "jupyter",
            "nbconvert",
            "--to",
            "html",
            "--execute",
            str(REPORT_NOTEBOOK),
            "--output",
            report_path.name,
            "--output-dir",
            str(report_path.parent),
            "--ExecutePreprocessor.timeout=300",
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def parse_resolution(value: str) -> tuple[int, int]:
    aliases = {"1080p": (1920, 1080), "2k": (2560, 1440), "4k": (3840, 2160)}
    normalized = value.lower().strip()
    if normalized in aliases:
        return aliases[normalized]
    try:
        width, height = (int(part) for part in normalized.split("x", 1))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("use 1080p, 2k, 4k, or WIDTHxHEIGHT") from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("resolution dimensions must be positive")
    return width, height


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apps", nargs="+", choices=sorted(APP_URLS), default=["youtube", "vimeo"])
    parser.add_argument("--capacities-mbps", nargs="+", type=int, default=[3, 6, 10])
    parser.add_argument("--duration-seconds", type=int, default=30)
    parser.add_argument("--latency-ms", type=int, default=100)
    parser.add_argument("--browser-resolution", type=parse_resolution, default=(1920, 1080))
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--tag", default="")
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    app_slug = "-".join(args.apps)
    run_name = args.tag or f"{app_slug}-{stamp}"
    run_root = args.output_root.resolve() / run_name
    run_root.mkdir(parents=True, exist_ok=False)
    (run_root / "run_config.json").write_text(
        json.dumps(
            {
                "mode": "deterministic",
                "apps": args.apps,
                "capacities_mbps": args.capacities_mbps,
                "duration_seconds": args.duration_seconds,
                "latency_ms": args.latency_ms,
                "browser_resolution": list(args.browser_resolution),
            },
            indent=2,
        ) + "\n"
    )

    core = load_direct_runner()
    core.VIDEO_URLS = {app: APP_URLS[app] for app in args.apps}
    core.DISPLAY_NUMS = {app: 99 + index for index, app in enumerate(args.apps)}
    core.result_dir_for = lambda capacity: run_root / f"{capacity}mbps"

    original_build = core.build_experiment

    def build_experiment(capacity: int, duration: int, resolution: tuple[int, int]) -> dict[str, Any]:
        experiment = original_build(capacity, duration, resolution)
        experiment.update(
            {
                "experiment_id": f"{app_slug}-{capacity}mbps-{args.latency_ms}ms-pfifo",
                "applications": list(args.apps),
                "latency_ms": args.latency_ms,
                "runner": "pramana-deterministic",
            }
        )
        return experiment

    core.build_experiment = build_experiment
    exit_code = 0
    for capacity in args.capacities_mbps:
        tier_code = core.run_one_tier(capacity, args.duration_seconds, args.browser_resolution)
        exit_code = tier_code or exit_code
        tier_dir = run_root / f"{capacity}mbps"
        render_report(tier_dir, tier_dir / "report.html")

    print(f"Pramana run: {run_root}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
