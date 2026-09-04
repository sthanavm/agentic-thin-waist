#!/usr/bin/env python3
"""Play the known-working Vimeo source alone and measure 15 seconds of QoE."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

from collect import STATS_JS, build_driver, start_display


VIMEO_URL = "https://player.vimeo.com/video/911411289?autoplay=1&muted=1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=int, default=15)
    parser.add_argument("--startup-timeout-seconds", type=int, default=60)
    parser.add_argument("--out", type=Path, default=Path("/out/vimeo_stats.jsonl"))
    parser.add_argument(
        "--screenshot", type=Path, default=Path("/out/vimeo_smoke.png")
    )
    args = parser.parse_args()

    display_num = "99"
    start_display(display_num, 1920, 1080)
    os.environ["DISPLAY"] = f":{display_num}"
    driver = build_driver(window_width=1920, window_height=1080)
    samples: list[dict[str, Any]] = []

    try:
        driver.set_window_rect(x=0, y=0, width=1920, height=1080)
        driver.set_page_load_timeout(45)
        try:
            driver.get(VIMEO_URL)
        except Exception as exc:
            print(f"navigation warning: {type(exc).__name__}: {exc}")

        previous_time: float | None = None
        deadline = time.monotonic() + args.startup_timeout_seconds
        while time.monotonic() < deadline:
            stats = driver.execute_script(STATS_JS)
            current_time = stats.get("current_time_secs")
            driver.execute_script(
                "const v=document.querySelector('video');"
                "if(v){v.muted=true;v.play().catch(()=>{});}"
            )
            if (
                isinstance(current_time, (int, float))
                and previous_time is not None
                and current_time > previous_time + 0.2
            ):
                break
            if isinstance(current_time, (int, float)):
                previous_time = current_time
            time.sleep(1)
        else:
            raise RuntimeError("Vimeo playback did not advance before timeout")

        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.unlink(missing_ok=True)
        for _ in range(args.duration_seconds):
            row = {
                "timestamp": time.time(),
                "url": driver.current_url,
                "stats": driver.execute_script(STATS_JS),
            }
            samples.append(row)
            with args.out.open("a", encoding="utf-8") as output:
                output.write(json.dumps(row) + "\n")
            time.sleep(1)

        driver.save_screenshot(str(args.screenshot))
    finally:
        driver.quit()

    stats = [row["stats"] for row in samples]
    playback_times = [float(item["current_time_secs"]) for item in stats]
    buffers = [float(item.get("buffer_ahead_secs") or 0) for item in stats]
    summary = {
        "samples": len(samples),
        "measurement_wall_seconds": samples[-1]["timestamp"] - samples[0]["timestamp"],
        "video_seconds_played": playback_times[-1] - playback_times[0],
        "starting_video_time": playback_times[0],
        "ending_video_time": playback_times[-1],
        "resolutions": sorted({item.get("resolution") for item in stats}),
        "buffer_ahead_minimum": min(buffers),
        "buffer_ahead_median": statistics.median(buffers),
        "buffer_ahead_maximum": max(buffers),
        "zero_buffer_samples": sum(value <= 0.01 for value in buffers),
        "output": str(args.out),
        "screenshot": str(args.screenshot),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
