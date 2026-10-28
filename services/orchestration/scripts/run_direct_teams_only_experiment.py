#!/usr/bin/env python3
"""Run a standalone Microsoft Teams receiver on one Thin Waist bottleneck.

Teams needs_peer: with nobody else in the room publishing video, the only
track is the local camera preview, which measures this machine rather than
the network. A bot peer (services/orchestration/scripts/selenium_video_qoe/
bot_peer.py) joins the same room on ordinary Docker networking - deliberately
outside the shaped ns1 namespace, so its own uplink isn't throttled by the
same cap as the Teams participant under test - and publishes a generated
Y4M clip as its camera. It's started before shaping/capture begins, since
admission can take minutes and every second of that would otherwise be
captured as idle link time.

Teams was confirmed (manually, against a real call, via
chrome://webrtc-internals) to use a genuine RTCPeerConnection with real
inbound-rtp video stats - so this reuses the same WebRTC getStats() path as
Google Meet. The join-flow JS (TEAMS_JOIN_JS in collect.py) is a best-effort
guess at the guest-join DOM, not yet verified against a real run, and may
need iteration.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
IMAGE = "agentic-thin-waist-substrate-worker:latest"
VIDEO_QOE_IMAGE = "video-qoe-collector:latest"

APPS = ("teams",)
DISPLAY_NUMS: dict[str, int] = {"teams": 99}


def docker(*args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["docker", *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return completed.stdout.strip()


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code}: {body}") from exc


def wait_for_worker(endpoint: str) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            request_json("GET", f"{endpoint}/health", timeout=3)
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError("substrate worker did not become healthy within 120 seconds")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def build_experiment(
    capacity_mbps: int,
    duration_seconds: int,
    browser_resolution: tuple[int, int],
    teams_url: str,
    latency_ms: int = 100,
) -> dict[str, Any]:
    window_width, window_height = browser_resolution
    return {
        "experiment_id": f"teams-only-{capacity_mbps}mbps-{latency_ms}ms-pfifo",
        "applications": list(APPS),
        "execution_mode": "concurrent",
        "capacity_mbps": capacity_mbps,
        "upload_mbps": capacity_mbps,
        "latency_ms": latency_ms,
        "latency_location": "upstream",
        "aqm_policy": "pfifo",
        "buffer_packets": 50,
        "cc_algorithm": "cubic",
        "duration_seconds": duration_seconds,
        "browser_resolution": f"{window_width}x{window_height}",
        "teams_url": teams_url,
    }


def result_dir_for(capacity_mbps: int, result_subdir: str, nest_mbps: bool = True) -> Path:
    base = REPO_ROOT / "experiments" / result_subdir
    return base / f"{capacity_mbps}mbps" if nest_mbps else base


def start_bot_peer(
    result_dir: Path,
    teams_url: str,
    bot_name: str,
    hold_seconds: int,
    join_deadline_seconds: int,
    clip_resolution: tuple[int, int] | None = None,
    clip_file: Path | None = None,
) -> str:
    """Launch the Teams bot peer on ordinary Docker networking (not ns1).

    clip_file mounts a real, pre-made Y4M as the bot's fake camera instead of
    letting it generate the synthetic clip itself - a real video carries
    genuine detail/motion complexity a procedural texture doesn't.
    """
    container = f"teams-only-bot-{uuid.uuid4().hex[:8]}"
    env_args = [
        "--env", f"BOT_ROOM_URL={teams_url}",
        "--env", f"BOT_NAME={bot_name}",
        "--env", "BOT_APP=teams",
        "--env", f"BOT_HOLD_SECONDS={hold_seconds}",
        "--env", f"BOT_JOIN_DEADLINE={join_deadline_seconds}",
        "--env", "BOT_STATUS=/out/bot_status.json",
        "--env", "BOT_DISPLAY=111",
    ]
    if clip_resolution and not clip_file:
        clip_width, clip_height = clip_resolution
        env_args += [
            "--env", f"BOT_CLIP_WIDTH={clip_width}",
            "--env", f"BOT_CLIP_HEIGHT={clip_height}",
        ]
    volume_args = ["--volume", f"{result_dir}:/out"]
    if clip_file:
        env_args += ["--env", "BOT_CLIP=/clip.y4m"]
        volume_args += ["--volume", f"{clip_file}:/clip.y4m:ro"]
    docker(
        "run",
        "--detach",
        "--rm",
        "--platform",
        "linux/amd64",
        "--name",
        container,
        *env_args,
        *volume_args,
        "--entrypoint",
        "python3",
        VIDEO_QOE_IMAGE,
        "bot_peer.py",
    )
    return container


def read_bot_status(result_dir: Path) -> dict[str, Any] | None:
    status_path = result_dir / "bot_status.json"
    if not status_path.is_file():
        return None
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def wait_for_bot_joined(result_dir: Path, timeout_seconds: float) -> dict[str, Any]:
    """Poll bot_status.json until the bot reports it joined (or failed).

    Admission is still manual - if nobody admits the bot within the deadline
    this raises, same as bot_peer.py's own "failed: not admitted" outcome.
    """
    deadline = time.monotonic() + timeout_seconds
    last_state = None
    while time.monotonic() < deadline:
        status = read_bot_status(result_dir)
        if status:
            state = status.get("state")
            if state != last_state:
                print(f"[teams bot] {state}: {status}")
                last_state = state
            if state == "joined":
                return status
            if state == "failed":
                raise RuntimeError(f"teams bot peer failed to join: {status}")
        time.sleep(3)
    raise RuntimeError(
        f"teams bot peer did not report 'joined' within {timeout_seconds:.0f}s "
        f"(last known status: {read_bot_status(result_dir)})"
    )


def run_video_collectors_concurrently(
    network_container: str,
    result_dir: Any,
    duration_seconds: int,
    video_urls: dict[str, str],
    display_nums: dict[str, int] | None = None,
    job_options: dict[str, dict[str, Any]] | None = None,
    volume_mounts: list[str] | None = None,
    browser_resolution: tuple[int, int] = (1920, 1080),
) -> None:
    display_nums = display_nums or DISPLAY_NUMS
    job_options = job_options or {}
    volume_mounts = volume_mounts or []
    window_width, window_height = browser_resolution
    if window_width <= 0 or window_height <= 0:
        raise ValueError("browser resolution dimensions must be positive")

    ns1_pid = docker(
        "exec", network_container, "cat", "/var/run/substrate/ns1.pid"
    ).strip()
    if not ns1_pid.isdigit():
        raise RuntimeError(f"could not resolve ns1 anchor PID: {ns1_pid!r}")

    jobs = []
    for app, url in video_urls.items():
        job = {
            "app": app,
            "url": url,
            "display_num": display_nums[app],
            "out_path": f"/out/{app}_stats.jsonl",
            "duration_seconds": duration_seconds,
            "sample_interval_seconds": 1.0,
            "window_width": window_width,
            "window_height": window_height,
        }
        job.update(job_options.get(app, {}))
        jobs.append(job)

    docker_args = [
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "--privileged",
        "--pid",
        "host",
        "--dns",
        "8.8.8.8",
        "--env",
        f"JOBS={json.dumps(jobs)}",
        "--volume",
        f"{result_dir}:/out",
    ]
    for volume_mount in volume_mounts:
        docker_args.extend(["--volume", volume_mount])
    docker_args.extend(
        [
            "--entrypoint",
            "nsenter",
            VIDEO_QOE_IMAGE,
            f"--net=/proc/{ns1_pid}/ns/net",
            "--",
            "python3",
            "collect.py",
        ]
    )
    collector_output = docker(*docker_args)
    if collector_output:
        print(collector_output)


def summarize_stats_jsonl(path: Any, app: str) -> dict[str, Any]:
    """Summarize a {app}_stats.jsonl file written by video-qoe-collector.

    Teams reports WebRTC inbound-rtp stats keyed by frames_decoded,
    not the <video>-element current_time_secs other apps use.
    """
    samples: list[dict[str, Any]] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    stats = [s.get("stats") or {} for s in samples]
    progress_field = "frames_decoded" if app == "teams" else "current_time_secs"
    times = [s[progress_field] for s in stats if s.get(progress_field) is not None]
    resolutions = sorted(
        {s["resolution"] for s in stats if s.get("resolution") and s["resolution"] != "0x0"}
    )
    summary = {
        "sample_count": len(samples),
        "advanced": bool(times) and max(times) > (times[0] if times else 0),
        "progress_field": progress_field,
        "progress_range": [min(times), max(times)] if times else None,
        "resolutions_observed": resolutions,
        "final_resolution": stats[-1].get("resolution") if stats else None,
    }
    if app == "teams":
        summary["final_frames_dropped"] = stats[-1].get("frames_dropped") if stats else None
        summary["final_packets_lost"] = stats[-1].get("packets_lost") if stats else None
        summary["final_freeze_count"] = stats[-1].get("freeze_count") if stats else None
    else:
        summary["final_dropped_video_frames"] = (
            stats[-1].get("dropped_video_frames") if stats else None
        )
        summary["final_total_video_frames"] = (
            stats[-1].get("total_video_frames") if stats else None
        )
    return summary


def run_one_tier(
    capacity_mbps: int,
    duration_seconds: int,
    browser_resolution: tuple[int, int],
    teams_url: str,
    teams_guest_name: str,
    teams_bot_name: str,
    teams_bot_join_deadline_seconds: int,
    latency_ms: int,
    result_subdir: str,
    nest_mbps: bool = True,
    bot_clip_resolution: tuple[int, int] | None = None,
    bot_clip_file: Path | None = None,
) -> int:
    video_urls = {"teams": teams_url}
    experiment = build_experiment(
        capacity_mbps, duration_seconds, browser_resolution, teams_url, latency_ms
    )
    result_dir = result_dir_for(capacity_mbps, result_subdir, nest_mbps)

    result_dir.mkdir(parents=True, exist_ok=True)
    for artifact_name in (
        "teams_stats.jsonl",
        "bot_status.json",
        f"{experiment['experiment_id']}.pcap",
        "failure.log",
    ):
        (result_dir / artifact_name).unlink(missing_ok=True)
    write_json(result_dir / "experiment.json", experiment)

    container = f"teams-only-direct-{uuid.uuid4().hex[:8]}"
    ctp_temp = tempfile.TemporaryDirectory(prefix="teams-only-direct-ctp-")
    capture_id: str | None = None
    endpoint: str | None = None
    bot_container: str | None = None
    failed = False

    try:
        print(f"[{capacity_mbps}mbps] 1/6 creating one ephemeral substrate worker")
        docker(
            "run",
            "--detach",
            "--rm",
            "--privileged",
            "--pid",
            "host",
            "--name",
            container,
            "--publish",
            "127.0.0.1::8002",
            "--env",
            "CONNECTIVITY_BACKEND=local_docker",
            "--env",
            "CAPTURE_DIR=/out",
            "--env",
            "CTP_DIR=/ctp",
            "--env",
            "NETGENT_USE_LOCAL=false",
            "--env",
            "NETGENT_NAMESPACE=ns1",
            "--env",
            "TIMEOUT=300000",
            "--volume",
            f"{result_dir}:/out",
            "--volume",
            f"{ctp_temp.name}:/ctp",
            "--volume",
            f"{REPO_ROOT / 'shared'}:/shared:ro",
            "--volume",
            (
                f"{REPO_ROOT / 'services/substrate-worker/src/substrate/main_local.py'}:"
                "/app/src/substrate/main_local.py:ro"
            ),
            IMAGE,
        )
        published = docker("port", container, "8002/tcp").splitlines()[0]
        endpoint = f"http://127.0.0.1:{published.rsplit(':', 1)[-1]}"
        wait_for_worker(endpoint)

        # Started here, before shaping/capture: admission can take minutes,
        # and every second of that would otherwise be captured as idle link
        # time if the bot only joined once the timed measurement began.
        print(
            f"[{capacity_mbps}mbps] 2/6 starting the Teams bot peer "
            "(ordinary Docker networking, not the shaped namespace)"
        )
        bot_container = start_bot_peer(
            result_dir,
            teams_url,
            teams_bot_name,
            hold_seconds=duration_seconds + 300,
            join_deadline_seconds=teams_bot_join_deadline_seconds,
            clip_resolution=bot_clip_resolution,
            clip_file=bot_clip_file,
        )

        print(
            f"[{capacity_mbps}mbps] 3/6 shaping once: "
            f"{capacity_mbps} Mbps / {experiment['latency_ms']} ms / pfifo (50 packets)"
        )
        shaping = request_json(
            "POST",
            f"{endpoint}/shape",
            {
                "upstream_iface": "veth4",
                "downstream_iface": "veth2",
                "download_mbps": experiment["capacity_mbps"],
                "upload_mbps": experiment["upload_mbps"],
                "latency_ms": experiment["latency_ms"],
                "latency_location": experiment["latency_location"],
                "qdisc": experiment["aqm_policy"],
                "buffer_packets": experiment["buffer_packets"],
            },
        )
        write_json(result_dir / "shaping.json", shaping)
        if shaping.get("status") != "shaped":
            raise RuntimeError(f"unexpected shaping response: {shaping}")
        request_json(
            "POST",
            f"{endpoint}/congestion",
            {"algorithm": experiment["cc_algorithm"], "namespace": "ns1"},
        )

        print(f"[{capacity_mbps}mbps] 4/6 waiting for the Teams bot peer to be admitted")
        wait_for_bot_joined(result_dir, timeout_seconds=teams_bot_join_deadline_seconds)

        print(f"[{capacity_mbps}mbps] 5/6 starting one shared packet capture")
        docker("exec", container, "chown", "root:root", "/out")
        capture = request_json(
            "POST",
            f"{endpoint}/capture",
            {
                "interface": "veth2",
                "capture_filter": "",
                "filename": experiment["experiment_id"],
                "duration_seconds": max(240, duration_seconds + 60),
            },
        )
        capture_id = capture["capture_id"]
        time.sleep(1)

        print(
            f"[{capacity_mbps}mbps] 6/6 playing Teams "
            "(SeleniumBase + undetected-chromedriver)"
        )
        run_video_collectors_concurrently(
            container,
            result_dir,
            duration_seconds,
            video_urls,
            job_options={
                "teams": {
                    "guest_name": teams_guest_name,
                    # Admission is manual, same as the bot peer - keep them in sync.
                    "join_timeout_seconds": teams_bot_join_deadline_seconds,
                }
            },
            browser_resolution=browser_resolution,
        )

        summaries: dict[str, Any] = {}
        for app in APPS:
            summary = summarize_stats_jsonl(result_dir / f"{app}_stats.jsonl", app)
            if summary["sample_count"] == 0:
                raise RuntimeError(f"{app} produced no QoE samples")
            if not summary["advanced"]:
                raise RuntimeError(
                    f"{app} playback never advanced past t=0: {summary}"
                )
            summaries[app] = summary

        request_json("DELETE", f"{endpoint}/capture/{capture_id}", timeout=15)
        capture_id = None
        docker("exec", container, "chown", "-R", f"{os.getuid()}:{os.getgid()}", "/out")
        pcap = result_dir / f"{experiment['experiment_id']}.pcap"
        if not pcap.is_file() or pcap.stat().st_size <= 24:
            raise RuntimeError(f"PCAP is missing or empty: {pcap}")
        (result_dir / "failure.log").unlink(missing_ok=True)

        print(f"PCAP:     {pcap}")
        print(f"Teams QoE: {result_dir / 'teams_stats.jsonl'}")
        print(json.dumps(summaries, indent=2))
        return 0
    except Exception as exc:
        failed = True
        print(f"[{capacity_mbps}mbps] FAILED: {exc}")
        return 1
    finally:
        if capture_id and endpoint:
            try:
                request_json("DELETE", f"{endpoint}/capture/{capture_id}", timeout=15)
            except Exception:
                pass
        try:
            docker(
                "exec", container, "chown", "-R",
                f"{os.getuid()}:{os.getgid()}", "/out", check=False,
            )
        except Exception:
            pass
        if failed:
            logs = docker("logs", container, check=False)
            (result_dir / "failure.log").write_text(logs + "\n", encoding="utf-8")
        docker("rm", "--force", container, check=False)
        # Teardown runs in this same path regardless of outcome, so a failed
        # run never leaves the bot sitting in the call.
        if bot_container:
            docker("rm", "--force", bot_container, check=False)
        ctp_temp.cleanup()


def main(
    capacities_mbps: list[int],
    duration_seconds: int,
    browser_resolution: tuple[int, int],
    teams_url: str,
    teams_guest_name: str,
    teams_bot_name: str,
    teams_bot_join_deadline_seconds: int,
    latency_ms: int,
    result_subdir: str,
    nest_mbps: bool = True,
    bot_clip_resolution: tuple[int, int] | None = None,
    bot_clip_file: Path | None = None,
) -> int:
    exit_code = 0
    for capacity_mbps in capacities_mbps:
        exit_code = (
            run_one_tier(
                capacity_mbps,
                duration_seconds,
                browser_resolution,
                teams_url,
                teams_guest_name,
                teams_bot_name,
                teams_bot_join_deadline_seconds,
                latency_ms,
                result_subdir,
                nest_mbps,
                bot_clip_resolution,
                bot_clip_file,
            )
            or exit_code
        )
    return exit_code


def parse_browser_resolution(value: str) -> tuple[int, int]:
    aliases = {
        "1080p": (1920, 1080),
        "2k": (2560, 1440),
        "1440p": (2560, 1440),
        "4k": (3840, 2160),
        "2160p": (3840, 2160),
    }
    normalized = value.strip().lower()
    if normalized in aliases:
        return aliases[normalized]
    try:
        width_text, height_text = normalized.split("x", 1)
        width, height = int(width_text), int(height_text)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError(
            "use 1080p, 2k, 4k, or WIDTHxHEIGHT"
        ) from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("resolution dimensions must be positive")
    return width, height


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--capacities-mbps",
        type=int,
        nargs="+",
        default=[3, 6, 10],
        help="Shared bottleneck capacities to run, one tier each (default: 3 6 10)",
    )
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=60,
        help="QoE collection window per tier (default: 60, i.e. one minute)",
    )
    parser.add_argument(
        "--browser-resolution",
        type=parse_browser_resolution,
        default=(1920, 1080),
        metavar="RESOLUTION",
        help="Chrome/Xvfb size: 1080p, 2k, 4k, or WIDTHxHEIGHT (default: 1080p)",
    )
    parser.add_argument(
        "--teams-url",
        required=True,
        help="Teams room URL (e.g. https://teams.live.com/meet/xxxxxxxxxxxxx?p=xxxxxxxxxxxxxxxx)",
    )
    parser.add_argument(
        "--teams-guest-name",
        default="NetGent QoE Collector",
        help="Display name the measured (receive-only) Teams participant joins with",
    )
    parser.add_argument(
        "--teams-bot-name",
        default="NetGent Bot Peer",
        help="Display name the bot peer (publishes the canned clip) joins with",
    )
    parser.add_argument(
        "--teams-bot-join-deadline-seconds",
        type=int,
        default=900,
        help="How long to wait for the bot peer to be admitted before giving "
        "up (default: 900, i.e. 15 minutes - admission is manual)",
    )
    parser.add_argument(
        "--latency-ms",
        type=int,
        default=100,
        help="One-way link latency applied by the shaper (default: 100)",
    )
    parser.add_argument(
        "--result-subdir",
        default="teams_only_experiment",
        help="Folder under experiments/ to write results into",
    )
    parser.add_argument(
        "--no-nest-mbps",
        action="store_true",
        help="Write directly into --result-subdir instead of a <capacity>mbps/ "
        "subfolder (only valid with a single capacity)",
    )
    parser.add_argument(
        "--bot-clip-resolution",
        type=parse_browser_resolution,
        default=None,
        metavar="RESOLUTION",
        help="Override the bot peer's fake-camera clip resolution "
        "(e.g. 1080p or WIDTHxHEIGHT); default is the clip generator's own "
        "640x480 default. Ignored if --bot-clip-file is set.",
    )
    parser.add_argument(
        "--bot-clip-file",
        type=Path,
        default=None,
        help="Mount a real, pre-made Y4M file as the bot's fake camera "
        "instead of the synthetic generator - takes priority over "
        "--bot-clip-resolution",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.no_nest_mbps and len(args.capacities_mbps) != 1:
        raise SystemExit("--no-nest-mbps requires exactly one --capacities-mbps value")
    raise SystemExit(
        main(
            args.capacities_mbps,
            args.duration_seconds,
            args.browser_resolution,
            args.teams_url,
            args.teams_guest_name,
            args.teams_bot_name,
            args.teams_bot_join_deadline_seconds,
            args.latency_ms,
            args.result_subdir,
            not args.no_nest_mbps,
            args.bot_clip_resolution,
            args.bot_clip_file,
        )
    )
