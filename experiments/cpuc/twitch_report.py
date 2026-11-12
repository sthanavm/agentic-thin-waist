#!/usr/bin/env python3
"""Turn twitch_runs.csv into the published Twitch table -- three reps per point.

WHY THIS IS NOT report.py. `report.build_table` keys the table by
(capacity, configured RTT) and keeps the last valid row at each key. That was
right while every point was n=1. Run it over a three-rep sweep and it silently
publishes one arbitrary repetition and throws the other two away -- the sweep
would have cost three times as long and the table would say exactly what an
n=1 table said, with no sign that reps existed. So this module publishes TWO
tables from the same rows:

  twitch_runs_table.csv  -- one row per run, 24 rows, every repetition visible
  twitch_iqb_vs_qoe_table.{csv,md} -- one row per capacity, the three reps
                            reduced with the spread kept beside the centre

Both carry the columns `demand_gate` reads (`rate_down_mbps`,
`app_demand_mbps`, `resolution_p`, `infra_share_pct`, `rtt_configured_ms`), so
the gate runs over every published row in either table rather than over a
summary of them:

    python3 -m cpuc.demand_gate <outdir>/twitch_runs_table.csv \
                                <outdir>/twitch_iqb_vs_qoe_table.csv

REDUCING THREE REPS. The centre is the MEDIAN, not the mean: with n=3 the
median is the middle measurement, so one bad run cannot drag the published
figure a third of the way toward itself. The spread is carried as min and max
in their own columns and printed in the markdown, because the reason for
running reps at all was that nobody could see the spread. Demand is the one
exception -- it is averaged, because that is what `demand.by_capacity` already
publishes for the other sweeps and a second convention for the same column
would be worse than the inconsistency of this one. `demand_n` says how many
runs went into it.

LABELS ACROSS REPS. If the three reps do not agree on the QoE label the cell
reads `mixed (high x2, acceptable x1)` and `qoe_label_agrees` is False. A
disagreement is a finding about the point, not noise to be averaged away, and
picking the modal label would hide it.

THE GUARD travels with every row. `suspect` on a run means the
background-traffic guard found browser_infra or `other` taking more than its
budget inside the app's own measured window; the run is kept and flagged
rather than discarded (see guard.py). The aggregate carries `reps_suspect`,
the count out of n, so a point whose reps were all contaminated cannot read
like a clean one.
"""

from __future__ import annotations

import argparse
import collections
import csv
import glob
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpuc import plots  # noqa: E402
from cpuc.report import (  # noqa: E402
    DL_MIN,
    _f,
    grade_reference,
    iqb_download_verdict,
    iqb_verdict,
    latency_caption,
)

OUT_PREFIX = "twitch_"
TRUTHY = ("True", "true", "1")


def _b(v) -> bool:
    return v in TRUTHY


def load(csv_path: str) -> list[dict]:
    with open(csv_path, newline="") as fh:
        return list(csv.DictReader(fh))


def load_demand(path: str) -> dict:
    """Per-RUN demand, keyed by run directory basename.

    The other sweeps read a per-capacity sidecar because they had one run per
    capacity. Here three runs share a capacity, so the join has to be per run
    or the three rows in the per-run table would all show the same averaged
    demand -- a number none of them measured.
    """
    try:
        with open(path) as fh:
            rows = json.load(fh)
    except Exception:  # noqa: BLE001 - absent sidecar just means no column
        return {}
    return {r["run"]: r for r in rows}


RES_FIELDS = (
    "resolution_modal_p",
    "resolution_modal_share",
    "resolution_first_p",
    "resolution_final_p",
    "resolution_samples",
    "resolution_timeline",
)


def resolution_detail(run_dir: str) -> dict:
    """What the rendition did over the session, not just which one won the vote.

    `video_resolution_p` -- the figure every table in this study publishes as
    "resolution" -- is `Counter(active_heights).most_common(1)[0][0]`, the
    MODAL sample height (shared/qoe.py:514). That is the right summary for a
    session that sat at one rendition, and it is silently the wrong thing to
    read as "the rendition this session ran at" when the player moved: at
    1.5 Mb/s Twitch spent 36 of 60 samples at 144p and then climbed, ending at
    360-480p, and the published cell says `144p` with nothing to say the
    session was still rising when the window closed.

    That mismatch is also the whole explanation for the demand gate's two
    flags here. Demand is a mean over the session; the gate's bands describe a
    steady rendition. A session that is not steady can be above the band for
    its modal rendition without any figure being wrong -- so the modal share
    and the final rendition are published beside it, and the reader can see
    which rows are steady and which are a ramp.
    """
    out = {k: None for k in RES_FIELDS}
    files = sorted(glob.glob(os.path.join(run_dir, "qoe", "*_stats.jsonl")))
    if not files:
        return out
    tl, t0 = [], None
    for line in open(files[0]):
        try:
            j = json.loads(line)
        except Exception:  # noqa: BLE001 - a truncated line is not a timeline
            continue
        if j.get("record") == "meta":
            continue
        st = j.get("stats") or {}
        h, ts = st.get("video_height"), j.get("timestamp")
        if t0 is None and ts is not None:
            t0 = ts
        if h and ts is not None:
            tl.append((ts - t0, int(h)))
    if not tl:
        return out
    counts = collections.Counter(h for _t, h in tl)
    modal, n = counts.most_common(1)[0]
    spans: list[list] = []
    for t, h in tl:
        if spans and spans[-1][1] == h:
            spans[-1][2] = t
        else:
            spans.append([t, h, t])
    out.update(
        {
            "resolution_modal_p": float(modal),
            "resolution_modal_share": round(n / len(tl), 3),
            "resolution_first_p": float(tl[0][1]),
            "resolution_final_p": float(tl[-1][1]),
            "resolution_samples": len(tl),
            "resolution_timeline": " ".join(
                f"{h}p@{a:.0f}-{b:.0f}s" for a, h, b in spans
            ),
        }
    )
    return out


def _run_key(row: dict) -> str:
    return os.path.basename((row.get("run_dir") or "").rstrip("/"))


def per_run_table(rows: list[dict], demand: dict) -> list[dict]:
    """One row per run, app joined to its own NDT run by (capacity, RTT, rep).

    The NDT run is the one taken next to that repetition, not the capacity's
    only speed test, so each published row's IQB verdict comes from the link
    as it was measured during that repetition.
    """
    ndts = {}
    for r in rows:
        if r.get("run_type") == "ndt":
            ndts[(_f(r.get("rate_down")), _f(r.get("rtt")), r.get("rep"))] = r

    out = []
    for a in rows:
        if a.get("run_type") != "app":
            continue
        key = (_f(a.get("rate_down")), _f(a.get("rtt")), a.get("rep"))
        n = ndts.get(key, {})
        dl, ul = _f(n.get("ndt_down")), _f(n.get("ndt_up"))
        lat = _f(n.get("ndt_minrtt"))
        verdict, which = iqb_verdict(dl, ul, lat)
        d = demand.get(_run_key(a), {})
        t = {
            "rate_down_mbps": key[0],
            "rate_up_mbps": _f(a.get("rate_up")),
            "rtt_configured_ms": key[1],
            "rep": _f(a.get("rep")),
            "is_reference": _b(a.get("is_reference")),
            "channel": a.get("channel_url") or "",
            "ndt_down_mbps": dl,
            "ndt_up_mbps": ul,
            "ndt_minrtt_ms": lat,
            "ndt_server": n.get("ndt_server") or "",
            "iqb_verdict": verdict,
            "iqb_decided_by": which,
            "iqb_verdict_download_only": iqb_download_verdict(dl),
            "resolution_p": _f(a.get("qoe_resolution_p")),
            "resolutions_observed": a.get("qoe_resolutions_observed") or "",
            "rebuffer_events": _f(a.get("qoe_rebuffer_events")),
            "rebuffer_ratio": _f(a.get("qoe_rebuffer_ratio")),
            "rebuffer_ms": _f(a.get("qoe_rebuffer_duration_ms")),
            "startup_s": _f(a.get("qoe_startup_delay_s")),
            "startup_basis": a.get("qoe_startup_basis") or "",
            "session_s": _f(a.get("qoe_session_seconds")),
            "played": _b(a.get("qoe_played")),
            "qoe_label": a.get("qoe_label") or "",
            "qoe_reason": a.get("qoe_reason") or "",
            "app_valid": _b(a.get("valid")),
            "suspect": _b(a.get("suspect")),
            "suspect_reason": a.get("suspect_reason") or "",
            "guard_ok": _b(a.get("guard_ok")),
            "guard_checked": _b(a.get("guard_checked")),
            "suppression_ok": _b(a.get("suppression_ok")),
            "shaper_avg_mbps": _f(a.get("shaper_avg_mbps")),
            "shaper_peak_mbps": _f(a.get("shaper_peak_mbps")),
            # Demand from the run's own capture, not from a capacity average.
            "app_demand_mbps": d.get("demand_window_mbps"),
            "app_active_mbps": d.get("demand_active_mbps"),
            "app_peak_mbps": d.get("peak_mbps"),
            "app_total_mb": d.get("app_total_mb"),
            "infra_share_pct": d.get("infra_share_pct"),
            "demand_window_span_s": d.get("window_span_s"),
            "demand_basis": d.get("basis") or "",
            "source_max_rendition_p": _f(a.get("source_max_rendition_p")),
            "source_renditions": a.get("source_renditions") or "",
            "source_transcode_count": _f(a.get("source_transcode_count")),
            "run_dir": a.get("run_dir") or "",
        }
        t.update(resolution_detail(a.get("run_dir") or ""))
        # The published resolution and the modal height are the same
        # definition read from two places. If they ever disagree, one of them
        # is stale -- say so rather than publish both.
        if (
            t["resolution_modal_p"] is not None
            and t["resolution_p"] is not None
            and t["resolution_modal_p"] != t["resolution_p"]
        ):
            print(
                f"!! {t['run_dir']}: published resolution {t['resolution_p']:g}p "
                f"but the samples are modal {t['resolution_modal_p']:g}p",
                file=sys.stderr,
            )
        t["resolution_steady"] = (
            None
            if t["resolution_modal_share"] is None
            else t["resolution_modal_share"] >= 0.95
        )
        out.append(grade_reference(t))
    out.sort(
        key=lambda t: (
            t["rtt_configured_ms"] or 0,
            -(t["rate_down_mbps"] or 0),
            t["rep"] or 0,
        )
    )
    return out


def _med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _spread(vals):
    vals = [v for v in vals if v is not None]
    return (min(vals), max(vals)) if vals else (None, None)


def _label_cell(labels: list[str]) -> tuple[str, bool]:
    kept = [x for x in labels if x]
    if not kept:
        return "", True
    uniq = sorted(set(kept))
    if len(uniq) == 1:
        return uniq[0], True
    counts = {x: kept.count(x) for x in uniq}
    parts = ", ".join(
        f"{k} x{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
    )
    return f"mixed ({parts})", False


def aggregate(runs: list[dict]) -> list[dict]:
    """One row per (capacity, RTT), the reps reduced with their spread kept."""
    buckets: dict[tuple, list[dict]] = {}
    for t in runs:
        buckets.setdefault((t["rate_down_mbps"], t["rtt_configured_ms"]), []).append(t)

    out = []
    for (rate, rtt), rs in sorted(
        buckets.items(), key=lambda kv: (kv[0][1] or 0, -(kv[0][0] or 0))
    ):
        dl = _med([r["ndt_down_mbps"] for r in rs])
        ul = _med([r["ndt_up_mbps"] for r in rs])
        lat = _med([r["ndt_minrtt_ms"] for r in rs])
        verdict, which = iqb_verdict(dl, ul, lat)
        res_lo, res_hi = _spread([r["resolution_p"] for r in rs])
        su_lo, su_hi = _spread([r["startup_s"] for r in rs])
        dl_lo, dl_hi = _spread([r["ndt_down_mbps"] for r in rs])
        dem_lo, dem_hi = _spread([r["app_demand_mbps"] for r in rs])
        label, agrees = _label_cell([r["qoe_label"] for r in rs])
        out.append(
            {
                "rate_down_mbps": rate,
                "rate_up_mbps": rs[0]["rate_up_mbps"],
                "rtt_configured_ms": rtt,
                "n_reps": len(rs),
                "is_reference": any(r["is_reference"] for r in rs),
                # IQB's verdict is computed from the MEDIAN speed test of the
                # reps, never from the configured rate -- same rule as the
                # streaming table.
                "ndt_down_mbps": dl,
                "ndt_down_min": dl_lo,
                "ndt_down_max": dl_hi,
                "ndt_up_mbps": ul,
                "ndt_minrtt_ms": lat,
                "iqb_verdict": verdict,
                "iqb_decided_by": which,
                "iqb_verdict_download_only": iqb_download_verdict(dl),
                "resolution_p": _med([r["resolution_p"] for r in rs]),
                "resolution_p_min": res_lo,
                "resolution_p_max": res_hi,
                "resolution_agrees": res_lo == res_hi,
                "rebuffer_events": _med([r["rebuffer_events"] for r in rs]),
                # Both ends of the spread are stored. The max alone was
                # published for a moment beside the median, which makes the
                # printed range read median-to-max and quietly hides a
                # repetition that did not rebuffer at all.
                "rebuffer_events_min": _spread([r["rebuffer_events"] for r in rs])[0],
                "rebuffer_events_max": _spread([r["rebuffer_events"] for r in rs])[1],
                "rebuffer_ratio": _med([r["rebuffer_ratio"] for r in rs]),
                "reps_with_rebuffering": sum(
                    1 for r in rs if (r["rebuffer_events"] or 0) > 0
                ),
                "startup_s": _med([r["startup_s"] for r in rs]),
                "startup_s_min": su_lo,
                "startup_s_max": su_hi,
                "qoe_label": label,
                "qoe_label_agrees": agrees,
                "app_demand_mbps": _mean([r["app_demand_mbps"] for r in rs]),
                "app_demand_min": dem_lo,
                "app_demand_max": dem_hi,
                "app_peak_mbps": _mean([r["app_peak_mbps"] for r in rs]),
                "infra_share_pct": _mean([r["infra_share_pct"] for r in rs]),
                "demand_n": sum(1 for r in rs if r["app_demand_mbps"] is not None),
                "demand_basis": next(
                    (r["demand_basis"] for r in rs if r["demand_basis"]), ""
                ),
                "reps_suspect": sum(1 for r in rs if r["suspect"]),
                "reps_valid": sum(1 for r in rs if r["app_valid"]),
                "resolution_final_p": _med([r["resolution_final_p"] for r in rs]),
                "resolution_modal_share": _mean(
                    [r["resolution_modal_share"] for r in rs]
                ),
                "reps_steady": sum(1 for r in rs if r["resolution_steady"]),
                # Which way a non-steady point moved. Both directions put the
                # demand mean above the band for the modal rendition, and the
                # reader needs to know which one happened here.
                "reps_ramping_up": sum(
                    1
                    for r in rs
                    if r["resolution_final_p"]
                    and r["resolution_modal_p"]
                    and r["resolution_final_p"] > r["resolution_modal_p"]
                ),
                "reps_downshifting": sum(
                    1
                    for r in rs
                    if r["resolution_final_p"]
                    and r["resolution_modal_p"]
                    and r["resolution_final_p"] < r["resolution_modal_p"]
                ),
                "source_max_rendition_p": rs[0]["source_max_rendition_p"],
                "source_renditions": rs[0]["source_renditions"],
                "channel": rs[0]["channel"],
            }
        )
    return out


def markdown_table(table: list[dict]) -> str:
    hdr = (
        "| down Mb/s | RTT set | RTT measured | NDT down (median of n) "
        "| IQB (download) | IQB (all legs) | source offers | resolution "
        "| rebuffers | startup | demand Mb/s | infra share | QoE (n=3) |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    lines = []
    for t in table:
        n = t["n_reps"]
        tag = " *(ref)*" if t["is_reference"] else ""
        sus = f" ⚠{t['reps_suspect']}/{n}" if t["reps_suspect"] else ""

        def rng(lo, hi, fmt="{:.0f}", unit=""):
            if lo is None:
                return "—"
            if hi is None or abs(hi - lo) < 1e-9:
                return fmt.format(lo) + unit
            return f"{fmt.format(lo)}–{fmt.format(hi)}{unit}"

        lines.append(
            "| {r:g}{tag} | {rset} | {rmeas} | {dl} | {vd} | {v} ({by}) | {src} "
            "| {res} | {reb} | {su} | {dem} | {inf} | {q}{sus} |".format(
                r=t["rate_down_mbps"],
                tag=tag,
                rset=(
                    f"{t['rtt_configured_ms']:g} ms"
                    if t["rtt_configured_ms"] is not None
                    else "—"
                ),
                rmeas=(
                    f"{t['ndt_minrtt_ms']:.1f} ms"
                    if t["ndt_minrtt_ms"] is not None
                    else "—"
                ),
                dl=(
                    f"{t['ndt_down_mbps']:.2f} ({rng(t['ndt_down_min'], t['ndt_down_max'], '{:.2f}')})"
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
                res=rng(t["resolution_p_min"], t["resolution_p_max"], "{:.0f}", "p"),
                reb=rng(t["rebuffer_events_min"], t["rebuffer_events_max"]),
                su=rng(t["startup_s_min"], t["startup_s_max"], "{:.2f}", " s"),
                dem=rng(t["app_demand_min"], t["app_demand_max"], "{:.2f}"),
                inf=(
                    f"{t['infra_share_pct']:.0f}%"
                    if t["infra_share_pct"] is not None
                    else "—"
                ),
                q=t["qoe_label"] or "—",
                sus=sus,
            )
        )
    notes = [
        f"every cell is **n={table[0]['n_reps'] if table else 0}** unless it says "
        "otherwise: the centre is the median of the repetitions and the bracket "
        "or range is their full spread, min to max. A single number means the "
        "repetitions agreed exactly.",
        "**demand** is the mean over the sustained playback window (first app "
        "byte to last), per run, averaged over the repetitions; **infra share** "
        "is the rest of the shaped link over the same window.",
    ]
    if any(t["reps_suspect"] for t in table):
        notes.append(
            "⚠n/m means the background-traffic guard failed for n of the m "
            "repetitions at that point: Chrome's own non-media traffic took "
            "more than 10% of the cap in more bins than the budget allows, "
            "inside the app's measured window. Those runs are kept and "
            "flagged, not discarded."
        )
    if any(t["reps_steady"] < t["n_reps"] for t in table):
        notes.append(
            "**resolution** is the modal sample height, which is what every "
            "table in this study publishes (shared/qoe.py:514). Where the "
            "repetitions were not steady the point carries `reps_steady`, "
            "`reps_ramping_up`, `reps_downshifting` and a per-run "
            "`resolution_timeline` in the CSVs, because a modal cell alone "
            "reads as a session that sat at that rendition."
        )
    if any(not t["qoe_label_agrees"] for t in table):
        notes.append(
            "`mixed (...)` means the repetitions disagreed on the QoE label; "
            "the counts are shown rather than a modal label."
        )
    if any(r.get("graded_against_self") for r in table):
        notes.append(
            "*(ref)* is the reference point, graded against itself; the "
            "rebuffer and startup tests are absolute, so the label is earned."
        )
    return hdr + "\n".join(lines) + "\n\n" + "\n\n".join(notes)


def find_degradation(table: list[dict], runs: list[dict]) -> dict:
    """The lowest capacity still delivering reference quality in EVERY rep.

    "Degrades" is the plan doc's High criteria, unchanged: resolution below
    90% of the reference, OR any rebuffering, OR startup at/over 5 s. What
    three reps add is the quantifier -- a point passes only if all of its
    repetitions pass. Taking the best rep would publish the luckiest run in
    the sweep as the answer.
    """
    ref_res = next(
        (t["resolution_p"] for t in table if t["is_reference"] and t["resolution_p"]),
        None,
    )
    ok, first_bad, ungradeable = [], None, []
    for t in sorted(
        (x for x in table if not x["is_reference"] and x["resolution_p"]),
        key=lambda x: -x["rate_down_mbps"],
    ):
        rs = [
            r
            for r in runs
            if r["rate_down_mbps"] == t["rate_down_mbps"]
            and r["rtt_configured_ms"] == t["rtt_configured_ms"]
        ]
        if any(r["rebuffer_events"] is None or r["startup_s"] is None for r in rs):
            ungradeable.append(t)
            continue
        bad = False
        for r in rs:
            if ref_res and r["resolution_p"] and r["resolution_p"] < 0.9 * ref_res:
                bad = True
            if (r["rebuffer_events"] or 0) > 0:
                bad = True
            if (r["startup_s"] or 0) >= 5.0:
                bad = True
        if bad:
            first_bad = first_bad or t
        else:
            ok.append(t)
    lowest_ok = min((t["rate_down_mbps"] for t in ok), default=None)
    return {
        "app": "twitch",
        "channel": table[0]["channel"] if table else "",
        "reps_per_point": table[0]["n_reps"] if table else 0,
        "criterion": (
            "every repetition at the point must hold reference resolution "
            "(>=90%), rebuffer zero times, and start in under 5 s"
        ),
        "reference_resolution_p": ref_res,
        "lowest_good_mbps": lowest_ok,
        "first_degraded_mbps": first_bad["rate_down_mbps"] if first_bad else None,
        "ungradeable_mbps": sorted(t["rate_down_mbps"] for t in ungradeable),
        "iqb_minimum_mbps": DL_MIN,
        "factor_below_iqb": (DL_MIN / lowest_ok) if lowest_ok else None,
        "points_good_below_iqb": sorted(
            t["rate_down_mbps"] for t in ok if t["rate_down_mbps"] < DL_MIN
        ),
        "points_iqb_download_would_fail_but_video_good": sorted(
            t["rate_down_mbps"]
            for t in ok
            if t["iqb_verdict_download_only"] == "fails minimum"
        ),
        "source_max_rendition_p": next(
            (
                t["source_max_rendition_p"]
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
        "points_where_reps_disagreed_on_label": sorted(
            t["rate_down_mbps"] for t in table if not t["qoe_label_agrees"]
        ),
        "points_where_reps_disagreed_on_resolution": sorted(
            t["rate_down_mbps"] for t in table if not t["resolution_agrees"]
        ),
        "points_not_steady_in_every_rep": sorted(
            t["rate_down_mbps"] for t in table if t["reps_steady"] < t["n_reps"]
        ),
        "points_ramping_up_at_window_close": sorted(
            t["rate_down_mbps"] for t in table if t["reps_ramping_up"]
        ),
        "points_downshifting_at_window_close": sorted(
            t["rate_down_mbps"] for t in table if t["reps_downshifting"]
        ),
        "points_with_any_suspect_rep": sorted(
            t["rate_down_mbps"] for t in table if t["reps_suspect"]
        ),
    }


def rep_spread(runs: list[dict], path: str, title: str) -> str | None:
    """Every repetition drawn, not a mean with the spread hidden in a column.

    This chart is the reason the sweep was run three times. One marker per
    run: if the three markers at a capacity sit on top of each other the point
    is repeatable, and if they scatter the reader sees it without being told.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pts = [r for r in runs if r["rate_down_mbps"] and r["resolution_p"] is not None]
    if not pts:
        return None
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7.4), sharex=True)
    for ax, key, ylab, colour in (
        (ax1, "resolution_p", "resolution delivered (p)", "#1565C0"),
        (ax2, "startup_s", "startup delay (s)", "#6A1B9A"),
    ):
        xs = [r["rate_down_mbps"] for r in pts if r[key] is not None]
        ys = [r[key] for r in pts if r[key] is not None]
        ax.set_xscale("log")
        ax.scatter(
            xs, ys, s=46, color=colour, alpha=0.65, zorder=3, label="one repetition"
        )
        caps = sorted({r["rate_down_mbps"] for r in pts})
        meds = [_med([r[key] for r in pts if r["rate_down_mbps"] == c]) for c in caps]
        ax.plot(caps, meds, "-", color=colour, lw=1.8, zorder=2, label="median of 3")
        ax.axvline(DL_MIN, color="#424242", ls=":", lw=2)
        ax.set_ylabel(ylab, color=colour)
        ax.tick_params(axis="y", labelcolor=colour)
        ax.grid(alpha=0.25, which="both")
        ax.legend(fontsize=8, loc="best")
        ax.set_xticks(caps)
        ax.set_xticklabels([f"{c:g}" for c in caps])
    ax2.axhline(5.0, color="#C62828", ls="--", lw=1.4)
    ax2.axhline(10.0, color="#C62828", ls="-.", lw=1.4)
    ax2.set_xlabel("shaped download capacity (Mb/s, log scale)")
    ax1.set_title(title)
    # Anchored in axes fraction, not in data units: at 1080p the data-unit
    # version sat on top of the resolution line.
    ax1.annotate(
        f"IQB streaming minimum {DL_MIN:g} Mb/s",
        xy=(DL_MIN, 0.06),
        xycoords=("data", "axes fraction"),
        xytext=(5, 0),
        textcoords="offset points",
        fontsize=8,
        color="#424242",
        ha="left",
    )
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="/home/student/cpuc_data/twitch/twitch_runs.csv")
    ap.add_argument(
        "--demand", default="/home/student/cpuc_data/twitch/twitch_demand.json"
    )
    ap.add_argument("--outdir", default="/home/student/cpuc_data/twitch")
    args = ap.parse_args()

    rows = load(args.csv)
    demand = load_demand(args.demand)
    runs = per_run_table(rows, demand)
    missing = [r["run_dir"] for r in runs if r["app_demand_mbps"] is None]
    if missing:
        # Loudly, not as a blank column: a demand figure that silently
        # vanished is exactly the failure this study keeps finding.
        print(f"!! {len(missing)} run(s) have no demand figure:", file=sys.stderr)
        for m in missing:
            print(f"   {m}", file=sys.stderr)
    table = aggregate(runs)
    finding = find_degradation(table, runs)

    os.makedirs(args.outdir, exist_ok=True)
    made = []

    runs_csv = os.path.join(args.outdir, OUT_PREFIX + "runs_table.csv")
    with open(runs_csv, "w", newline="") as fh:
        cols = list(dict.fromkeys(k for r in runs for k in r))
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(runs)
    made.append(runs_csv)

    tbl_csv = os.path.join(args.outdir, OUT_PREFIX + "iqb_vs_qoe_table.csv")
    with open(tbl_csv, "w", newline="") as fh:
        cols = list(dict.fromkeys(k for r in table for k in r))
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(table)
    made.append(tbl_csv)

    md = os.path.join(args.outdir, OUT_PREFIX + "iqb_vs_qoe_table.md")
    with open(md, "w") as fh:
        fh.write(markdown_table(table) + "\n")
    made.append(md)

    cap = latency_caption(table)
    made.append(
        plots.contrast(
            table,
            os.path.join(args.outdir, OUT_PREFIX + "contrast_iqb_vs_qoe.png"),
            DL_MIN,
            "rate_down_mbps",
            "iqb_verdict_download_only",
            f"IQB's verdict against measured Twitch QoE ({cap}, n=3)",
        )
    )
    made.append(
        plots.demand_vs_capacity(
            table,
            os.path.join(args.outdir, OUT_PREFIX + "demand_vs_capacity.png"),
            DL_MIN,
            "rate_down_mbps",
            "app_demand_mbps",
            "Twitch",
            "What Twitch actually used against what the link offered (mean of 3)",
            peak_key="app_peak_mbps",
        )
    )
    made.append(
        plots.source_vs_delivered(
            table,
            os.path.join(args.outdir, OUT_PREFIX + "source_vs_delivered.png"),
            DL_MIN,
            "rate_down_mbps",
            "What the Twitch ladder offered against what the player chose",
        )
    )
    made.append(
        rep_spread(
            runs,
            os.path.join(args.outdir, OUT_PREFIX + "rep_spread.png"),
            f"Twitch, every repetition shown ({cap}, 3 reps per capacity)",
        )
    )
    made = [m for m in made if m]

    with open(os.path.join(args.outdir, OUT_PREFIX + "finding.json"), "w") as fh:
        json.dump(finding, fh, indent=1)

    print(markdown_table(table))
    print()
    print("FINDING:", json.dumps(finding, indent=1))
    print()
    for m in made:
        print(f"wrote {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
