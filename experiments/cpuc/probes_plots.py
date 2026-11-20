"""Plots for probe-only CTP runs (probes_only.py output).

  * one RTT timeseries per run, IRTT and NetVibe on the same RTT axis
  * one comparison figure: per-run RTT p95 for every CTP profile against the
    no-CTP baseline, one panel per probe

    python3 experiments/cpuc/probes_plots.py \\
        --csv experiments/cpuc/results/probes_ctp_runs.csv \\
        --runs experiments/cpuc/results/probes_runs/collect \\
        --out experiments/cpuc/results/probes_plots
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Validated categorical slots 1-2 (light surface): blue, orange.
COLOR = {"irtt": "#2a78d6", "netvibe": "#eb6834"}
LABEL = {"irtt": "IRTT", "netvibe": "NetVibe"}
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(INK2)
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_run(run_dir: Path, out: Path) -> bool:
    series = json.loads((run_dir / "rtt_series.json").read_text())
    rec = json.loads((run_dir / "record.json").read_text())
    probes = [p for p in ("irtt", "netvibe") if series.get(p)]
    if not probes:
        return False
    fig, ax = plt.subplots(figsize=(9, 3.6), facecolor=SURFACE)
    _style(ax)
    for p in probes:
        xs = [s["t_s"] for s in series[p]]
        ys = [s["rtt_ms"] for s in series[p]]
        ax.plot(xs, ys, color=COLOR[p], linewidth=2, label=LABEL[p], alpha=0.9)
        ax.annotate(
            LABEL[p],
            (xs[-1], ys[-1]),
            xytext=(6, 0),
            textcoords="offset points",
            color=INK2,
            fontsize=9,
            va="center",
        )
    ctp = rec.get("ctp_profile") or "no CTP"
    ax.set_title(
        f"Probe RTT, {ctp}  ({rec['condition']}, mode={rec['mode']}, rep {rec['rep']})",
        color=INK,
        fontsize=10,
        loc="left",
    )
    ax.set_xlabel("time since probe start (s)", color=INK2, fontsize=9)
    ax.set_ylabel("round-trip time (ms)", color=INK2, fontsize=9)
    if len(probes) > 1:
        ax.legend(frameon=False, fontsize=9, labelcolor=INK2, loc="upper left")
    fig.tight_layout()
    fig.savefig(out, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    return True


def _short(name: str) -> str:
    """Display name: the repo sample's subnet keeps its /20; corpus names wrap at _."""
    return (
        name.replace("ctp-ctp_samples-", "sample ")
        .replace(".0_20-", ".0/20 ")
        .replace("_tree", " tree")
        .replace("_profile", " profile")
    )


def plot_comparison(rows: list[dict], out: Path) -> None:
    groups = ["none"] + sorted({r["ctp_profile"] for r in rows} - {"none"})
    labels = ["no CTP\n(baseline)"] + [textwrap.fill(_short(g), 18) for g in groups[1:]]
    fig, axes = plt.subplots(
        1,
        2,
        figsize=(max(7, 1.3 * len(groups) + 3), 4.2),
        sharey=True,
        facecolor=SURFACE,
    )
    for ax, p in zip(axes, ("irtt", "netvibe")):
        _style(ax)
        for i, g in enumerate(groups):
            vals = [
                float(r[f"{p}_rtt_p95_ms"])
                for r in rows
                if r["ctp_profile"] == g and r.get(f"{p}_rtt_p95_ms") not in ("", None)
            ]
            if not vals:
                continue
            jitter = [(k - (len(vals) - 1) / 2) * 0.08 for k in range(len(vals))]
            ax.scatter(
                [i + j for j in jitter],
                vals,
                s=40,
                color=COLOR[p],
                edgecolors=SURFACE,
                linewidths=1.5,
                zorder=5,
            )
            med = statistics.median(vals)
            ax.hlines(med, i - 0.25, i + 0.25, color=INK, linewidth=2, zorder=4)
            ax.annotate(
                f"{med:.0f}",
                (i + 0.27, med),
                color=INK2,
                fontsize=8,
                va="center",
            )
        ax.set_xticks(range(len(groups)))
        ax.set_xticklabels(labels, fontsize=8, color=INK2)
        ax.set_title(f"{LABEL[p]} RTT p95 per run", color=INK, fontsize=10, loc="left")
    axes[0].set_ylabel("RTT p95 (ms); bar = median of runs", color=INK2, fontsize=9)
    fig.tight_layout()
    fig.savefig(out, dpi=130, facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--runs", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(open(args.csv)))
    n = 0
    for r in rows:
        run_dir = Path(args.runs) / r["slug"]
        if run_dir.is_dir() and plot_run(run_dir, out / f"{r['slug']}_rtt.png"):
            n += 1
    plot_comparison(rows, out / "probes_ctp_vs_baseline.png")
    print(f"wrote {n} run timeseries + probes_ctp_vs_baseline.png to {out}")


if __name__ == "__main__":
    main()
