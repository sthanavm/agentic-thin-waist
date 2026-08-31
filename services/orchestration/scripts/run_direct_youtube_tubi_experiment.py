#!/usr/bin/env python3
"""Run YouTube and Tubi concurrently on one fixed Thin Waist bottleneck."""

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

VIDEO_URLS: dict[str, str] = {
    "youtube": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&autoplay=1&mute=1",
    "tubi": "https://tubitv.com/movies/312932/mission-impossible",
}

DISPLAY_NUMS: dict[str, int] = {"youtube": 99, "tubi": 100}


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


def build_experiment(capacity_mbps: int, duration_seconds: int) -> dict[str, Any]:
    return {
        "experiment_id": f"youtube-tubi-{capacity_mbps}mbps-100ms-pfifo",
        "applications": ["youtube", "tubi"],
        "execution_mode": "concurrent",
        "capacity_mbps": capacity_mbps,
        "upload_mbps": capacity_mbps,
        "latency_ms": 100,
        "latency_location": "upstream",
        "aqm_policy": "pfifo",
        "buffer_packets": 50,
        "cc_algorithm": "cubic",
        "duration_seconds": duration_seconds,
    }


def result_dir_for(capacity_mbps: int) -> Path:
    return REPO_ROOT / "experiments" / "youtube_tubi_experiment" / f"{capacity_mbps}mbps"


def run_video_collectors_concurrently(
    network_container: str,
    result_dir: Any,
    duration_seconds: int,
    video_urls: dict[str, str] | None = None,
    display_nums: dict[str, int] | None = None,
    job_options: dict[str, dict[str, Any]] | None = None,
    volume_mounts: list[str] | None = None,
) -> None:
    video_urls = video_urls or VIDEO_URLS
    display_nums = display_nums or DISPLAY_NUMS
    job_options = job_options or {}
    volume_mounts = volume_mounts or []

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


def summarize_stats_jsonl(path: Any) -> dict[str, Any]:
    """Summarize a {app}_stats.jsonl file written by video-qoe-collector."""
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
    times = [s["current_time_secs"] for s in stats if s.get("current_time_secs") is not None]
    resolutions = sorted(
        {s["resolution"] for s in stats if s.get("resolution") and s["resolution"] != "0x0"}
    )
    return {
        "sample_count": len(samples),
        "advanced": bool(times) and max(times) > (times[0] if times else 0),
        "current_time_range": [min(times), max(times)] if times else None,
        "resolutions_observed": resolutions,
        "final_resolution": stats[-1].get("resolution") if stats else None,
        "final_dropped_video_frames": stats[-1].get("dropped_video_frames") if stats else None,
        "final_total_video_frames": stats[-1].get("total_video_frames") if stats else None,
    }


def run_one_tier(capacity_mbps: int, duration_seconds: int) -> int:
    experiment = build_experiment(capacity_mbps, duration_seconds)
    result_dir = result_dir_for(capacity_mbps)

    result_dir.mkdir(parents=True, exist_ok=True)
    for artifact_name in (
        "youtube_stats.jsonl",
        "tubi_stats.jsonl",
        f"{experiment['experiment_id']}.pcap",
        "failure.log",
    ):
        (result_dir / artifact_name).unlink(missing_ok=True)
    write_json(result_dir / "experiment.json", experiment)

    container = f"youtube-tubi-direct-{uuid.uuid4().hex[:8]}"
    ctp_temp = tempfile.TemporaryDirectory(prefix="youtube-tubi-direct-ctp-")
    capture_id: str | None = None
    endpoint: str | None = None
    failed = False

    try:
        print(f"[{capacity_mbps}mbps] 1/5 creating one ephemeral substrate worker")
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

        print(
            f"[{capacity_mbps}mbps] 2/5 shaping once: "
            f"{capacity_mbps} Mbps / 100 ms / pfifo (50 packets)"
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

        print(f"[{capacity_mbps}mbps] 3/5 starting one shared packet capture")
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
            f"[{capacity_mbps}mbps] 4/5 playing YouTube and Tubi concurrently "
            "(SeleniumBase + undetected-chromedriver)"
        )
        run_video_collectors_concurrently(container, result_dir, duration_seconds)

        summaries: dict[str, Any] = {}
        for app in VIDEO_URLS:
            summary = summarize_stats_jsonl(result_dir / f"{app}_stats.jsonl")
            if summary["sample_count"] == 0:
                raise RuntimeError(f"{app} produced no QoE samples")
            if not summary["advanced"]:
                raise RuntimeError(
                    f"{app} playback never advanced past t=0: {summary}"
                )
            summaries[app] = summary

        print(f"[{capacity_mbps}mbps] 5/5 finalizing the shared capture")
        request_json("DELETE", f"{endpoint}/capture/{capture_id}", timeout=15)
        capture_id = None
        docker("exec", container, "chown", "-R", f"{os.getuid()}:{os.getgid()}", "/out")
        pcap = result_dir / f"{experiment['experiment_id']}.pcap"
        if not pcap.is_file() or pcap.stat().st_size <= 24:
            raise RuntimeError(f"PCAP is missing or empty: {pcap}")
        (result_dir / "failure.log").unlink(missing_ok=True)

        print(f"PCAP:        {pcap}")
        print(f"YouTube QoE: {result_dir / 'youtube_stats.jsonl'}")
        print(f"Tubi QoE:    {result_dir / 'tubi_stats.jsonl'}")
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
        ctp_temp.cleanup()


def main(capacities_mbps: list[int], duration_seconds: int) -> int:
    exit_code = 0
    for capacity_mbps in capacities_mbps:
        exit_code = run_one_tier(capacity_mbps, duration_seconds) or exit_code
    return exit_code


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
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(args.capacities_mbps, args.duration_seconds))
