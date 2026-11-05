#!/usr/bin/env python3
"""The bandwidth-minimum sweep: is IQB's 25 Mb/s streaming minimum too high?

Seven bandwidth points at a fixed 20 ms RTT and no induced loss, plus the
reference run. EVERY point gets both an app run and an NDT run, because the
argument is the CONTRAST between them: a link a speed test calls failing, on
which the video is still good.

Differences from the Stage 1 runner, all deliberate:

  * NO iperf3 gate. Jaber's directive -- it was the bottleneck. The shaper is
    verified AFTER the run from the capture instead, using the achieved-
    throughput figure run_direct already computes into
    record["network_stats"], plus `tc -s qdisc` drop counters. That moves
    verification from a 40-90 s pre-flight to a free post-hoc read.
  * The guard still runs and is still recorded, but a failure no longer
    discards the run: retry once, then keep the row FLAGGED AS SUSPECT.
    Flagged data beats no data tonight -- but it is flagged, so nothing
    downstream can mistake it for clean.
  * The reference run goes FIRST, so labels resolve immediately instead of
    deferring.

Everything else is kept as proven by the pilot: the :latest image with the
collector source mounted, host-side loss, the browser-suppression assertion,
the row format.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpuc import conditions as C  # noqa: E402
from cpuc import (
    drive_pramana,
    guard as guard_mod,
    netns,
    qoe_labels,
    runner,
)  # noqa: E402
from cpuc import ndt as ndt_mod  # noqa: E402
from cpuc.runner import (  # noqa: E402
    run_ndt_point,
    tc_drop_stats,
    verify_shaper_from_record,
)

RENDITIONS_SIDECAR = os.environ.get(
    "CPUC_RENDITIONS", "/home/student/cpuc_data/sweep/renditions.json"
)
MIN_SOURCE_HEIGHT = int(os.environ.get("CPUC_MIN_SOURCE_P", "1080"))


class SourceTooLowRes(RuntimeError):
    """The source video cannot carry the resolutions this study needs."""


def load_renditions(path: str = RENDITIONS_SIDECAR) -> dict:
    """What the source OFFERS, probed once and recorded in every row.

    The ladder is a property of the video, not of the bandwidth point, so one
    probe answers it for the whole sweep. Recording it is the point: "the
    source offered 2160p, the link was 25 Mb/s, and YouTube chose 720p" is a
    much stronger statement about IQB's threshold than a resolution on its own,
    because it rules out the source as the reason.
    """
    try:
        with open(path) as fh:
            d = json.load(fh)
    except Exception:
        return {}
    return {
        "source_url": d.get("url"),
        "source_renditions": ",".join(
            str(h) for h in (d.get("rendition_heights") or [])
        ),
        "source_max_rendition_p": d.get("max_rendition_p"),
        "source_has_1080p_plus": d.get("has_1080p_plus"),
    }


def assert_source_high_enough(info: dict) -> None:
    """Jaber's requirement: the source must carry 1080p or better."""
    mx = info.get("source_max_rendition_p")
    if not mx:
        raise SourceTooLowRes(
            f"no rendition ladder recorded at {RENDITIONS_SIDECAR}; run the "
            f"rendition probe before collecting, or the rows cannot say what "
            f"the source offered"
        )
    if int(mx) < MIN_SOURCE_HEIGHT:
        raise SourceTooLowRes(
            f"source tops out at {mx}p, below the required {MIN_SOURCE_HEIGHT}p; "
            f"a resolution result would be bounded by the source, not the link"
        )


MIN_FREE_BYTES = 2 * 1024**3
GATE_MAX_LOAD1 = 4.0


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run_app_point(plan, adapter, out_root, csv_path, reference, attempt):
    cond = plan.condition
    group = runner.group_id_for(cond, plan.rep)
    run_id = f"app_{plan.app}_{group}_a{attempt}_{os.urandom(3).hex()}"
    out_dir = os.path.join(out_root, run_id)
    row = runner._base_row(plan, run_id, group, out_dir, "sweep_20ms", time.gmtime())
    row["suspect"] = False
    row["suspect_reason"] = ""
    row["attempt"] = attempt
    row.update(load_renditions())

    runner.apply_condition(cond)
    quiet, mbps = netns.link_idle_wait()
    row["link_idle_mbps_before"] = round(mbps, 3)
    row.update({f"tc_{k}": v for k, v in tc_drop_stats().items()})

    try:
        res = adapter.run_app(plan.app, cond, runner.APP_WINDOW_S, out_dir)
    except Exception as exc:  # noqa: BLE001
        row["invalid_reason"] = f"app run raised {type(exc).__name__}: {exc}"
        runner.write_row(csv_path, row)
        return row
    if not res.ok:
        row["invalid_reason"] = f"app run failed: {res.error}"
        runner.write_row(csv_path, row)
        return row

    record = res.attribution if isinstance(res.attribution, dict) else {}
    row.update(verify_shaper_from_record(record, cond.rate_down_mbps))

    # Suppression stays fatal: an unsuppressed browser contaminates the very
    # thing being measured, and it is cheap to get right.
    try:
        guard_mod.assert_suppression_flags(res.browser_cmdline)
        row["suppression_ok"] = True
    except guard_mod.SuppressionNotActive as exc:
        row["suppression_ok"] = False
        row["invalid_reason"] = f"SuppressionNotActive: {exc}"
        runner.write_row(csv_path, row)
        return row

    # The interference guard is recorded, and flags rather than discards.
    verdict = guard_mod.verdict_from_record(record)
    row.update(verdict.as_row())
    if not verdict.ok:
        row["suspect"] = True
        row["suspect_reason"] = (
            "background-traffic guard failed: "
            + ("; ".join(verdict.offenders) if verdict.offenders else verdict.note)
        )[:300]

    label, reason, metric_row = adapter.label(res, plan.app, reference)
    row["qoe_label"] = label
    row["qoe_reason"] = reason
    row.update(metric_row)
    row["valid"] = label != "invalid"
    if label == "deferred":
        row["valid"] = True
    if not row["valid"]:
        row["invalid_reason"] = f"qoe_label={label}: {reason}"
    runner.write_row(csv_path, row)
    return row


def reference_for(csv_path: str) -> dict:
    return runner_reference(csv_path)


def runner_reference(csv_path: str) -> dict:
    import csv as _csv

    out: dict = {}
    if not os.path.exists(csv_path):
        return out
    with open(csv_path, newline="") as fh:
        for r in _csv.DictReader(fh):
            if r.get("run_type") != "app":
                continue
            if r.get("is_reference") not in ("True", "true", "1"):
                continue
            res = r.get("qoe_resolution_p")
            if res and res not in ("", "None"):
                try:
                    out.setdefault(r["app"], {})["resolution_p"] = int(float(res))
                except ValueError:
                    pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--out-root", default="/home/student/cpuc_data/sweep/runs")
    ap.add_argument("--csv", default="/home/student/cpuc_data/sweep/cpuc_runs.csv")
    ap.add_argument("--marker", default="/home/student/cpuc_data/sweep/COMPLETE")
    args = ap.parse_args()

    os.makedirs(args.out_root, exist_ok=True)
    os.makedirs(os.path.dirname(args.csv), exist_ok=True)
    for stale in (args.marker, args.marker + ".FAILED", args.marker + ".APPS_DONE"):
        if os.path.exists(stale):
            os.remove(stale)

    netns.start_iperf3_server()
    rend = load_renditions()
    assert_source_high_enough(rend)
    log(
        f"source OK: max {rend['source_max_rendition_p']}p, offers "
        f"[{rend['source_renditions']}] -- {rend['source_url']}"
    )
    drive_pramana.assert_collector_has_probe()
    log(f"collector OK: {drive_pramana.REQUIRED_COLLECTOR_IMAGE}")

    plans = C.sweep_plan(reps=args.reps)
    adapter = runner.StreamingAdapter(drive=drive_pramana.drive)
    log(
        f"=== SWEEP: {len(plans)} runs ({sum(1 for p in plans if p.run_type=='app')} app "
        f"+ {sum(1 for p in plans if p.run_type=='ndt')} ndt) -> {args.csv}"
    )

    stats = {"total": len(plans), "valid": 0, "invalid": 0, "suspect": 0, "flagged": []}
    apps_done = 0
    n_apps = sum(1 for p in plans if p.run_type == "app")
    started = time.time()
    try:
        for i, plan in enumerate(plans, 1):
            free = shutil.disk_usage(args.out_root).free
            if free < MIN_FREE_BYTES:
                raise RuntimeError(f"disk guard: {free/1024**3:.2f} GB free")
            label = (
                f"{plan.run_type} {plan.app or 'ndt'} "
                f"{plan.condition.rate_down_mbps:g}Mb/s rep{plan.rep}"
            )
            log(f"[{i}/{len(plans)}] {label}")
            row = {}
            for attempt in (1, 2):
                try:
                    if plan.run_type == "app":
                        row = run_app_point(
                            plan,
                            adapter,
                            args.out_root,
                            args.csv,
                            runner_reference(args.csv),
                            attempt,
                        )
                    else:
                        row = run_ndt_point(plan, args.out_root, args.csv, attempt)
                except Exception:
                    log(f"  attempt {attempt} raised:\n{traceback.format_exc()[-600:]}")
                    row = {
                        "valid": False,
                        "invalid_reason": f"attempt {attempt} raised",
                    }
                sus = " SUSPECT" if row.get("suspect") else ""
                log(
                    f"  attempt {attempt}: valid={row.get('valid')} "
                    f"label={row.get('qoe_label') or '-'}{sus} "
                    f"{str(row.get('invalid_reason') or '')[:80]}"
                )
                if row.get("valid"):
                    break
            stats["valid" if row.get("valid") else "invalid"] += 1
            if row.get("suspect"):
                stats["suspect"] += 1
            if not row.get("valid"):
                stats["flagged"].append(
                    f"{label}: {str(row.get('invalid_reason'))[:110]}"
                )
            if plan.run_type == "app":
                apps_done += 1
                if apps_done == n_apps:
                    with open(args.marker + ".APPS_DONE", "w") as fh:
                        json.dump(
                            {
                                "apps_done": apps_done,
                                "elapsed_s": round(time.time() - started, 1),
                            },
                            fh,
                        )
                    log(f"=== ALL {n_apps} APP RUNS DONE ===")
        payload = {
            "mode": "SWEEP",
            "elapsed_s": round(time.time() - started, 1),
            **stats,
        }
        with open(args.marker, "w") as fh:
            json.dump(payload, fh, indent=1)
        log(f"=== SWEEP DONE: {json.dumps(payload)}")
        return 0
    except Exception:
        with open(args.marker + ".FAILED", "w") as fh:
            fh.write(traceback.format_exc())
        log("=== SWEEP RAISED:\n" + traceback.format_exc()[-1200:])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
