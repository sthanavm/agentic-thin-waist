#!/usr/bin/env python3
"""Report Chrome viewport, rendered video size, and decoded stream quality."""

import argparse
import json
import os
import time

from collect import build_driver, start_display


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default=(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            "&autoplay=1&mute=1"
        ),
    )
    parser.add_argument("--wait-seconds", type=float, default=20)
    parser.add_argument("--screenshot", default="/out/render-test.png")
    parser.add_argument("--set-window", action="store_true")
    args = parser.parse_args()

    display_num = "99"
    start_display(display_num)
    os.environ["DISPLAY"] = f":{display_num}"

    driver = build_driver()
    try:
        if args.set_window:
            driver.set_window_rect(x=0, y=0, width=1920, height=1080)
        driver.get(args.url)
        time.sleep(args.wait_seconds)
        result = driver.execute_script(
            """
            const video = document.querySelector('video');
            const rect = video?.getBoundingClientRect();
            return {
                url: location.href,
                screen: `${screen.width}x${screen.height}`,
                viewport: `${innerWidth}x${innerHeight}`,
                rendered_video: rect
                    ? `${Math.round(rect.width)}x${Math.round(rect.height)}`
                    : null,
                decoded_video: video
                    ? `${video.videoWidth}x${video.videoHeight}`
                    : null,
                current_time: video?.currentTime ?? null,
                paused: video?.paused ?? null,
                ready_state: video?.readyState ?? null,
            };
            """
        )
        driver.save_screenshot(args.screenshot)
        result["screenshot"] = args.screenshot
        print(json.dumps(result, indent=2))
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
