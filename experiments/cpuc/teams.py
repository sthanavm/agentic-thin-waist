#!/usr/bin/env python3
"""Microsoft Teams Stage 1 + Stage 2: is IQB's conferencing minimum too high?

Same argument and the same harness as `meet.py`, ported to Teams and extended
to the plan doc's full conferencing design rather than the simplified single
sweep Meet used: Stage 1 runs IQB's own min/high corners (25/25 and 100/100
Mb/s) WITH loss, Stage 2 sweeps below the minimum (4, 6, 8, 25 Mb/s) with no
induced loss, at IQB's two conferencing latencies (50 ms min, 20 ms high) plus
100 ms. `conditions.teams_plan()` dedupes the two stages into 21 unique
conditions (not 24) and 1 rep, so this script runs 42 total runs: one Teams
call and one NDT run per condition, never at the same time.

THE BOT CLIP IS THE THING MOST LIKELY TO INVALIDATE THIS -- same caveat
meet.py documents. The bot publishes whatever Y4M it is given, and the default
in `make_test_clip.py` (640x480) would cap the received resolution at every
point for a reason that has nothing to do with the link. The clip is passed
explicitly here too (Big Buck Bunny, 720p, same asset meet.py uses), its
header is checked before the first run, and what the bot actually reports
PUBLISHING is pulled from its log and written into every row.
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
    deps,
    drive_pramana,
    guard as guard_mod,
    netns,
    qoe_labels,
    runner,
)  # noqa: E402
from cpuc.meet import (  # noqa: E402
    MeetingLikelyDropped,
    MeetingUnreachable,
    NoRemoteInboundMedia,
    assert_clip_tall_enough,
    assert_remote_inbound_media,
    assert_room_reachable,
    bot_published_size,
    clip_header,
    log,
    peer_is_publishing,
)
from cpuc.runner import AppRunResult  # noqa: E402
from cpuc.runner import run_ndt_point, verify_shaper_from_record  # noqa: E402

MIN_FREE_BYTES = 2 * 1024**3
# Same real 720p clip meet.py uses -- it encodes cheaply and is well above
# what Teams sends for one remote participant, so the bot is not the cap.
# See meet.py's module docstring for why a synthetic 1080p clip failed.
CLIP = os.environ.get(
    "CPUC_TEAMS_CLIP", "/home/student/pramana-assets/bbb_720p24_10s.y4m"
)
MIN_CLIP_HEIGHT = int(os.environ.get("CPUC_TEAMS_MIN_CLIP_H", "720"))
# A persistent (not one-off) meeting link, for the same reason meet.py insists
# on a Calendar event: an instant meeting ends when everyone leaves, and the
# bot leaves between runs.
ROOM = os.environ.get("CPUC_TEAMS_ROOM", "")
BOT_NAME = "pramana-bot"
JOIN_DEADLINE_S = int(os.environ.get("CPUC_TEAMS_JOIN_DEADLINE_S", "150"))

PEER_STATUS = os.environ.get(
    "CPUC_TEAMS_PEER_STATUS",
    "/home/student/pramana-assets/peer-teams/teams_status.json",
)
PEER_FRESH_S = 180.0

# Teams' getStats path emits the same inbound-rtp fields Meet's does (see PR14
# in agentic-thin-waist main: collect.py's WEBRTC_HOOK_JS is app-agnostic, it
# is driven off shared/apps.py's registry, not hardcoded to Meet). The remote-
# tile DOM gate does not apply here for the same reason it does not apply to
# Meet, so it is disabled the same way.
os.environ.setdefault("PRAMANA_REMOTE_TILE_MIN_AREA", "0")


def drive_teams(app: str, condition, duration_s: int, out_dir: str) -> AppRunResult:
    """One Teams run: bot publishes the clip, Chrome in ns1 receives it."""
    deps.install()
    os.environ.setdefault(
        "PRAMANA_COLLECTOR_IMAGE", drive_pramana.REQUIRED_COLLECTOR_IMAGE
    )
    os.environ.setdefault("PRAMANA_COLLECTOR_SRC", drive_pramana.COLLECTOR_SRC)
    import pramana_helpers as H  # noqa: PLC0415

    from cpuc import browser  # noqa: PLC0415

    cfg = H.ExperimentConfig(
        apps=[app],
        bandwidth_mbps=condition.rate_down_mbps,
        upload_mbps=condition.rate_up_mbps,
        latency_ms=condition.latency_for_platform(),
        loss_pct=condition.loss_pct,
        aqm=condition.qdisc,
        cca="cubic",
        duration_s=duration_s,
        trial=condition.trial_for(app),
        app_urls={app: ROOM},
        peer_wait_s=30,
        peers={
            app: {
                "join_url": ROOM,
                "y4m": CLIP,
                "name": BOT_NAME,
                "join_deadline_s": JOIN_DEADLINE_S,
            }
        },
    )
    restore = netns.install_shape_hook(H, condition.loss_pct, condition.rtt_ms)
    try:
        with browser.CmdlineWatcher(timeout_s=duration_s + 240) as watcher:
            record = H.run_direct(cfg)
    except Exception as exc:  # noqa: BLE001
        return AppRunResult(ok=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        restore()

    return AppRunResult(
        ok=True,
        player_qoe=record.get("player_qoe") or {},
        browser_cmdline=watcher.cmdline or "",
        attribution=record,
        raw_path=str(record.get("run_dir") or out_dir),
    )


class TeamsAdapter:
    name = "teams"
    category = "conferencing"

    def run_app(self, app, condition, duration_s, out_dir):
        return drive_teams(app, condition, duration_s, out_dir)

    def label(self, result, app, reference):
        pq = result.player_qoe.get(app, result.player_qoe)
        m = qoe_labels.extract_conferencing_metrics(pq or {})
        ref = (reference or {}).get(app, {})
        lab, reason = qoe_labels.label_conferencing(
            m, ref.get("resolution_p"), ref.get("fps")
        )
        return lab, reason, m.as_row()


def teams_reference(csv_path: str, rtt_ms: float | None = None) -> dict:
    """Reference resolution AND fps, read back from rows already written.

    Same logic as `meet.meet_reference`: filtered by configured RTT, because
    High is defined as a fraction of the reference measured at that same RTT.
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
                fps = float(r.get("qoe_fps") or 0) or None
            except ValueError:
                continue
            if res and fps:
                out[r["app"]] = {"resolution_p": res, "fps": fps}
    return out


def run_app_point(plan, adapter, out_root, csv_path, reference, attempt, clip_info):
    cond = plan.condition
    group = runner.group_id_for(cond, plan.rep)
    run_id = f"teams_{group}_a{attempt}_{os.urandom(3).hex()}"
    out_dir = os.path.join(out_root, run_id)
    row = runner._base_row(
        plan, run_id, group, out_dir, f"teams_{cond.rtt_ms:g}ms", time.gmtime()
    )
    row.update({"suspect": False, "suspect_reason": "", "attempt": attempt})
    row.update(clip_info)

    row.update(assert_room_reachable(ROOM))
    runner.apply_condition(cond)
    quiet, mbps = netns.link_idle_wait()
    row["link_idle_mbps_before"] = round(mbps, 3)

    try:
        res = adapter.run_app(plan.app, cond, runner.APP_WINDOW_S, out_dir)
    except Exception as exc:  # noqa: BLE001
        row["invalid_reason"] = f"teams run raised {type(exc).__name__}: {exc}"
        runner.write_row(csv_path, row)
        return row
    if not res.ok:
        row["invalid_reason"] = f"teams run failed: {res.error}"
        runner.write_row(csv_path, row)
        return row

    record = res.attribution if isinstance(res.attribution, dict) else {}
    row.update(verify_shaper_from_record(record, cond.rate_down_mbps))

    try:
        row.update(assert_remote_inbound_media(record, plan.app))
    except NoRemoteInboundMedia as exc:
        row["invalid_reason"] = f"NoRemoteInboundMedia: {exc}"
        runner.write_row(csv_path, row)
        return row
    published = bot_published_size(str(res.raw_path or out_dir))
    row["bot_publishing"] = published
    if published:
        try:
            ph = int(published.split("x")[1])
            row["bot_publishing_ok"] = ph >= MIN_CLIP_HEIGHT
        except (IndexError, ValueError):
            row["bot_publishing_ok"] = None

    try:
        guard_mod.assert_suppression_flags(res.browser_cmdline)
        row["suppression_ok"] = True
    except guard_mod.SuppressionNotActive as exc:
        row["suppression_ok"] = False
        row["invalid_reason"] = f"SuppressionNotActive: {exc}"
        runner.write_row(csv_path, row)
        return row

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
        help="comma-separated Stage 2 capacities instead of the default "
        "(25,8,6,4), e.g. '25,8,6,4,2' to extend the low end",
    )
    ap.add_argument(
        "--rtts",
        default="",
        help="comma-separated RTTs instead of the default (20,50,100)",
    )
    ap.add_argument(
        "--condition-range",
        default="",
        help="1-indexed inclusive range of unique conditions to run, e.g. "
        "'1-11', for splitting a batch across a time-limited meeting. "
        "Indexes follow teams_plan()'s own emission order (conditions.py), "
        "which puts all three per-RTT references (100/100 Mb/s, no loss) "
        "first -- so '1-11' always covers every reference and a later "
        "'12-21' can resolve its labels against rows already in the CSV "
        "without needing to re-run anything from the first half.",
    )
    ap.add_argument(
        "--skip-reference",
        action="store_true",
        help="do not re-run the 100/100 Mb/s no-loss corners; labels resolve "
        "against reference rows already in the CSV",
    )
    ap.add_argument(
        "--ndt-only",
        action="store_true",
        help="run only the NDT half of the matrix -- no meeting needed, so "
        "this can be run any time (e.g. after an M-Lab rate limit clears), "
        "decoupled from the Teams calls",
    )
    ap.add_argument(
        "--app-only",
        action="store_true",
        help="run only the Teams-call half of the matrix, skipping NDT -- for "
        "when NDT is blocked (M-Lab rate limiting) or just not needed right "
        "now; run --ndt-only separately later to fill in the speed tests",
    )
    ap.add_argument("--out-root", default="/home/student/cpuc_data/teams/runs")
    ap.add_argument("--csv", default="/home/student/cpuc_data/teams/cpuc_runs.csv")
    ap.add_argument("--marker", default="/home/student/cpuc_data/teams/COMPLETE")
    args = ap.parse_args()

    if not ROOM:
        ap.error(
            "CPUC_TEAMS_ROOM is not set -- point it at a persistent (Calendar, "
            "not instant) Teams meeting link before running"
        )

    os.makedirs(args.out_root, exist_ok=True)
    os.makedirs(os.path.dirname(args.csv), exist_ok=True)
    for stale in (
        args.marker,
        args.marker + ".FAILED",
        args.marker + ".APPS_DONE",
        args.marker + ".MEETING_DROPPED",
    ):
        if os.path.exists(stale):
            os.remove(stale)

    clip_info = assert_clip_tall_enough(CLIP)
    log(
        f"bot clip OK: {clip_info['clip_w']}x{clip_info['clip_h']} ({clip_info['clip']})"
    )
    drive_pramana.assert_collector_has_probe()
    log(f"collector OK: {drive_pramana.REQUIRED_COLLECTOR_IMAGE}")
    log(f"room: {ROOM}")

    rates = (
        tuple(float(x) for x in args.rates.split(",") if x.strip())
        if args.rates
        else C.TEAMS_STAGE2_RATES_MBPS
    )
    rtts = (
        tuple(float(x) for x in args.rtts.split(",") if x.strip())
        if args.rtts
        else C.TEAMS_RTTS_MS
    )
    plans = C.teams_plan(rates=rates, rtts=rtts)
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
    # All Teams runs first (highest rate first), then all NDT runs -- same
    # reasoning as meet.py: the meeting has a finite life, the NDT runs do not
    # need it, so they should not risk losing a bandwidth point to a meeting
    # that expires mid-batch.
    plans.sort(key=lambda p: (p.run_type != "app", -p.condition.rate_down_mbps))
    if args.limit:
        plans = plans[: args.limit]
    adapter = TeamsAdapter()
    n_apps = sum(1 for p in plans if p.run_type == "app")
    log(
        f"=== TEAMS SWEEP: {len(plans)} runs ({n_apps} teams + {len(plans)-n_apps} ndt)"
    )

    stats = {"total": len(plans), "valid": 0, "invalid": 0, "suspect": 0, "flagged": []}
    apps_done, started = 0, time.time()
    no_media_streak = 0
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
                            teams_reference(args.csv, rtt_ms=plan.condition.rtt_ms),
                            attempt,
                            clip_info,
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
                    f"publishing={row.get('bot_publishing') or '-'} "
                    f"{str(row.get('invalid_reason') or '')[:70]}"
                )
                if row.get("valid"):
                    break
            if plan.run_type == "app":
                reason = str(row.get("invalid_reason") or "")
                no_media = (not row.get("valid")) and (
                    "NoRemoteInboundMedia" in reason
                    or "MeetingUnreachable" in reason
                    or "advancing inbound video" in reason
                )
                no_media_streak = no_media_streak + 1 if no_media else 0
                if no_media_streak >= 2:
                    peer = peer_is_publishing(PEER_STATUS, PEER_FRESH_S)
                    if peer["publishing"]:
                        log(
                            f"  no-media streak at {plan.condition.rate_down_mbps:g} Mb/s, but "
                            f"the meeting is live ({peer['note']}) -- treating this "
                            "as the link being too slow to carry video, not a "
                            "dropped meeting, and continuing"
                        )
                        no_media_streak = 0
                    else:
                        raise MeetingLikelyDropped(
                            "two consecutive Teams runs received no remote media "
                            f"(last: {reason[:160]}) and the peer is not "
                            f"publishing ({peer['note']}). Stopping rather than "
                            "burning the remaining runs."
                        )

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
                    log(f"=== ALL {n_apps} TEAMS RUNS DONE ===")
        payload = {
            "mode": "TEAMS",
            "elapsed_s": round(time.time() - started, 1),
            **stats,
        }
        with open(args.marker, "w") as fh:
            json.dump(payload, fh, indent=1)
        log(f"=== TEAMS DONE: {json.dumps(payload)}")
        return 0
    except MeetingLikelyDropped as exc:
        with open(args.marker + ".MEETING_DROPPED", "w") as fh:
            json.dump({"error": str(exc), **stats}, fh, indent=1)
        log(f"=== MEETING DROPPED: {exc}")
        return 2
    except Exception:
        with open(args.marker + ".FAILED", "w") as fh:
            fh.write(traceback.format_exc())
        log("=== RAISED:\n" + traceback.format_exc()[-1200:])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
