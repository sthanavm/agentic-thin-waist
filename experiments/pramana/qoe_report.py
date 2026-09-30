#!/usr/bin/env python3
"""Six per-metric QoE charts for one run, in the style Jaber signed off on.

Ported from sthanav-meet-vimeo-youtube-experiment. The essential idea there is
not the chart list - it is that the *data* is clipped to the QoE sampling window
before anything is plotted:

    window = [first QoE sample, last QoE sample]
    packets outside that window are discarded entirely
    t = 0 means "first QoE sample", not "first captured packet"

Our charts drew the whole capture instead, so a run whose browser took 200s to
be admitted spent most of its x-axis on dead air - and three of six Meet runs
put the call entirely outside the plotted range. Clipping at the data level, as
he does, removes that by construction rather than by choosing nicer limits.

Also carried over: a hard 0-11 Mbps download ceiling so a 3/6/10 Mbps sweep is
comparable side by side, and a jitter-buffer-delay proxy for Meet's buffer
health (a call has no buffer-ahead, but it does have a jitter buffer).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import pramana_helpers as H  # noqa: E402

DOWNLOAD_YMAX = 11.0  # shared across every run so a sweep is comparable
APP_COLOURS = {
    "youtube": "#FF0000",
    "meet": "#00897B",
    "vimeo": "#1AB7EA",
    "tubi": "#FBC02D",
    "twitch": "#9146FF",
    "zoom": "#2D8CFF",
    "unclassified": "#999999",
}


def _stats(row: dict) -> dict:
    return row.get("stats", row)


def load_samples(run_dir: Path, app: str) -> list[dict]:
    p = run_dir / "qoe" / f"{app}_stats.jsonl"
    if not p.is_file():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("record") == "meta" or not isinstance(_stats(r), dict):
            continue
        if r.get("timestamp") is not None:
            out.append(r)
    return out


def per_second_mbps(
    pcap: Path,
    apps: list[str],
    cap: float,
    window: tuple[float, float],
    endpoint_ips: Optional[dict[str, set]] = None,
) -> tuple[dict[str, list[float]], list[int]]:
    """Per-app Mbps in each 1s bin, counting ONLY packets inside `window`."""
    at = H.attribute_capture(pcap, apps, cap, app_endpoint_ips=endpoint_ips)
    w0, w1 = window
    t0 = at.t0_epoch or w0
    lo = max(0, int(math.floor(w0 - t0)))
    hi = max(lo + 1, int(math.ceil(w1 - t0)))
    seconds = list(range(hi - lo))
    out: dict[str, list[float]] = {}
    for app in apps:
        t = at.traffic(app, "download")
        series = (t.mbps if t else []) or []
        out[app] = [series[i] if 0 <= i < len(series) else 0.0 for i in range(lo, hi)]
    return out, seconds


def _fig(title: str, xlabel: str, ylabel: str, size=(10, 4.8)):
    fig, ax = plt.subplots(figsize=size, dpi=140)
    ax.set(title=title, xlabel=xlabel, ylabel=ylabel)
    ax.grid(True, alpha=0.3)
    return fig, ax


def build_report(run_dir: Path, out_dir: Optional[Path] = None) -> list[Path]:
    rec = json.loads((run_dir / "record.json").read_text())
    cfg = rec.get("config", {})
    apps = cfg.get("apps") or []
    cap = float(cfg.get("bandwidth_mbps") or 0)
    out_dir = out_dir or (run_dir / "report")
    out_dir.mkdir(parents=True, exist_ok=True)

    samples = {a: load_samples(run_dir, a) for a in apps}
    samples = {a: s for a, s in samples.items() if s}
    if not samples:
        raise SystemExit(f"no QoE samples under {run_dir}")

    # THE window: first to last QoE sample across every app in the run.
    w0 = min(s[0]["timestamp"] for s in samples.values())
    w1 = max(s[-1]["timestamp"] for s in samples.values())
    rel = {a: [r["timestamp"] - w0 for r in s] for a, s in samples.items()}
    written: list[Path] = []

    def save(fig, name):
        p = out_dir / name
        fig.tight_layout()
        fig.savefig(p, dpi=140, bbox_inches="tight")
        plt.close(fig)
        written.append(p)

    # 01/02 throughput, clipped to the window
    pcap = run_dir / "capture.pcap"
    if pcap.is_file():
        eps = None
        try:
            flds = {"app_urls": cfg.get("app_urls")}
            fake = type("C", (), {"app_urls": flds["app_urls"]})()
            eps = H.app_endpoint_ips_from_urls(fake)
        except Exception:
            eps = None
        mbps, secs = per_second_mbps(pcap, apps, cap, (w0, w1), eps)
        fig, ax = _fig(
            f"Per-application Download Throughput — {cap:g} Mbps",
            "Seconds from synchronized start",
            "Downloaded Mbit in each 1-second bin",
            (10, 5),
        )
        for a in apps:
            ax.plot(
                secs,
                mbps.get(a, []),
                marker="o",
                ms=3,
                color=APP_COLOURS.get(a, "#555"),
                label=H.display_name(a),
            )
        if cap:
            ax.axhline(
                cap,
                color="#333",
                linestyle="--",
                alpha=0.7,
                label=f"Configured bottleneck ({cap:g} Mbps)",
            )
        ax.set_ylim(0, DOWNLOAD_YMAX)
        ax.legend()
        save(fig, "01_download_throughput.png")

    # 03 buffer health: buffer-ahead for VOD, jitter-buffer delay for a call
    fig, ax = _fig(
        f"Buffer Health — {cap:g} Mbps",
        "Seconds from synchronized start",
        "Buffer ahead (s)",
    )
    twin, drew = None, False
    for a, s in samples.items():
        buf = [(_stats(r).get("buffer_ahead_secs") or 0) for r in s]
        if any(v > 0 for v in buf):
            ax.plot(
                rel[a],
                buf,
                marker="o",
                ms=3,
                color=APP_COLOURS.get(a, "#555"),
                label=f"{H.display_name(a)} buffer ahead",
            )
            drew = True
        else:
            # A call has no buffer-ahead. Its jitter buffer is the equivalent
            # signal: how long each frame waited before it could be emitted.
            jd = [
                1000.0
                * (_stats(r).get("jitter_buffer_delay_seconds") or 0)
                / max(1, _stats(r).get("jitter_buffer_emitted_count") or 1)
                for r in s
            ]
            if any(v > 0 for v in jd):
                twin = twin or ax.twinx()
                twin.plot(
                    rel[a],
                    jd,
                    marker="s",
                    ms=3,
                    color=APP_COLOURS.get(a, "#555"),
                    label=f"{H.display_name(a)} jitter buffer delay",
                )
                twin.set_ylabel("Jitter buffer delay (ms per emitted frame)")
                drew = True
    if drew:
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = twin.get_legend_handles_labels() if twin else ([], [])
        ax.legend(h1 + h2, l1 + l2, loc="best")
        save(fig, "03_buffer_health.png")
    else:
        plt.close(fig)

    # 04 cumulative dropped-frame rate
    fig, ax = _fig(
        f"Cumulative Dropped Frame Rate — {cap:g} Mbps",
        "Seconds from synchronized start",
        "Dropped frames (% of decoded frames so far)",
    )
    for a, s in samples.items():
        pct = []
        for r in s:
            st = _stats(r)
            dec = st.get("total_video_frames") or st.get("frames_decoded") or 0
            drp = st.get("dropped_video_frames")
            if drp is None:
                drp = st.get("frames_dropped") or 0
            pct.append(
                100.0 * drp / max(1, dec + (drp if "frames_decoded" in st else 0))
            )
        ax.plot(
            rel[a],
            pct,
            marker="o",
            ms=3,
            color=APP_COLOURS.get(a, "#555"),
            label=H.display_name(a),
        )
    ax.legend(loc="best")
    save(fig, "04_cumulative_dropped_frame_rate.png")

    # 05 resolution over time, ticks labelled WxH
    fig, ax = _fig(
        f"Video Resolution Over Time — {cap:g} Mbps",
        "Seconds from synchronized start",
        "Rendered/decoded resolution",
    )
    labels: dict[int, set] = {}
    for a, s in samples.items():
        hs, ws = [], []
        for r in s:
            st = _stats(r)
            hs.append(st.get("video_height") or st.get("frame_height") or 0)
            ws.append(st.get("video_width") or st.get("frame_width") or 0)
        ax.step(
            rel[a],
            hs,
            where="post",
            linewidth=2,
            color=APP_COLOURS.get(a, "#555"),
            label=H.display_name(a),
        )
        ax.scatter(rel[a], hs, s=18, color=APP_COLOURS.get(a, "#555"))
        for w, h in zip(ws, hs):
            if w and h:
                labels.setdefault(h, set()).add(f"{w}x{h}")
    if labels:
        ticks = sorted(labels)
        ax.set_yticks(ticks)
        ax.set_yticklabels([" / ".join(sorted(labels[h])) for h in ticks])
    ax.legend(loc="best")
    save(fig, "05_video_resolution_over_time.png")

    # 06 cumulative decoded frames
    fig, ax = _fig(
        f"Total Video Frames — {cap:g} Mbps",
        "Seconds from synchronized start",
        "Cumulative total video frames",
    )
    for a, s in samples.items():
        fr = [
            (
                _stats(r).get("total_video_frames")
                or _stats(r).get("frames_decoded")
                or 0
            )
            for r in s
        ]
        ax.plot(
            rel[a],
            fr,
            marker="o",
            ms=3,
            color=APP_COLOURS.get(a, "#555"),
            label=H.display_name(a),
        )
    ax.legend(loc="best")
    save(fig, "06_total_video_frames.png")

    print(
        f"  {run_dir.name}: window {w1 - w0:.1f}s, {len(written)} charts -> {out_dir}"
    )
    return written


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        build_report(Path(arg).expanduser().resolve())
