"""Same window or separate windows for IRTT and NetVibe?

Reads the no-CTP window test (probes_only.py with --mode both / irtt / netvibe,
3 reps each) and asks, per probe and statistic, whether running the probes
together moves the numbers by more than the rep-to-rep spread.

    shift  = |median(together) - median(alone)|
    spread = the larger of the two modes' ranges (max - min across reps)
    shift > spread  ->  the other probe is perturbing this one

Only rows with ok=True are used; failed rows stay in the CSV as a record.

    python3 experiments/cpuc/probes_window_check.py \\
        --csv experiments/cpuc/results/probes_window_test.csv \\
        --runs experiments/cpuc/results/probes_runs/window_test
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

STATS = ("mean", "median", "p95", "max")


def _p95(xs: list[float]) -> float:
    xs = sorted(xs)
    k = (len(xs) - 1) * 0.95
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def run_stats(run_dir: Path, probe: str) -> dict[str, float] | None:
    series = json.loads((run_dir / "rtt_series.json").read_text()).get(probe) or []
    xs = [p["rtt_ms"] for p in series]
    if not xs:
        return None
    return {
        "mean": statistics.fmean(xs),
        "median": statistics.median(xs),
        "p95": _p95(xs),
        "max": max(xs),
        "n": len(xs),
        "over_102ms": sum(1 for x in xs if x > 102.0),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--runs", required=True)
    args = ap.parse_args()
    rows = [r for r in csv.DictReader(open(args.csv))]
    print("per run:")
    by: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        tag = "ok" if r["ok"] == "True" else f"FAILED ({r['error'][:60]})"
        line = f"  {r['mode']:8s} {r['slug'][-6:]}  {tag}"
        for probe in ("irtt", "netvibe"):
            s = run_stats(Path(args.runs) / r["slug"], probe)
            if s is None:
                continue
            line += (
                f"  {probe}: mean {s['mean']:.3f} p95 {s['p95']:.3f} max {s['max']:.2f}"
                f" (>102ms: {s['over_102ms']}/{s['n']})"
            )
            if r["ok"] == "True":
                by.setdefault((probe, r["mode"]), []).append(s)
        print(line)

    print("\ntogether vs alone (ms):")
    verdicts = []
    for probe, alone in (("irtt", "irtt"), ("netvibe", "netvibe")):
        tog, alo = by.get((probe, "both"), []), by.get((probe, alone), [])
        print(f"  {probe}: n together={len(tog)}, n alone={len(alo)}")
        for stat in STATS:
            t = [s[stat] for s in tog]
            a = [s[stat] for s in alo]
            if len(t) < 2 or len(a) < 2:
                continue
            shift = abs(statistics.median(t) - statistics.median(a))
            spread = max(max(t) - min(t), max(a) - min(a))
            moved = shift > spread
            verdicts.append((probe, stat, moved))
            print(
                f"    {stat:6s} together {statistics.median(t):8.3f} "
                f"[{min(t):.3f}-{max(t):.3f}]  alone {statistics.median(a):8.3f} "
                f"[{min(a):.3f}-{max(a):.3f}]  shift {shift:.3f} vs spread {spread:.3f}"
                f"  -> {'MOVED' if moved else 'within spread'}"
            )
    moved = [v for v in verdicts if v[2]]
    print(
        "\nverdict:",
        (
            (
                "SEPARATE windows -- running together moved "
                + ", ".join(f"{p} {s}" for p, s, _ in moved)
            )
            if moved
            else "TOGETHER -- no statistic moved beyond the rep-to-rep spread"
        ),
    )


if __name__ == "__main__":
    main()
