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

from cpuc import plots  # noqa: E402
from cpuc.conditions import IQB_VIDEO_CONFERENCING as IQB  # noqa: E402
from cpuc.qoe_labels import (  # noqa: E402
    ConferencingMetrics,
    label_conferencing,
)

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


DEMAND_SIDECAR = os.environ.get(
    "CPUC_MEET_DEMAND", "/home/student/cpuc_data/meet/meet_demand.json"
)

# Per-(capacity, RTT) demand. The rate-only sidecar above predates the latency
# sweeps and cannot tell them apart, so reading it across latencies printed the
# 20 ms figure in all three rows: Meet drew 3.689 Mb/s on a 100 Mb/s link at
# 20 ms but 1.449 at 50 ms, and the table claimed both were 3.689.
DEMAND_BYLAT_SIDECAR = os.environ.get(
    "CPUC_MEET_DEMAND_BYLAT",
    "/home/student/cpuc_data/meet/meet_demand_bylat.json",
)


def load_demand_bylat(path: str | None = None) -> dict:
    path = path or DEMAND_BYLAT_SIDECAR
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001 - absent sidecar just means no column
        return {}


def load_demand(path: str | None = None) -> dict:
    """Meet's OWN throughput per capacity point, from run_direct's attribution.

    The row's `shaper_avg_mbps` is the whole shaped download direction, which
    includes Chrome's page and infrastructure traffic -- fonts.gstatic.com,
    www.google.com and the gvt1 component updater. Measured on the 3 Mb/s run
    the link total was 2.031 Mb/s while Meet's own stream was 1.561, so using
    the link total as "what Meet demanded" overstates it by ~30%. This sidecar
    carries the per-app figure so the demand claim is about the application.
    """
    path = path or DEMAND_SIDECAR
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {}


# Points the matrix asked for, so a missing one is reported as NOT COLLECTED
# rather than silently absent from the curve.
EXPECTED_RATES = (100.0, 25.0, 6.0, 3.0, 1.0, 0.5)

# Points where "no remote video arrived" cannot be read as a measurement of the
# link: the persistent peer stopped publishing at about the time these runs
# were taken, and the meeting ended during that batch. The SAME two capacities
# at 50 ms failed while the peer was VERIFIED still publishing, so those are
# genuine and are deliberately not listed here.
#
# Keyed (capacity Mb/s, configured RTT ms). An explicit list rather than
# something inferred from the row, because the peer's state is not in the CSV
# -- so it is written down where a reader can check it against the batch log,
# instead of living in a PR comment.
CONFOUNDED_POINTS = {
    (1.0, 100.0): "peer stopped publishing during this batch",
    (0.5, 100.0): "peer stopped publishing during this batch",
}


def grade_reference(t: dict) -> dict:
    """Label the reference run against ITSELF, at analysis time.

    Every run is graded relative to its app's reference, so the reference has
    nothing to be graded against while the batch is still collecting and is
    written out as `deferred` -- qoe_labels says "label assigned in analysis".
    This is that analysis. The control condition is what the whole argument
    leans on, and leaving the best link in the study with no quality verdict
    reads as a gap rather than as a baseline.

    Grading it against itself is not a free pass: the resolution and fps tests
    become trivially true, but the freeze test is absolute and still applies,
    so a reference run that froze more than 1% of the time does not come out
    `high`. That is why this goes through the same labeller rather than
    writing "high" into the table.
    """
    if t.get("qoe_label") != "deferred" or not t.get("is_reference"):
        return t
    res, fps = t.get("resolution_p"), t.get("fps")
    if res is None or fps is None or t.get("freeze_ratio") is None:
        return t
    m = ConferencingMetrics(
        resolution_p=int(res),
        fps=fps,
        freeze_ratio=t.get("freeze_ratio"),
        freeze_count=t.get("freeze_count"),
        connected=True,
    )
    label, reason = label_conferencing(m, int(res), fps)
    t["qoe_label"] = label
    t["qoe_reason"] = reason
    t["graded_against_self"] = True
    return t


def mark_confounded(t: dict) -> dict:
    """Separate a measured absence of video from one that cannot be attributed.

    Three of the five "no remote media" cells are measurements: the call ran
    and no remote video arrived at that capacity. Two are not. Rendering all
    five identically invites the table to be read as a clean curve down to
    0.5 Mb/s at every latency, which is the one thing this data cannot say.
    """
    key = (t.get("rate_mbps_symmetric"), t.get("rtt_configured_ms"))
    why = CONFOUNDED_POINTS.get(key)
    if not why or t.get("qoe_label") not in ("no remote media", "not collected"):
        return t
    t["confounded"] = True
    t["confounded_reason"] = why
    t["qoe_label"] = "no remote media (confounded)"
    return t


def build_table(rows):
    apps, ndts = {}, {}
    for r in rows:
        # Keyed by (capacity, configured RTT). Keying on capacity alone was
        # safe only while every run was 20 ms; once a second latency is
        # appended to the same CSV, a 50 ms row would silently overwrite the
        # 20 ms row at the same capacity and the table would mix regimes
        # without saying so.
        key = (_f(r.get("rate_down")), _f(r.get("rtt")))
        valid = r.get("valid") in ("True", "true", "1")
        if r.get("run_type") == "app":
            if valid or key not in apps:
                apps[key] = r
        elif r.get("run_type") == "ndt":
            if valid or key not in ndts:
                ndts[key] = r
    out = []
    for key in sorted(set(apps) | set(ndts), key=lambda k: (k[1] or 0, -(k[0] or 0))):
        rate, rtt_cfg = key
        a, n = apps.get(key, {}), ndts.get(key, {})
        dl, ul, lat = (
            _f(n.get("ndt_down")),
            _f(n.get("ndt_up")),
            _f(n.get("ndt_minrtt")),
        )
        verdict, which = iqb_verdict(dl, ul, lat)
        out.append(
            {
                "rate_mbps_symmetric": rate,
                "rtt_configured_ms": rtt_cfg,
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
                "link_total_avg_mbps": _f(a.get("shaper_avg_mbps")),
                "app_valid": a.get("valid") in ("True", "true", "1"),
                "invalid_reason": (a.get("invalid_reason") or "")[:160],
            }
        )

    # Attach Meet's own throughput, and add a NOT COLLECTED row for any
    # expected capacity point that produced no valid app run.
    demand = load_demand()
    bylat = load_demand_bylat()
    seen = {t["rate_mbps_symmetric"] for t in out}
    for t in out:
        rtt = t.get("rtt_configured_ms")
        d = {}
        if rtt is not None:
            d = bylat.get(f"{t['rate_mbps_symmetric']:g}@{rtt:g}") or {}
        if not d and rtt in (None, 20.0):
            # The rate-only sidecar describes the 20 ms pass, so it is never
            # used to fill a row measured at another latency.
            d = (
                demand.get(f"{t['rate_mbps_symmetric']:g}")
                or demand.get(str(t["rate_mbps_symmetric"]))
                or {}
            )
        t["browser_infra_mb"] = d.get("bucket_browser_infra_mb")
        if t["app_valid"]:
            t["meet_demand_mbps"] = d.get("meet_avg_mbps")
            t["meet_peak_mbps"] = d.get("meet_peak_mbps")
        else:
            # A run that never established remote media still moved some
            # bytes while it tried to join. Reporting those bytes as "what
            # Meet demanded" would draw a demand curve that appears to scale
            # gracefully down to 0.23 Mb/s, when in fact the call failed. If
            # the QoE is not collected, neither is the demand.
            t["meet_demand_mbps"] = None
            t["meet_peak_mbps"] = None
            # "Not collected" and "we ran it and no video ever arrived" are
            # different claims, and only the second one is evidence about the
            # link. A run that reached NoRemoteInboundMedia on every attempt
            # IS a measurement: the call did not carry remote video at that
            # capacity. Collapsing it into "not collected" would throw away
            # the lowest point on the curve.
            reason = t.get("invalid_reason") or ""
            if "NoRemoteInboundMedia" in reason:
                t["qoe_label"] = "no remote media"
            else:
                t["qoe_label"] = "not collected"
    for rate in EXPECTED_RATES:
        if rate not in seen:
            out.append(
                {
                    "rate_mbps_symmetric": rate,
                    "rtt_configured_ms": None,
                    "is_reference": rate == 100.0,
                    "ndt_down_mbps": None,
                    "ndt_up_mbps": None,
                    "ndt_minrtt_ms": None,
                    "iqb_verdict": "no ndt",
                    "iqb_decided_by": "not collected",
                    "resolution_p": None,
                    "fps": None,
                    "freeze_count": None,
                    "freeze_ratio": None,
                    "mean_jitter_ms": None,
                    "qoe_label": "not collected",
                    "suspect": False,
                    "link_total_avg_mbps": None,
                    "app_valid": False,
                    "invalid_reason": "no valid run",
                    "meet_demand_mbps": None,
                    "meet_peak_mbps": None,
                    "browser_infra_mb": None,
                }
            )
    out.sort(key=lambda t: -t["rate_mbps_symmetric"])
    return [mark_confounded(grade_reference(r)) for r in out]


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


def markdown_table(t):
    hdr = (
        "| capacity (sym) | RTT set | RTT measured | NDT down | NDT up "
        "| IQB verdict | resolution | fps | freezes | Meet demand | QoE |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    lines = []
    for r in t:
        tag = " *(ref)*" if r["is_reference"] else ""
        sus = " ⚠" if r["suspect"] else ""
        lines.append(
            "| {c:g} Mb/s{tag} | {rset} | {rmeas} | {dl} | {ul} | {v} ({by}) "
            "| {res} | {fps} | {fz} | {dem} | {q}{sus} |".format(
                c=r["rate_mbps_symmetric"],
                tag=tag,
                rset=(
                    f"{r['rtt_configured_ms']:g} ms"
                    if r.get("rtt_configured_ms") is not None
                    else "—"
                ),
                rmeas=(
                    f"{r['ndt_minrtt_ms']:.1f} ms"
                    if r.get("ndt_minrtt_ms") is not None
                    else "—"
                ),
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
                dem=(
                    f"{r['meet_demand_mbps']:.2f}"
                    if r.get("meet_demand_mbps") is not None
                    else "—"
                ),
                q=r["qoe_label"] or "—",
                sus=sus,
            )
        )
    notes = []
    if any(r.get("graded_against_self") for r in t):
        notes.append(
            "*(ref)* is the reference run, graded against itself: its "
            "resolution and fps are 100% of their own, but the freeze test "
            "is absolute, so the label is earned rather than granted."
        )
    if any(r.get("confounded") for r in t):
        notes.append(
            "**no remote media (confounded)** is NOT evidence about the link: "
            "the persistent peer stopped publishing during that batch, so an "
            "absence of remote video cannot be told apart from the meeting "
            "having ended. The same two capacities at 50 ms failed while the "
            "peer was verified still publishing, and those rows are plain "
            "**no remote media**."
        )
    out = hdr + "\n".join(lines)
    if notes:
        out += "\n\n" + "\n\n".join(notes)
    return out


def by_latency(table):
    """[(rtt, rows)] ascending, or [(None, table)] when nothing is tagged."""
    lats = sorted({r["rtt_configured_ms"] for r in table if r.get("rtt_configured_ms")})
    if not lats:
        return [(None, table)]
    return [
        (lat, [r for r in table if r.get("rtt_configured_ms") == lat]) for lat in lats
    ]


def latency_sentences(table) -> str:
    """How the curve moves with RTT, one clause per latency.

    find_breakpoint() answers "where does the call stop being good", and that
    question has a different answer at each latency. Run over the whole table
    it would mix three regimes and report one number that describes none of
    them, so each latency is summarised from its own rows.
    """
    groups = [(lat, g) for lat, g in by_latency(table) if lat is not None]
    if len(groups) < 2:
        return ""
    parts = []
    for lat, g in groups:
        good = [
            r["rate_mbps_symmetric"]
            for r in g
            if r.get("qoe_label") == "high" and r.get("rate_mbps_symmetric")
        ]
        bad = [
            r["rate_mbps_symmetric"]
            for r in g
            if r.get("qoe_label") in ("unusable", "no remote media")
            and r.get("rate_mbps_symmetric")
        ]
        if good:
            clause = f"at {lat:g} ms it held reference quality to {min(good):g} Mb/s"
            if bad:
                clause += f" and failed by {max(bad):g} Mb/s"
            parts.append(clause)
    if not parts:
        return ""
    return (
        "Across latency, "
        + "; ".join(parts)
        + ". Every latency was measured on the same capacities, so the "
        "comparison is like for like."
    )


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


def sentences(f, table=None):
    """The finding, stated so it cannot be overturned by the data behind it.

    Two things are deliberately NOT claimed. First, that the call is simply
    "demand-limited": that holds only where Meet's peak stayed below the cap
    (100 and 25 Mb/s). At 6, 3 and 1 Mb/s its peak equalled the cap, so it was
    capacity-limited there and still delivered reference quality -- a stronger
    statement, but a different one. Second, that quality broke at the bottom:
    the 1 Mb/s point produced no valid run, so the breakpoint is below the
    lowest point actually measured, not at it.
    """
    table = table or []
    lg = f["lowest_capacity_still_good_mbps"]
    fac = f["factor_below_iqb_minimum"]
    iqb = f["iqb_conferencing_binding_minimum_mbps"]
    s = []
    if lg:
        s.append(
            f"Google Meet held reference-quality video "
            f"({f['reference_resolution_p']:.0f}p at {f['reference_fps']:.0f} fps, "
            f"freezes under 1%) down to {lg:g} Mb/s symmetric, "
            f"{fac:.0f}x below IQB's {iqb:g} Mb/s conferencing minimum."
        )
    else:
        s.append("No capacity point held reference quality; see the table.")

    contrast = f.get("capacities_iqb_fails_but_call_good") or []
    if contrast:
        s.append(
            "At "
            + ", ".join(f"{c:g}" for c in sorted(contrast, reverse=True))
            + " Mb/s a speed test puts the link below IQB's minimum while the "
            "call is still reference quality, which is the contrast the "
            "argument rests on."
        )

    # Demand-limited vs capacity-limited, decided per point rather than asserted.
    demand_ltd, cap_ltd = [], []
    for r in table:
        pk, cap = r.get("meet_peak_mbps"), r.get("rate_mbps_symmetric")
        if pk is None or not cap or not r.get("app_valid"):
            continue
        (cap_ltd if pk >= 0.95 * cap else demand_ltd).append(cap)
    if demand_ltd and cap_ltd:
        # Being capacity-limited and still looking good is the interesting
        # claim, and it stops being true at the point where quality breaks.
        # Splitting the capacity-limited points by their own label keeps the
        # sentence from sweeping an unusable point into "still delivered
        # reference quality".
        good_cap = sorted(
            (
                r["rate_mbps_symmetric"]
                for r in table
                if r.get("meet_peak_mbps") is not None
                and r.get("rate_mbps_symmetric")
                and r.get("app_valid")
                and r["meet_peak_mbps"] >= 0.95 * r["rate_mbps_symmetric"]
                and r.get("qoe_label") == "high"
            ),
            reverse=True,
        )
        line = (
            "Meet was demand-limited at "
            + ", ".join(f"{c:g}" for c in sorted(demand_ltd, reverse=True))
            + " Mb/s, where its peak stayed well under the cap, but "
            "capacity-limited at "
            + ", ".join(f"{c:g}" for c in sorted(cap_ltd, reverse=True))
            + " Mb/s, where its peak reached the cap"
        )
        if good_cap:
            line += (
                " and it still delivered reference quality at "
                + ", ".join(f"{c:g}" for c in good_cap)
                + " Mb/s."
            )
        else:
            line += "."
        s.append(line)
    elif demand_ltd:
        s.append(
            "Meet never reached the cap at any measured point, so it was "
            "demand-limited throughout."
        )

    # Where the call actually breaks. This is the question the low end was
    # run to answer, so it is stated outright rather than left to the table.
    broke = sorted(
        (
            r["rate_mbps_symmetric"]
            for r in table
            if r.get("qoe_label") == "unusable" and r.get("rate_mbps_symmetric")
        ),
        reverse=True,
    )
    nomedia = sorted(
        (
            r["rate_mbps_symmetric"]
            for r in table
            if r.get("qoe_label") == "no remote media" and r.get("rate_mbps_symmetric")
        ),
        reverse=True,
    )
    if broke:
        line = f"Quality breaks between {lg:g} and {broke[0]:g} Mb/s: " if lg else ""
        r1 = next(r for r in table if r["rate_mbps_symmetric"] == broke[0])
        line += (
            f"at {broke[0]:g} Mb/s the call fell to "
            f"{r1['resolution_p']:.0f}p at {r1['fps']:.0f} fps with "
            f"{r1['freeze_ratio'] * 100:.0f}% of the session frozen, which is "
            "Unusable by the pre-registered thresholds."
        )
        s.append(line)
    if nomedia:
        s.append(
            "At "
            + ", ".join(f"{c:g}" for c in nomedia)
            + " Mb/s no remote video arrived at all, in two attempts, so the "
            "call did not carry video rather than carrying it badly."
        )

    missing = [
        r["rate_mbps_symmetric"] for r in table if r.get("qoe_label") == "not collected"
    ]
    if missing:
        s.append(
            "The "
            + ", ".join(f"{c:g}" for c in sorted(missing))
            + " Mb/s point was not collected."
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
    ax1.set_title(f"Google Meet QoE against shaped capacity ({latency_caption(table)})")
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
    # The headline comes from the 20 ms set, which is the one with a complete
    # curve; the latency comparison is then stated across all three.
    groups = by_latency(table)
    primary = next((g for lat, g in groups if lat == 20.0), table)
    finding = find_breakpoint(primary)
    finding["latencies_ms"] = [lat for lat, _ in groups if lat is not None]
    text = sentences(finding, primary)
    extra = latency_sentences(table)
    if extra:
        text = text + " " + extra

    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "iqb_vs_qoe_table.csv"), "w", newline="") as fh:
        # Union, not table[0]: rows carry keys the first row does not.
        # A reference row gains graded_against_self and a confounded
        # row gains confounded/confounded_reason, and keying off one
        # row raises ValueError the moment that happens.
        cols = list(dict.fromkeys(k for r in table for k in r))
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(table)
    with open(os.path.join(args.outdir, "iqb_vs_qoe_table.md"), "w") as fh:
        fh.write(markdown_table(table) + "\n")
    with open(os.path.join(args.outdir, "finding.json"), "w") as fh:
        json.dump(finding, fh, indent=1)
    with open(os.path.join(args.outdir, "finding.txt"), "w") as fh:
        fh.write(text + "\n")
    png = plot(table, os.path.join(args.outdir, "meet_qoe_vs_bandwidth.png"))

    # One chart per claim; see cpuc/plots.py.
    made = [png]
    made.append(
        plots.contrast(
            table,
            os.path.join(args.outdir, "contrast_iqb_vs_qoe.png"),
            BINDING_MIN,
            "rate_mbps_symmetric",
            "iqb_verdict",
            f"IQB's verdict against measured Meet QoE ({latency_caption(table)})",
        )
    )
    made.append(
        plots.demand_vs_capacity(
            table,
            os.path.join(args.outdir, "demand_vs_capacity.png"),
            BINDING_MIN,
            "rate_mbps_symmetric",
            "meet_demand_mbps",
            "Meet",
            "What Meet actually used against what the link offered",
            peak_key="meet_peak_mbps",
        )
    )
    made.append(
        plots.single_metric(
            table,
            os.path.join(args.outdir, "fps_vs_bandwidth.png"),
            BINDING_MIN,
            "rate_mbps_symmetric",
            "fps",
            "received frame rate (fps)",
            "Frame rate against capacity",
            colour="#2E7D32",
        )
    )
    made.append(
        plots.single_metric(
            table,
            os.path.join(args.outdir, "freezes_vs_bandwidth.png"),
            BINDING_MIN,
            "rate_mbps_symmetric",
            "freeze_ratio",
            "freeze time (% of call)",
            "Freezing against capacity",
            scale=100.0,
            colour="#C62828",
            thresholds=(
                (1.0, "1% -- above this a call cannot be High", "--"),
                (10.0, "10% -- above this a call is Unusable", "-."),
            ),
        )
    )
    made = [m for m in made if m]

    print(markdown_table(table))
    print("\nFINDING\n" + text)
    print("\nwrote table.csv / table.md / finding.json / finding.txt")
    for m in made:
        print(f"       {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
