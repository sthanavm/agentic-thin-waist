"""Reusable report renderer for deterministic and intent-based Pramana runs."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
from IPython.display import Image, Markdown, display

APP_DOMAINS = {
    "youtube": ("youtube.com", "googlevideo.com", "ytimg.com", "ggpht.com"),
    "vimeo": ("vimeo.com", "vimeocdn.com"),
    "tubi": ("tubitv.com", "adrise.tv", "tubi.video", "tubi.io"),
}
APP_COLORS = {"youtube": "#ff0000", "vimeo": "#1ab7ea", "tubi": "#fbc02d"}
CLIENT_IP = "172.16.1.1"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.is_file() else {}


def _read_qoe(run_dir: Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(run_dir.glob("*_stats.jsonl")):
        app = path.name.removesuffix("_stats.jsonl")
        rows = []
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        result[app] = rows
    return result


def _qoe_summary(qoe: dict[str, list[dict[str, Any]]]) -> pd.DataFrame:
    rows = []
    for app, samples in qoe.items():
        stats = [row.get("stats") or {} for row in samples]
        times = [float(item["current_time_secs"]) for item in stats if item.get("current_time_secs") is not None]
        timestamps = [float(row["timestamp"]) for row in samples if row.get("timestamp") is not None]
        buffers = [float(item["buffer_ahead_secs"]) for item in stats if item.get("buffer_ahead_secs") is not None]
        stalls = 0
        stall_seconds = 0.0
        for index in range(1, min(len(times), len(timestamps))):
            wall_delta = timestamps[index] - timestamps[index - 1]
            video_delta = times[index] - times[index - 1]
            if wall_delta > 0 and video_delta < min(0.2, wall_delta * 0.25):
                stalls += 1
                stall_seconds += wall_delta
        watched = max(times) - min(times) if len(times) > 1 else 0.0
        wall = max(timestamps) - min(timestamps) if len(timestamps) > 1 else 0.0
        dropped = [float(item.get("dropped_video_frames") or 0) for item in stats]
        total = [float(item.get("total_video_frames") or 0) for item in stats]
        rows.append(
            {
                "application": app,
                "status": "advanced" if watched > 0 else "no playback samples",
                "samples": len(samples),
                "watched_seconds": watched,
                "delivery_fraction": watched / wall if wall else None,
                "stall_intervals": stalls,
                "estimated_stall_seconds": stall_seconds,
                "mean_buffer_seconds": sum(buffers) / len(buffers) if buffers else None,
                "zero_buffer_samples": sum(value <= 0 for value in buffers),
                "final_resolution": stats[-1].get("resolution") if stats else None,
                "dropped_frame_delta": dropped[-1] - dropped[0] if dropped else None,
                "total_frame_delta": total[-1] - total[0] if total else None,
            }
        )
    return pd.DataFrame(rows).set_index("application") if rows else pd.DataFrame()


def _capture_frames(run_dir: Path, qoe: dict[str, list[dict[str, Any]]]) -> tuple[pd.DataFrame, dict[str, set[str]], float]:
    pcaps = list(run_dir.glob("*.pcap"))
    if not pcaps:
        return pd.DataFrame(), {}, 0.0
    pcap = pcaps[0]
    all_rows = [row for rows in qoe.values() for row in rows]
    if all_rows:
        window_start = min(float(row["timestamp"]) for row in all_rows)
        window_end = max(float(row["timestamp"]) for row in all_rows)
    else:
        bounds = subprocess.run(
            ["tshark", "-r", str(pcap), "-T", "fields", "-e", "frame.time_epoch"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        values = [float(value) for value in bounds if value]
        window_start, window_end = min(values), max(values)
    sni = subprocess.run(
        ["tshark", "-r", str(pcap), "-Y", "tls.handshake.extensions_server_name",
         "-T", "fields", "-e", "ip.dst", "-e", "tls.handshake.extensions_server_name"],
        capture_output=True, text=True, check=True,
    ).stdout
    hosts: dict[str, set[str]] = {}
    for line in sni.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and fields[0] and fields[1]:
            hosts.setdefault(fields[0].split(",")[0], set()).update(
                host.lower() for host in fields[1].split(",")
            )

    output = subprocess.run(
        ["tshark", "-r", str(pcap), "-Y", f"ip.dst == {CLIENT_IP}", "-T", "fields",
         "-e", "frame.time_epoch", "-e", "ip.src", "-e", "frame.len"],
        capture_output=True, text=True, check=True,
    ).stdout
    packets = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) < 3 or not all(fields[:3]):
            continue
        timestamp = float(fields[0])
        if not window_start <= timestamp <= window_end:
            continue
        ip = fields[1].split(",")[0]
        app = "unclassified"
        ip_hosts = hosts.get(ip, set())
        for candidate, domains in APP_DOMAINS.items():
            if any(domain in host for host in ip_hosts for domain in domains):
                app = candidate
                break
        packets.append((timestamp, int(timestamp - window_start), ip, int(fields[2].split(",")[0]), app))
    return pd.DataFrame(packets, columns=["timestamp", "second", "remote_ip", "frame_bytes", "application"]), hosts, window_end - window_start


def _network_summary(packets: pd.DataFrame, duration: float) -> tuple[pd.DataFrame, float | None]:
    if packets.empty or duration <= 0:
        return pd.DataFrame(), None
    per_second = packets.groupby(["second", "application"]).frame_bytes.sum().unstack(fill_value=0) * 8 / 1_000_000
    rows = []
    app_rates = []
    for app in sorted(column for column in per_second.columns if column != "unclassified"):
        values = per_second[app]
        total = packets.loc[packets.application == app, "frame_bytes"].sum()
        average = total * 8 / duration / 1_000_000
        app_rates.append(average)
        rows.append({"application": app, "download_MiB": total / 2**20, "average_Mbps": average, "p95_Mbps": values.quantile(.95), "peak_Mbps": values.max()})
    fairness = None
    if app_rates and sum(value * value for value in app_rates):
        fairness = sum(app_rates) ** 2 / (len(app_rates) * sum(value * value for value in app_rates))
    return pd.DataFrame(rows).set_index("application") if rows else pd.DataFrame(), fairness


def render_report(run_dir: str | Path, title: str) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    display(Markdown(f"# {title}\n\nRun directory: `{run_dir}`"))
    config = _read_json(run_dir / "experiment.json") or _read_json(run_dir / "run_config.json")
    orchestration = _read_json(run_dir / "orchestration.json")
    if config:
        display(Markdown("## Configuration"))
        display(pd.DataFrame([config]).T.rename(columns={0: "value"}))
    if orchestration:
        display(Markdown("## Intent orchestration"))
        display(pd.DataFrame([{"status": orchestration.get("status"), "intent": orchestration.get("intent"), "error": orchestration.get("error")}]))

    qoe = _read_qoe(run_dir)
    summary = _qoe_summary(qoe)
    display(Markdown("## Player QoE validation"))
    display(Markdown(
        "This adds Pramana's delivery fraction, estimated stall intervals, zero-buffer count, "
        "and explicit data-availability status. The older report graphed raw series but did not "
        "turn them into validation-oriented run metrics. Startup delay is intentionally not claimed "
        "because sampling begins only after playback advances."
    ))
    if summary.empty:
        display(Markdown("No local player QoE samples were available for this intent run."))
    else:
        display(summary.round(3))

    packets, hosts, duration = _capture_frames(run_dir, qoe)
    network, fairness = _network_summary(packets, duration)
    display(Markdown("## Per-application network delivery and fairness"))
    display(Markdown(
        "This adds Pramana's average/p95/peak delivery summary and Jain fairness index. "
        "It complements the existing time-series plots by making starvation and unequal sharing explicit."
    ))
    if not network.empty:
        display(network.round(3))
        display(Markdown(f"**Jain fairness index:** {fairness:.3f}" if fairness is not None else "Fairness unavailable."))
        bins = max(1, math.ceil(duration))
        rates = packets.groupby(["second", "application"]).frame_bytes.sum().unstack(fill_value=0).reindex(range(bins), fill_value=0) * 8 / 1_000_000
        fig, ax = plt.subplots(figsize=(11, 5), dpi=120)
        for app in rates.columns:
            ax.plot(rates.index, rates[app], label=app, color=APP_COLORS.get(app), alpha=.7 if app == "unclassified" else 1)
        ax.set(title="Download throughput by attributed application", xlabel="Seconds", ylabel="Mbit per 1-second bin")
        ax.legend(); plt.tight_layout(); plt.show()

        totals = packets.groupby("remote_ip").frame_bytes.sum().sort_values(ascending=False).head(10)
        endpoint_rates = packets[packets.remote_ip.isin(totals.index)].groupby(["second", "remote_ip"]).frame_bytes.sum().unstack(fill_value=0).reindex(range(bins), fill_value=0) * 8 / 1_000_000
        fig, ax = plt.subplots(figsize=(12, 6), dpi=120)
        for ip in totals.index:
            names = sorted(hosts.get(ip, set()))
            ax.plot(endpoint_rates.index, endpoint_rates[ip], label=f"{ip} — {names[0] if names else 'unknown'}")
        ax.set(title="Download throughput by remote endpoint", xlabel="Seconds", ylabel="Mbit per 1-second bin")
        ax.legend(fontsize=8); plt.tight_layout(); plt.show()
    else:
        display(Markdown("No local PCAP was available; network plots are omitted."))

    apps = list(qoe) or list(config.get("applications") or config.get("apps") or [])
    if apps:
        display(Markdown("## Five-second browser timeline"))
        seconds = list(range(0, 30, 5))
        fig, axes = plt.subplots(len(apps), len(seconds), figsize=(18, 3.4 * len(apps)), dpi=120, squeeze=False)
        for row, app in enumerate(apps):
            for column, second in enumerate(seconds):
                ax = axes[row][column]
                path = run_dir / f"{app}_screenshots/{second:03d}s.png"
                if path.is_file():
                    ax.imshow(plt.imread(path))
                else:
                    ax.text(.5, .5, "not captured", ha="center", va="center")
                ax.set_title(f"{app} — {second}s", fontsize=9); ax.axis("off")
        plt.tight_layout(); plt.show()
    return {"config": config, "qoe_summary": summary, "network_summary": network, "fairness": fairness}
