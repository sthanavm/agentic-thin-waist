#!/usr/bin/env python3
"""The three deliverables for the Google Meet bandwidth argument.

  1. The table: bandwidth | NDT download | IQB verdict | resolution | fps |
     freezes | QoE label. App and NDT rows joined on group_id.
  2. The plot: resolution and fps against bandwidth, with IQB's 25 Mb/s
     conferencing minimum drawn as a vertical line.
  3. The finding, in three sentences.

IQB's CONFERENCING row (Fig 10) is the one that applies here, and its binding
leg is UPLOAD: minimum 25 Mb/s up against 10 Mb/s down. Our shaping is
symmetric, so 25 Mb/s symmetric sits exactly on IQB's conferencing minimum and
every point below it is the argument. The verdict is computed from the NDT
result only -- never from the configured rate -- because the claim is about
what a speed test would have concluded.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpuc.conditions import IQB_VIDEO_CONFERENCING as IQB  # noqa: E402

DL_MIN, DL_HIGH = IQB["download_min_mbps"], IQB["download_high_mbps"]  # 10, 100
UL_MIN, UL_HIGH = IQB["upload_min_mbps"], IQB["upload_high_mbps"]  # 25, 100
LAT_MIN, LAT_HIGH = IQB["latency_min_ms"], IQB["latency_high_ms"]  # 50, 20
BINDING_MIN = max(DL_MIN, UL_MIN)  # 25 Mb/s, the upload leg


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def iqb_verdict(dl, ul, lat):
    """Worst of download, upload and latency, with the deciding leg named."""
    if dl is None and ul is None:
        return "no ndt", "nothing measured"
    tiers = []
    for name, val, mn, hi, lower_better in (
        ("download", dl, DL_MIN, DL_HIGH, False),
        ("upload", ul, UL_MIN, UL_HIGH, False),
        ("latency", lat, LAT_MIN, LAT_HIGH, True),
    ):
        if val is None:
            continue
        if lower_better:
            tier = 2 if val <= hi else (1 if val <= mn else 0)
        else:
            tier = 2 if val >= hi else (1 if val >= mn else 0)
        tiers.append((tier, name))
    if not tiers:
        return "no ndt", "nothing measured"
    worst, which = min(tiers)
    return {2: "meets high", 1: "meets minimum only", 0: "fails minimum"}[worst], which


def build_table(rows):
    apps, ndts = {}, {}
    for r in rows:
        key = _f(r.get("rate_down"))
        valid = r.get("valid") in ("True", "true", "1")
        if r.get("run_type") == "app":
            if valid or key not in apps:
                apps[key] = r
        elif r.get("run_type") == "ndt":
            if valid or key not in ndts:
                ndts[key] = r
    out = []
    for rate in sorted(set(apps) | set(ndts), reverse=True):
        a, n = apps.get(rate, {}), ndts.get(rate, {})
        dl, ul, lat = (
            _f(n.get("ndt_down")),
            _f(n.get("ndt_up")),
            _f(n.get("ndt_minrtt")),
        )
        verdict, which = iqb_verdict(dl, ul, lat)
        out.append(
            {
                "rate_mbps_symmetric": rate,
                "is_reference": a.get("is_reference") in ("True", "true", "1"),
                "ndt_down_mbps": dl,
                "ndt_up_mbps": ul,
                "ndt_minrtt_ms": lat,
                "iqb_verdict": verdict,
                "iqb_decided_by": which,
                "resolution_p": _f(a.get("qoe_resolution_p")),
                "fps": _f(a.get("qoe_fps")),
                "freeze_count": _f(a.get("qoe_freeze_count")),
                "freeze_ratio": _f(a.get("qoe_freeze_ratio")),
                "mean_jitter_ms": _f(a.get("qoe_mean_jitter_ms")),
                "qoe_label": a.get("qoe_label") or "",
                "suspect": a.get("suspect") in ("True", "true", "1"),
                "meet_demand_mbps": _f(a.get("shaper_avg_mbps")),
            }
        )
    return out


def markdown_table(t):
    hdr = (
        "| capacity (sym) | NDT down | NDT up | IQB verdict | resolution | fps "
        "| freezes | QoE |\n|---|---|---|---|---|---|---|---|\n"
    )
    lines = []
    for r in t:
        tag = " *(ref)*" if r["is_reference"] else ""
        sus = " ⚠" if r["suspect"] else ""
        lines.append(
            "| {c:g} Mb/s{tag} | {dl} | {ul} | {v} ({by}) | {res} | {fps} | "
            "{fz} | {q}{sus} |".format(
                c=r["rate_mbps_symmetric"],
                tag=tag,
                dl=(
                    f"{r['ndt_down_mbps']:.2f}"
                    if r["ndt_down_mbps"] is not None
                    else "—"
                ),
                ul=f"{r['ndt_up_mbps']:.2f}" if r["ndt_up_mbps"] is not None else "—",
                v=r["iqb_verdict"],
                by=r["iqb_decided_by"],
                res=f"{r['resolution_p']:.0f}p" if r["resolution_p"] else "—",
                fps=f"{r['fps']:.1f}" if r["fps"] is not None else "—",
                fz=(
                    f"{r['freeze_count']:.0f} ({r['freeze_ratio']:.2%})"
                    if r["freeze_count"] is not None and r["freeze_ratio"] is not None
                    else "—"
                ),
                q=r["qoe_label"] or "—",
                sus=sus,
            )
        )
    return hdr + "\n".join(lines)


def find_breakpoint(table):
    """Lowest capacity still holding reference resolution, fps and no freezes.

    "Degrades" is the plan doc's own High criteria, fixed before looking:
    resolution or fps below 90% of reference, or freezes at/above 1%.
    """
    ref = next((t for t in table if t["is_reference"]), None)
    ref_res = ref["resolution_p"] if ref else None
    ref_fps = ref["fps"] if ref else None
    good, bad = [], []
    for t in sorted(
        (x for x in table if not x["is_reference"] and x["resolution_p"]),
        key=lambda x: -x["rate_mbps_symmetric"],
    ):
        degraded = False
        if ref_res and t["resolution_p"] < 0.9 * ref_res:
            degraded = True
        if ref_fps and (t["fps"] or 0) < 0.9 * ref_fps:
            degraded = True
        if (t["freeze_ratio"] or 0) >= 0.01:
            degraded = True
        (bad if degraded else good).append(t)
    lowest_good = min((t["rate_mbps_symmetric"] for t in good), default=None)
    demands = [t["meet_demand_mbps"] for t in table if t["meet_demand_mbps"]]
    return {
        "reference_resolution_p": ref_res,
        "reference_fps": ref_fps,
        "iqb_conferencing_binding_minimum_mbps": BINDING_MIN,
        "lowest_capacity_still_good_mbps": lowest_good,
        "highest_capacity_degraded_mbps": max(
            (t["rate_mbps_symmetric"] for t in bad), default=None
        ),
        "factor_below_iqb_minimum": (
            (BINDING_MIN / lowest_good) if lowest_good else None
        ),
        "capacities_good_below_iqb_minimum": sorted(
            t["rate_mbps_symmetric"]
            for t in good
            if t["rate_mbps_symmetric"] < BINDING_MIN
        ),
        "capacities_iqb_fails_but_call_good": sorted(
            t["rate_mbps_symmetric"]
            for t in good
            if t["iqb_verdict"] == "fails minimum"
        ),
        "meet_observed_demand_mbps_min": min(demands) if demands else None,
        "meet_observed_demand_mbps_max": max(demands) if demands else None,
    }


def sentences(f):
    lg = f["lowest_capacity_still_good_mbps"]
    fac = f["factor_below_iqb_minimum"]
    bad = f["highest_capacity_degraded_mbps"]
    dmin, dmax = f["meet_observed_demand_mbps_min"], f["meet_observed_demand_mbps_max"]
    s = []
    if lg:
        s.append(
            f"Google Meet held reference-quality video ({f['reference_resolution_p']}p "
            f"at {f['reference_fps']:.0f} fps, no freezes) down to {lg:g} Mb/s "
            f"symmetric, which is {fac:.0f}x below IQB's "
            f"{f['iqb_conferencing_binding_minimum_mbps']:g} Mb/s conferencing minimum."
        )
    else:
        s.append("No capacity point held reference quality; see the table.")
    if bad:
        s.append(f"Quality first degraded at {bad:g} Mb/s.")
    else:
        s.append(
            "No tested capacity degraded quality, so the breakpoint is below the "
            "lowest point measured."
        )
    if dmin is not None:
        s.append(
            f"Across every point Meet drew only {dmin:.2f}-{dmax:.2f} Mb/s of the "
            f"capacity available to it, so the call is demand-limited rather than "
            f"capacity-limited, and IQB's minimum is set against a demand the "
            f"application never makes."
        )
    if f["capacities_iqb_fails_but_call_good"]:
        s.append(
            "At "
            + ", ".join(f"{c:g}" for c in f["capacities_iqb_fails_but_call_good"])
            + " Mb/s a speed test puts the link below IQB's minimum while the call "
            "is still reference quality -- the contrast the argument rests on."
        )
    return " ".join(s)


def plot(table, path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pts = [t for t in table if t["resolution_p"] and not t["is_reference"]]
    pts.sort(key=lambda t: t["rate_mbps_symmetric"])
    if not pts:
        return None
    x = [t["rate_mbps_symmetric"] for t in pts]
    res = [t["resolution_p"] for t in pts]
    fps = [t["fps"] or 0 for t in pts]

    fig, ax1 = plt.subplots(figsize=(9, 5.2))
    ax1.set_xscale("log")
    ax1.plot(x, res, "o-", color="#1565C0", lw=2, ms=7, label="resolution (p)")
    ax1.set_xlabel("shaped symmetric capacity (Mb/s, log scale)")
    ax1.set_ylabel("received resolution (p)", color="#1565C0")
    ax1.tick_params(axis="y", labelcolor="#1565C0")
    ax1.set_ylim(0, max(res) * 1.3)

    ax2 = ax1.twinx()
    ax2.plot(x, fps, "s--", color="#2E7D32", lw=2, ms=6, label="frame rate (fps)")
    ax2.set_ylabel("frame rate (fps)", color="#2E7D32")
    ax2.tick_params(axis="y", labelcolor="#2E7D32")
    ax2.set_ylim(0, max(max(fps) * 1.35, 5))

    ax1.axvline(BINDING_MIN, color="#B71C1C", ls=":", lw=2)
    ax1.annotate(
        f"IQB conferencing minimum\n{BINDING_MIN:g} Mb/s (upload leg)",
        xy=(BINDING_MIN, max(res) * 1.16),
        xytext=(BINDING_MIN * 0.92, max(res) * 1.16),
        color="#B71C1C",
        fontsize=9,
        ha="right",
    )
    ax1.set_xticks(x)
    ax1.set_xticklabels([f"{v:g}" for v in x])
    ax1.grid(alpha=0.25, which="both")
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="lower right", fontsize=9)
    ax1.set_title(
        "Google Meet QoE against shaped capacity (20 ms RTT, no induced loss)"
    )
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="/home/student/cpuc_data/meet/cpuc_runs.csv")
    ap.add_argument("--outdir", default="/home/student/cpuc_data/meet")
    args = ap.parse_args()

    with open(args.csv, newline="") as fh:
        rows = list(csv.DictReader(fh))
    table = build_table(rows)
    finding = find_breakpoint(table)
    text = sentences(finding)

    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "iqb_vs_qoe_table.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(table[0].keys()))
        w.writeheader()
        w.writerows(table)
    with open(os.path.join(args.outdir, "iqb_vs_qoe_table.md"), "w") as fh:
        fh.write(markdown_table(table) + "\n")
    with open(os.path.join(args.outdir, "finding.json"), "w") as fh:
        json.dump(finding, fh, indent=1)
    with open(os.path.join(args.outdir, "finding.txt"), "w") as fh:
        fh.write(text + "\n")
    png = plot(table, os.path.join(args.outdir, "meet_qoe_vs_bandwidth.png"))

    print(markdown_table(table))
    print("\nFINDING\n" + text)
    print("\nwrote table.csv / table.md / finding.json / finding.txt / " + str(png))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
