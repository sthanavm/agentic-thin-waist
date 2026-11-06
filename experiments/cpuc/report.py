#!/usr/bin/env python3
"""Turn cpuc_runs.csv into the three things the argument needs.

  1. The table: bandwidth | NDT download | IQB verdict | resolution | rebuffers
     | startup | QoE label. One row per bandwidth point, app and NDT joined on
     group_id. That table IS the argument.
  2. The plot: resolution and rebuffer ratio against bandwidth, with IQB's
     25 Mb/s streaming minimum drawn as a vertical line.
  3. The finding: the bandwidth at which YouTube actually degrades, and how far
     below IQB's minimum that is.

The IQB verdict is computed from the NDT result ONLY -- never from the
configured rate -- because the whole point is what a speed test would have
concluded about this link.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpuc import plots  # noqa: E402
from cpuc.conditions import IQB_VIDEO_STREAMING  # noqa: E402

DL_MIN = IQB_VIDEO_STREAMING["download_min_mbps"]  # 25
DL_HIGH = IQB_VIDEO_STREAMING["download_high_mbps"][0]  # 50, low end of 50-100
UL_MIN = IQB_VIDEO_STREAMING["upload_min_mbps"]  # 10
UL_HIGH = IQB_VIDEO_STREAMING["upload_high_mbps"]  # 10
LAT_MIN = IQB_VIDEO_STREAMING["latency_min_ms"]  # 100
LAT_HIGH = IQB_VIDEO_STREAMING["latency_high_ms"]  # 50


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def iqb_verdict(dl, ul, lat_ms):
    """IQB's own verdict from a speed test: worst of download, upload, latency.

    Returns (verdict, which metric decided it). The high download threshold is
    a RANGE in Fig 10 (50-100 Mb/s); its low end is used, which is the
    generous reading -- it makes "meets high" easier, so it cannot inflate the
    argument that the minimum is too high.
    """
    if dl is None:
        return "no ndt", "download missing"
    tiers = []
    for name, val, mn, hi, lower_is_better in (
        ("download", dl, DL_MIN, DL_HIGH, False),
        ("upload", ul, UL_MIN, UL_HIGH, False),
        ("latency", lat_ms, LAT_MIN, LAT_HIGH, True),
    ):
        if val is None:
            continue
        if lower_is_better:
            tier = 2 if val <= hi else (1 if val <= mn else 0)
        else:
            tier = 2 if val >= hi else (1 if val >= mn else 0)
        tiers.append((tier, name))
    if not tiers:
        return "no ndt", "nothing measured"
    worst, which = min(tiers)
    return {2: "meets high", 1: "meets minimum only", 0: "fails minimum"}[worst], which


def iqb_download_verdict(dl):
    """IQB's verdict on the DOWNLOAD leg alone.

    Reported beside the overall verdict because the argument is specifically
    about the download minimum, while the overall verdict is the worst of three
    legs. Our upload is shaped to 10 Mb/s -- exactly IQB's upload minimum -- and
    TCP at 20 ms RTT does not reliably fill it, so any shortfall there flips the
    overall verdict to "fails minimum" by UPLOAD at every point including the
    reference. That would look like strong support for the download claim while
    actually saying nothing about it.
    """
    if dl is None:
        return "no ndt"
    if dl >= DL_HIGH:
        return "meets high"
    if dl >= DL_MIN:
        return "meets minimum only"
    return "fails minimum"


DEMAND_SIDECAR = os.environ.get(
    "CPUC_YT_DEMAND", "/home/student/cpuc_data/sweep/youtube_demand.json"
)
DEMAND_BYLAT_SIDECAR = os.environ.get(
    "CPUC_YT_DEMAND_BYLAT",
    "/home/student/cpuc_data/sweep/youtube_demand_bylat.json",
)


def load_demand_bylat(path: str | None = None) -> dict:
    """The same figures keyed by (capacity, configured RTT).

    Demand is a property of the run, not of the capacity. The rate-only
    sidecar was correct while every run sat at 20 ms and silently wrong the
    moment a second latency was appended to the CSV: every 50 and 100 ms row
    would have been given the 20 ms demand and the chart would have shown
    three identical curves.
    """
    path = path or DEMAND_BYLAT_SIDECAR
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001 - absent sidecar just means no column
        return {}


def load_demand(path: str | None = None) -> dict:
    """YouTube's OWN throughput per point, from run_direct's attribution.

    The link total includes Chrome's page and infrastructure traffic, so it
    overstates what the player asked for. Measured at 3 Mb/s: the link carried
    2.333 Mb/s against YouTube's own 2.090.
    """
    path = path or DEMAND_SIDECAR
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {}


def load(csv_path):
    with open(csv_path, newline="") as fh:
        return list(csv.DictReader(fh))


def build_table(rows):
    """Join the app row and the NDT row for each bandwidth point."""
    apps, ndts = {}, {}
    for r in rows:
        # Keyed by (capacity, configured RTT). Keying on capacity alone was
        # safe only while every run was 20 ms; once a second latency is
        # appended to the same CSV, a 50 ms row would silently overwrite the
        # 20 ms row at the same capacity and the table would mix regimes.
        key = (_f(r.get("rate_down")), _f(r.get("rtt")))
        if r.get("run_type") == "app":
            # keep the last valid attempt per point
            if r.get("valid") in ("True", "true", "1") or key not in apps:
                apps[key] = r
        elif r.get("run_type") == "ndt":
            if r.get("valid") in ("True", "true", "1") or key not in ndts:
                ndts[key] = r
    out = []
    for key in sorted(set(apps) | set(ndts), key=lambda k: (k[1] or 0, -(k[0] or 0))):
        rate, rtt_cfg = key
        a, n = apps.get(key, {}), ndts.get(key, {})
        dl, ul = _f(n.get("ndt_down")), _f(n.get("ndt_up"))
        lat = _f(n.get("ndt_minrtt"))
        verdict, which = iqb_verdict(dl, ul, lat)
        out.append(
            {
                "rate_down_mbps": rate,
                "rtt_configured_ms": rtt_cfg,
                "is_reference": a.get("is_reference") in ("True", "true", "1"),
                "ndt_down_mbps": dl,
                "ndt_up_mbps": ul,
                "ndt_minrtt_ms": lat,
                "iqb_verdict": verdict,
                "iqb_decided_by": which,
                "iqb_verdict_download_only": iqb_download_verdict(dl),
                "resolution_p": _f(a.get("qoe_resolution_p")),
                "rebuffer_events": _f(a.get("qoe_rebuffer_events")),
                "rebuffer_ratio": _f(a.get("qoe_rebuffer_ratio")),
                "startup_s": _f(a.get("qoe_startup_delay_s")),
                "qoe_label": a.get("qoe_label") or "",
                "suspect": a.get("suspect") in ("True", "true", "1"),
                "app_valid": a.get("valid") in ("True", "true", "1"),
                "shaper_avg_mbps": _f(a.get("shaper_avg_mbps")),
                "app_demand_mbps": None,
                "app_peak_mbps": None,
                "source_max_rendition_p": _f(a.get("source_max_rendition_p")),
                "source_renditions": a.get("source_renditions") or "",
            }
        )
    demand = load_demand()
    bylat = load_demand_bylat()
    for row in out:
        rtt = row.get("rtt_configured_ms")
        d = {}
        if rtt is not None:
            d = bylat.get(f"{row['rate_down_mbps']:g}@{rtt:g}") or {}
        if not d and rtt in (None, 20.0):
            # The rate-only sidecar describes the 20 ms pass, so it is never
            # used to fill a row measured at another latency.
            d = (
                demand.get(f"{row['rate_down_mbps']:g}")
                or demand.get(str(row["rate_down_mbps"]))
                or {}
            )
        row["app_demand_mbps"] = d.get("youtube_avg_mbps")
        row["app_peak_mbps"] = d.get("youtube_peak_mbps")
        row["app_total_mb"] = d.get("youtube_total_mb")
    return out


def latency_caption(table) -> str:
    """ "20 ms set, 22.6-23.3 ms measured" -- both numbers, from the data.

    Jaber asked for the configured and the measured RTT together wherever
    latency appears. A chart title that says only "20 ms" is a statement
    about the shaper; what the speed test saw on the same link is the one a
    reader can check, and the two are not equal (netem adds its delay on top
    of the real path).
    """
    cfg = sorted({r["rtt_configured_ms"] for r in table if r.get("rtt_configured_ms")})
    meas = [r["ndt_minrtt_ms"] for r in table if r.get("ndt_minrtt_ms")]
    if not cfg:
        return "no induced loss"
    setpart = ", ".join(f"{c:g}" for c in cfg) + " ms set"
    if not meas:
        return f"{setpart}, no loss"
    lo, hi = min(meas), max(meas)
    span = f"{lo:.1f} ms" if abs(hi - lo) < 0.05 else f"{lo:.1f}-{hi:.1f} ms"
    return f"{setpart}, {span} measured, no loss"


def markdown_table(table):
    hdr = (
        "| down Mb/s | RTT set | RTT measured | NDT down | IQB (download) "
        "| IQB (all legs) | source offers | resolution chosen | rebuffers "
        "| startup | QoE |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    lines = []
    for t in table:
        tag = " *(ref)*" if t["is_reference"] else ""
        sus = " ⚠" if t["suspect"] else ""
        lines.append(
            "| {r:g}{tag} | {rset} | {rmeas} | {dl} | {vd} | {v} ({by}) "
            "| {src} | {res} | {reb} | {su} | {q}{sus} |".format(
                r=t["rate_down_mbps"],
                tag=tag,
                rset=(
                    f"{t['rtt_configured_ms']:g} ms"
                    if t.get("rtt_configured_ms") is not None
                    else "—"
                ),
                rmeas=(
                    f"{t['ndt_minrtt_ms']:.1f} ms"
                    if t.get("ndt_minrtt_ms") is not None
                    else "—"
                ),
                dl=(
                    f"{t['ndt_down_mbps']:.2f}"
                    if t["ndt_down_mbps"] is not None
                    else "—"
                ),
                vd=t["iqb_verdict_download_only"],
                v=t["iqb_verdict"],
                by=t["iqb_decided_by"],
                src=(
                    f"up to {t['source_max_rendition_p']:.0f}p"
                    if t.get("source_max_rendition_p")
                    else "—"
                ),
                res=f"{t['resolution_p']:.0f}p" if t["resolution_p"] else "—",
                reb=(
                    f"{t['rebuffer_events']:.0f}"
                    if t["rebuffer_events"] is not None
                    else "—"
                ),
                su=f"{t['startup_s']:.2f} s" if t["startup_s"] is not None else "—",
                q=t["qoe_label"] or "—",
                sus=sus,
            )
        )
    return hdr + "\n".join(lines)


def find_degradation(table):
    """The lowest bandwidth still delivering reference resolution with no rebuffering.

    "Degrades" is defined before looking: resolution below 90% of the
    reference, OR any rebuffering, OR startup at/over 5 s -- the plan doc's own
    High criteria. The answer is the first point walking DOWN the rates where
    that becomes true.
    """
    ref = next((t for t in table if t["is_reference"]), None)
    ref_res = ref["resolution_p"] if ref else None
    ok_points, first_bad = [], None
    for t in sorted(
        (x for x in table if not x["is_reference"] and x["resolution_p"]),
        key=lambda x: -x["rate_down_mbps"],
    ):
        degraded = False
        if ref_res and t["resolution_p"] < 0.9 * ref_res:
            degraded = True
        if (t["rebuffer_events"] or 0) > 0:
            degraded = True
        if (t["startup_s"] or 0) >= 5.0:
            degraded = True
        if degraded:
            first_bad = first_bad or t
        else:
            ok_points.append(t)
    lowest_ok = min((t["rate_down_mbps"] for t in ok_points), default=None)
    return {
        "reference_resolution_p": ref_res,
        "lowest_good_mbps": lowest_ok,
        "first_degraded_mbps": first_bad["rate_down_mbps"] if first_bad else None,
        "iqb_minimum_mbps": DL_MIN,
        "factor_below_iqb": (DL_MIN / lowest_ok) if lowest_ok else None,
        "points_good_below_iqb": sorted(
            t["rate_down_mbps"] for t in ok_points if t["rate_down_mbps"] < DL_MIN
        ),
        # Stated on the download leg alone, so the upload leg cannot be
        # mistaken for support for a claim about the download minimum.
        # The sharper version of the finding: the source could have gone
        # higher, so the rendition chosen is a player decision about the link,
        # not a ceiling imposed by the content.
        "source_max_rendition_p": next(
            (
                t.get("source_max_rendition_p")
                for t in table
                if t.get("source_max_rendition_p")
            ),
            None,
        ),
        "points_where_source_exceeded_delivered": sorted(
            t["rate_down_mbps"]
            for t in table
            if t.get("source_max_rendition_p")
            and t.get("resolution_p")
            and t["source_max_rendition_p"] > t["resolution_p"]
        ),
        "points_iqb_download_would_fail_but_video_good": sorted(
            t["rate_down_mbps"]
            for t in ok_points
            if t["iqb_verdict_download_only"] == "fails minimum"
        ),
        "points_iqb_all_legs_would_fail_but_video_good": sorted(
            t["rate_down_mbps"]
            for t in ok_points
            if t["iqb_verdict"] == "fails minimum"
        ),
    }


def plot(table, path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pts = [t for t in table if t["resolution_p"] and not t["is_reference"]]
    pts.sort(key=lambda t: t["rate_down_mbps"])
    if not pts:
        return None
    x = [t["rate_down_mbps"] for t in pts]
    res = [t["resolution_p"] for t in pts]
    reb = [(t["rebuffer_ratio"] or 0.0) * 100 for t in pts]

    fig, ax1 = plt.subplots(figsize=(9, 5.2))
    ax1.set_xscale("log")
    ax1.plot(x, res, "o-", color="#1565C0", lw=2, ms=7, label="resolution (p)")
    ax1.set_xlabel("shaped download capacity (Mb/s, log scale)")
    ax1.set_ylabel("video resolution (p)", color="#1565C0")
    ax1.tick_params(axis="y", labelcolor="#1565C0")
    ax1.set_ylim(0, max(res) * 1.25)

    ax2 = ax1.twinx()
    ax2.plot(x, reb, "s--", color="#C62828", lw=2, ms=6, label="rebuffer ratio (%)")
    ax2.set_ylabel("rebuffer ratio (%)", color="#C62828")
    ax2.tick_params(axis="y", labelcolor="#C62828")
    ax2.set_ylim(0, max(max(reb) * 1.3, 1.0))

    ax1.axvline(DL_MIN, color="#424242", ls=":", lw=2)
    ax1.annotate(
        f"IQB streaming minimum\n{DL_MIN:g} Mb/s",
        xy=(DL_MIN, max(res) * 1.12),
        xytext=(DL_MIN * 0.42, max(res) * 1.12),
        color="#424242",
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
        f"YouTube QoE against shaped capacity (auto quality, {latency_caption(table)})"
    )
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="/home/student/cpuc_data/sweep/cpuc_runs.csv")
    ap.add_argument("--outdir", default="/home/student/cpuc_data/sweep")
    args = ap.parse_args()

    rows = load(args.csv)
    table = build_table(rows)
    finding = find_degradation(table)

    os.makedirs(args.outdir, exist_ok=True)
    tbl_csv = os.path.join(args.outdir, "iqb_vs_qoe_table.csv")
    with open(tbl_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(table[0].keys()))
        w.writeheader()
        w.writerows(table)

    md = os.path.join(args.outdir, "iqb_vs_qoe_table.md")
    with open(md, "w") as fh:
        fh.write(markdown_table(table) + "\n")

    png = plot(table, os.path.join(args.outdir, "qoe_vs_bandwidth.png"))

    # One chart per claim; see cpuc/plots.py for why each one exists.
    made = [png]
    made.append(
        plots.contrast(
            table,
            os.path.join(args.outdir, "contrast_iqb_vs_qoe.png"),
            DL_MIN,
            "rate_down_mbps",
            "iqb_verdict_download_only",
            f"IQB's verdict against measured YouTube QoE ({latency_caption(table)})",
        )
    )
    made.append(
        plots.demand_vs_capacity(
            table,
            os.path.join(args.outdir, "demand_vs_capacity.png"),
            DL_MIN,
            "rate_down_mbps",
            "app_demand_mbps",
            "YouTube",
            "What YouTube actually used against what the link offered",
            peak_key="app_peak_mbps",
        )
    )
    made.append(
        plots.single_metric(
            table,
            os.path.join(args.outdir, "startup_vs_bandwidth.png"),
            DL_MIN,
            "rate_down_mbps",
            "startup_s",
            "startup delay (s)",
            "Startup delay against capacity",
            thresholds=(
                (5.0, "5 s -- above this a run cannot be High", "--"),
                (10.0, "10 s -- above this a run is Unusable", "-."),
            ),
        )
    )
    made.append(
        plots.single_metric(
            table,
            os.path.join(args.outdir, "rebuffer_vs_bandwidth.png"),
            DL_MIN,
            "rate_down_mbps",
            "rebuffer_ratio",
            "rebuffer ratio (% of session)",
            "Rebuffering against capacity",
            scale=100.0,
            colour="#C62828",
            thresholds=((5.0, "5% -- above this a run is Unusable", "-."),),
        )
    )
    made.append(
        plots.source_vs_delivered(
            table,
            os.path.join(args.outdir, "source_vs_delivered.png"),
            DL_MIN,
            "rate_down_mbps",
            "What the source offered against what the player chose",
        )
    )
    made = [m for m in made if m]

    with open(os.path.join(args.outdir, "finding.json"), "w") as fh:
        json.dump(finding, fh, indent=1)

    print(markdown_table(table))
    print()
    print("FINDING:", json.dumps(finding, indent=1))
    print(f"\nwrote: {tbl_csv}\n       {md}")
    for m in made:
        print(f"       {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
