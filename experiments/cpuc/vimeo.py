#!/usr/bin/env python3
"""Vimeo streaming sweep: is IQB's streaming minimum too high?

Same argument and harness shape as `teams.py`, on the streaming half of the
plan doc instead of conferencing: Stage 1 runs IQB's own min/high download
corners (25/10 and 100/10 Mb/s) WITH loss, and a below-minimum sweep covers
7 points under IQB's 25 Mb/s minimum with no induced loss, at the plan doc's
two streaming RTTs (100 ms IQB minimum, 20 ms plan-doc high -- see
`conditions.STREAMING_HIGH_RTT_MS_UNRESOLVED`). `conditions.vimeo_plan()`
dedupes the overlap into 20 unique conditions and 1 rep, so this script runs
40 total: one Vimeo play and one NDT run per condition, never at the same
time.

VIDEO: Vimeo's registry default (`pramana_helpers.APP_REGISTRY["vimeo"]`,
video 76979871) was checked live before this was written and turned out to be
Vimeo's own "The New Vimeo Player" staff demo clip, whose source master is
1280x720 -- there is no 1080p to select for that video, confirmed via its
player config endpoint, not assumed from the auto-picked rendition. Two other
candidates (911411289's actual Big Buck Bunny-labelled lookalikes aside) were
checked and rejected for being embed-restricted: HTTP 401 from
player.vimeo.com directly, meaning Vimeo's "where can this be embedded"
privacy setting does not include this collector regardless of resolution.

`VIMEO_URL` below is the SAME video `run_direct_youtube_vimeo_experiment.py`
used for the repo's existing youtube_vimeo_experiment (video 911411289,
"Invincible") -- confirmed live before this was written: player.vimeo.com
returns 200 (embeddable), and its page carries quality renditions up to
1440p (1080p, 720p, 540p, 360p, 240p also present), found in the embedded
player config JSON since the plain /config endpoint 403s for this video. No
`quality=` override is set, matching how the historical experiment ran it --
Vimeo's own ABR picks the rendition. The URL is passed explicitly so every
row records exactly what was played rather than relying on a registry
default a reader cannot see from the CSV.

WHY NO BOT/ROOM MACHINERY: unlike Meet/Teams, Vimeo is on-demand HTML5 video,
not a live call -- there is no peer to admit, no meeting to keep alive across
a batch, and no room that can go stale. `drive_pramana.drive()`, already built
for exactly this (the streaming half, see its own module docstring and
`runner.StreamingAdapter`), is used directly rather than through a
Teams/Meet-style custom driver.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpuc import conditions as C  # noqa: E402
from cpuc import (
    deps,
    drive_pramana,
    guard as guard_mod,
    netns,
    qoe_labels,
    runner,
)  # noqa: E402
from cpuc.runner import run_ndt_point, verify_shaper_from_record  # noqa: E402

MIN_FREE_BYTES = 2 * 1024**3
VIMEO_URL = os.environ.get(
    "CPUC_VIMEO_URL", "https://player.vimeo.com/video/911411289?autoplay=1&muted=1"
)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def assert_url_reachable(url: str) -> dict:
    """Cheap check the video URL answers from ns1 before spending a 60s run.

    Same reasoning as meet.py's assert_room_reachable: catches a dead link or
    broken egress in seconds rather than discovering it after a full run.
    """
    # shlex.quote on the URL: it carries `&`-joined query params, which a
    # bare interpolation into a shell=True string would split as job control.
    cmd = (
        f"docker exec {netns.WORKER_CONTAINER} ip netns exec {netns.CLIENT_NS} "
        f"timeout 20 curl -s -o /dev/null -w '%{{http_code}}' -L {shlex.quote(url)}"
    )
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    code = (out.stdout or "").strip()
    if not code.startswith("2") and not code.startswith("3"):
        raise RuntimeError(f"video URL {url} returned HTTP {code!r} from ns1")
    return {"room": url, "room_http": code}


class VimeoAdapter:
    name = "vimeo"
    category = "streaming"

    def run_app(self, app, condition, duration_s, out_dir):
        return drive_pramana.drive(
            app, condition, duration_s, out_dir, app_url=VIMEO_URL
        )

    def label(self, result, app, reference):
        pq = result.player_qoe.get(app, result.player_qoe)
        m = qoe_labels.extract_streaming_metrics(pq or {})
        ref = (reference or {}).get(app, {})
        lab, reason = qoe_labels.label_streaming(m, ref.get("resolution_p"))
        return lab, reason, m.as_row()


def vimeo_reference(csv_path: str, rtt_ms: float | None = None) -> dict:
    """Reference resolution, read back from rows already written.

    Streaming labels only need resolution (unlike conferencing's resolution
    + fps), per qoe_labels.label_streaming. Same filter-by-RTT reasoning as
    teams.teams_reference().
    """
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
            if rtt_ms is not None:
                try:
                    if abs(float(r.get("rtt") or -1) - rtt_ms) > 1e-6:
                        continue
                except ValueError:
                    continue
            try:
                res = int(float(r.get("qoe_resolution_p") or 0)) or None
            except ValueError:
                continue
            if res:
                out[r["app"]] = {"resolution_p": res}
    return out


def run_app_point(plan, adapter, out_root, csv_path, reference, attempt):
    cond = plan.condition
    group = runner.group_id_for(cond, plan.rep)
    run_id = f"vimeo_{group}_a{attempt}_{os.urandom(3).hex()}"
    out_dir = os.path.join(out_root, run_id)
    row = runner._base_row(
        plan, run_id, group, out_dir, f"vimeo_{cond.rtt_ms:g}ms", time.gmtime()
    )
    row.update({"suspect": False, "suspect_reason": "", "attempt": attempt})

    row.update(assert_url_reachable(VIMEO_URL))
    runner.apply_condition(cond)
    quiet, mbps = netns.link_idle_wait()
    row["link_idle_mbps_before"] = round(mbps, 3)

    try:
        res = adapter.run_app(plan.app, cond, runner.APP_WINDOW_S, out_dir)
    except Exception as exc:  # noqa: BLE001
        row["invalid_reason"] = f"vimeo run raised {type(exc).__name__}: {exc}"
        runner.write_row(csv_path, row)
        return row
    if not res.ok:
        row["invalid_reason"] = f"vimeo run failed: {res.error}"
        runner.write_row(csv_path, row)
        return row

    record = res.attribution if isinstance(res.attribution, dict) else {}
    row.update(verify_shaper_from_record(record, cond.rate_down_mbps))

    try:
        guard_mod.assert_suppression_flags(res.browser_cmdline)
        row["suppression_ok"] = True
    except guard_mod.SuppressionNotActive as exc:
        row["suppression_ok"] = False
        row["invalid_reason"] = f"SuppressionNotActive: {exc}"
        runner.write_row(csv_path, row)
        return row

    # Non-fatal: a failed verdict only sets suspect=True, same as meet.py and
    # (after rebasing onto main's #23) teams.py. link_interference landed
    # in-repo on main, so this is a real per-run judgement now, not the
    # universal non-signal it would have been before that -- the original
    # Vimeo batch (40/40 runs) predates this and was collected without it.
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
    row["valid"] = label != "invalid" or label == "deferred"
    if label in ("deferred",):
        row["valid"] = True
    if label == "invalid":
        row["valid"] = False
        row["invalid_reason"] = f"qoe_label=invalid: {reason}"
    runner.write_row(csv_path, row)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="stop after N plans (0 = all)")
    ap.add_argument(
        "--rates",
        default="",
        help="comma-separated below-minimum sweep capacities instead of the "
        "default (25,12.5,6.25,3,1.5,1,0.5)",
    )
    ap.add_argument(
        "--rtts",
        default="",
        help="comma-separated RTTs instead of the default (20,100)",
    )
    ap.add_argument(
        "--condition-range",
        default="",
        help="1-indexed inclusive range of unique conditions to run, e.g. "
        "'1-10', for splitting a batch across time. Indexes follow "
        "vimeo_plan()'s own emission order (conditions.py), highest rate "
        "first, which puts the reference (100/10 Mb/s, no loss) first.",
    )
    ap.add_argument(
        "--skip-reference",
        action="store_true",
        help="do not re-run the 100/10 Mb/s no-loss corner; labels resolve "
        "against the reference row already in the CSV",
    )
    ap.add_argument(
        "--ndt-only",
        action="store_true",
        help="run only the NDT half of the matrix, decoupled from the Vimeo "
        "plays -- for recovering speed tests after an M-Lab rate limit clears",
    )
    ap.add_argument(
        "--app-only",
        action="store_true",
        help="run only the Vimeo-play half, skipping NDT; run --ndt-only "
        "separately later to fill in the speed tests",
    )
    ap.add_argument("--out-root", default="/home/student/cpuc_data/vimeo/runs")
    ap.add_argument("--csv", default="/home/student/cpuc_data/vimeo/cpuc_runs.csv")
    ap.add_argument("--marker", default="/home/student/cpuc_data/vimeo/COMPLETE")
    args = ap.parse_args()

    os.makedirs(args.out_root, exist_ok=True)
    os.makedirs(os.path.dirname(args.csv), exist_ok=True)
    for stale in (args.marker, args.marker + ".FAILED", args.marker + ".APPS_DONE"):
        if os.path.exists(stale):
            os.remove(stale)

    deps.install()
    drive_pramana.assert_collector_has_probe()
    log(f"collector OK: {drive_pramana.REQUIRED_COLLECTOR_IMAGE}")
    log(f"video: {VIMEO_URL}")

    rates = (
        tuple(float(x) for x in args.rates.split(",") if x.strip())
        if args.rates
        else C.SWEEP_RATES_MBPS
    )
    rtts = (
        tuple(float(x) for x in args.rtts.split(",") if x.strip())
        if args.rtts
        else C.VIMEO_RTTS_MS
    )
    plans = C.vimeo_plan(rates=rates, rtts=rtts)
    if args.condition_range:
        lo_s, hi_s = args.condition_range.split("-", 1)
        lo, hi = int(lo_s), int(hi_s)
        seen: list[str] = []
        for p in plans:
            if p.run_type == "app" and p.condition.slug() not in seen:
                seen.append(p.condition.slug())
        if not (1 <= lo <= hi <= len(seen)):
            ap.error(
                f"--condition-range {args.condition_range} out of bounds for "
                f"{len(seen)} unique conditions"
            )
        wanted = set(seen[lo - 1 : hi])
        plans = [p for p in plans if p.condition.slug() in wanted]
        log(f"condition range {lo}-{hi}: {len(wanted)} of {len(seen)} conditions")
    if args.skip_reference:
        plans = [p for p in plans if not p.is_reference]
    if args.ndt_only and args.app_only:
        ap.error("--ndt-only and --app-only are mutually exclusive")
    if args.ndt_only:
        plans = [p for p in plans if p.run_type == "ndt"]
    if args.app_only:
        plans = [p for p in plans if p.run_type == "app"]
    plans.sort(key=lambda p: (p.run_type != "app", -p.condition.rate_down_mbps))
    if args.limit:
        plans = plans[: args.limit]
    adapter = VimeoAdapter()
    n_apps = sum(1 for p in plans if p.run_type == "app")
    log(
        f"=== VIMEO SWEEP: {len(plans)} runs ({n_apps} vimeo + {len(plans)-n_apps} ndt)"
    )

    stats = {"total": len(plans), "valid": 0, "invalid": 0, "suspect": 0, "flagged": []}
    apps_done, started = 0, time.time()
    try:
        for i, plan in enumerate(plans, 1):
            if shutil.disk_usage(args.out_root).free < MIN_FREE_BYTES:
                raise RuntimeError("disk guard")
            label = (
                f"{plan.run_type} {plan.app or 'ndt'} "
                f"{plan.condition.rate_down_mbps:g}/{plan.condition.rate_up_mbps:g} Mb/s "
                f"{plan.condition.rtt_ms:g}ms {plan.condition.loss_mode}"
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
                            vimeo_reference(args.csv, rtt_ms=plan.condition.rtt_ms),
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
                log(
                    f"  attempt {attempt}: valid={row.get('valid')} "
                    f"label={row.get('qoe_label') or '-'}"
                    f"{' SUSPECT' if row.get('suspect') else ''} "
                    f"{str(row.get('invalid_reason') or '')[:70]}"
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
                        json.dump({"apps_done": apps_done}, fh)
                    log(f"=== ALL {n_apps} VIMEO RUNS DONE ===")
        payload = {
            "mode": "VIMEO",
            "elapsed_s": round(time.time() - started, 1),
            **stats,
        }
        with open(args.marker, "w") as fh:
            json.dump(payload, fh, indent=1)
        log(f"=== VIMEO DONE: {json.dumps(payload)}")
        return 0
    except Exception:
        with open(args.marker + ".FAILED", "w") as fh:
            fh.write(traceback.format_exc())
        log("=== RAISED:\n" + traceback.format_exc()[-1200:])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
