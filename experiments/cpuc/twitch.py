#!/usr/bin/env python3
"""Twitch capacity sweep: the same eight capacities as YouTube, three reps each.

WHY THREE REPS. Every streaming number in this dataset so far is n=1, which is
the weakest thing in it: a single run cannot separate a player's decision from
a single bad fetch, and the YouTube sweep already contains points where two
runs at the SAME capacity differed (12.5 Mb/s: 19.58 and 38.83 MB of demand).
So this sweep runs each capacity three times and publishes the spread, not a
point.

THE SAME EIGHT CAPACITIES, STRUCTURALLY. The matrix comes from
`conditions.sweep_plan(reps=3, rtt_ms=20, app="twitch")` -- the function the
YouTube sweep itself uses, with the app as a parameter -- so "the same eight
capacities" is enforced by sharing the definition rather than asserted by a
comment next to a copied tuple. That is 8 capacities (the 100 Mb/s reference
plus the seven below IQB's 25 Mb/s minimum) x 3 reps = 24 Twitch runs and 24
NDT runs.

THE SOURCE GATE IS A GATE. lofigirl is not a Twitch partner, so its
transcodes are allocated best-effort and can disappear. On a source-only
channel every capacity below the source bitrate would show buffering rather
than a step down the ladder, and the sweep would be measuring the wrong thing
while looking like it worked. `twitch_source.py` re-probes the live HLS master
playlist before the first run and this script ABORTS if the ladder is gone --
see `--require-transcodes`.

NDT SPACING. M-Lab rate-limits a client that asks for speed tests too
quickly, and a rate-limited NDT reports a throughput that is not the link's.
`--ndt-min-gap-s` enforces a wall-clock minimum between the START of
consecutive NDT runs; the app runs absorb most of it, so the cost is usually
zero waiting. This is separate from `runner.GAP_BEFORE_NDT_S`, which is the
30 s inside one point that keeps the app run and the speed test from
overlapping.

LIVE STREAM, SO NO DELIVERED FRACTION. Twitch is `live=True` in the registry:
there is no finite duration, so "fraction of the video watched" is undefined
and startup is time-to-first-frame only. `qoe_labels.label_streaming` already
grades on resolution, rebuffering and startup, which are all defined for a
live stream.

NOT COMPARABLE TO YOUTUBE'S BITRATE. lofigirl is a near-static animation: a
mostly-still 1080p frame costs a fraction of what Big Buck Bunny costs at the
same resolution. Compare the SHAPE of the degradation across apps -- which
rendition at which capacity, where rebuffering starts, how startup grows --
and compare demand only within an app. `conditions.STREAMING_APPS_PLAN`
carries the same warning for the same reason.
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
from cpuc import (  # noqa: E402
    deps,
    drive_pramana,
    guard as guard_mod,
    netns,
    qoe_labels,
    runner,
    twitch_source,
)
from cpuc.runner import run_ndt_point, verify_shaper_from_record  # noqa: E402
from cpuc.sweep import assert_source_high_enough  # noqa: E402

MIN_FREE_BYTES = 2 * 1024**3
TWITCH_APP = "twitch"
OUT_ROOT_DEFAULT = "/home/student/cpuc_data/twitch"


def log(msg: str) -> None:
    print(f"[twitch {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def twitch_url() -> str:
    """The pinned channel, read from the registry the COLLECTOR opens.

    `shared.apps.default_url` -> `shared.apps.url_for` -> the real-Chrome
    collector is the path a QoE run takes, so that is the registry this has to
    read. The other one (`pramana_helpers.APP_REGISTRY["twitch"]["url"]`)
    drives the NetGent workflow, and the two disagreeing about this exact
    channel is what made the first repin land in the wrong file -- see
    `shared/apps.py`'s module docstring. A drift test now pins them together;
    this reads the run path's own value anyway, and records it in every row.
    """
    deps.install()
    # `shared` lives at the REPO ROOT, two levels above this package, and
    # deps.install() only puts experiments/pramana on the path. Added here
    # rather than at import time so the module still imports in a bare
    # checkout where only the cpuc package is on sys.path.
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from shared import apps as shared_apps  # noqa: PLC0415

    return shared_apps.url_for(TWITCH_APP)


def assert_url_reachable(url: str) -> dict:
    """Cheap check the channel answers from ns1 before spending a 60 s run.

    Same reasoning as vimeo.py: catches dead egress or a 404'd channel in
    seconds rather than after a full run.
    """
    cmd = (
        f"docker exec {netns.WORKER_CONTAINER} ip netns exec {netns.CLIENT_NS} "
        f"timeout 20 curl -s -o /dev/null -w '%{{http_code}}' -L {shlex.quote(url)}"
    )
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    code = (out.stdout or "").strip()
    if not code.startswith("2") and not code.startswith("3"):
        raise RuntimeError(f"channel {url} returned HTTP {code!r} from ns1")
    return {"channel_url": url, "channel_http": code}


class TwitchAdapter:
    name = TWITCH_APP
    category = "streaming"

    def __init__(self, url: str):
        self.url = url

    def run_app(self, app, condition, duration_s, out_dir):
        return drive_pramana.drive(
            app, condition, duration_s, out_dir, app_url=self.url
        )

    def label(self, result, app, reference):
        pq = result.player_qoe.get(app, result.player_qoe)
        m = qoe_labels.extract_streaming_metrics(pq or {})
        ref = (reference or {}).get(app, {})
        lab, reason = qoe_labels.label_streaming(m, ref.get("resolution_p"))
        return lab, reason, m.as_row()


def twitch_reference(csv_path: str, rtt_ms: float | None = None) -> dict:
    """Reference resolution, read back from rows already written.

    Same filter-by-RTT reasoning as vimeo_reference: a label is relative to a
    reference taken at the SAME latency, or the latency's effect is charged to
    the capacity. The reference here is the 100 Mb/s row of rep 1.
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


def run_app_point(plan, adapter, out_root, csv_path, reference, attempt, source_row):
    cond = plan.condition
    group = runner.group_id_for(cond, plan.rep)
    run_id = f"twitch_{group}_a{attempt}_{os.urandom(3).hex()}"
    out_dir = os.path.join(out_root, run_id)
    row = runner._base_row(
        plan, run_id, group, out_dir, f"twitch_{cond.rtt_ms:g}ms", time.gmtime()
    )
    row.update({"suspect": False, "suspect_reason": "", "attempt": attempt})
    row.update(source_row)
    row.update(assert_url_reachable(adapter.url))

    runner.apply_condition(cond)
    quiet, mbps = netns.link_idle_wait()
    row["link_idle_mbps_before"] = round(mbps, 3)
    row["link_idle_quiet"] = quiet
    row.update({f"tc_{k}": v for k, v in runner.tc_drop_stats().items()})

    try:
        res = adapter.run_app(plan.app, cond, runner.APP_WINDOW_S, out_dir)
    except Exception as exc:  # noqa: BLE001
        row["invalid_reason"] = f"twitch run raised {type(exc).__name__}: {exc}"
        runner.write_row(csv_path, row)
        return row
    if not res.ok:
        row["invalid_reason"] = f"twitch run failed: {res.error}"
        runner.write_row(csv_path, row)
        return row

    record = res.attribution if isinstance(res.attribution, dict) else {}
    row.update(verify_shaper_from_record(record, cond.rate_down_mbps))
    row["run_dir"] = (
        os.path.dirname(((record.get("artifacts") or {}).get("pcap") or "").strip())
        or ""
    )

    # Suppression stays fatal: an unsuppressed browser contaminates the thing
    # being measured, and it is cheap to get right.
    try:
        guard_mod.assert_suppression_flags(res.browser_cmdline)
        row["suppression_ok"] = True
    except guard_mod.SuppressionNotActive as exc:
        row["suppression_ok"] = False
        row["invalid_reason"] = f"SuppressionNotActive: {exc}"
        runner.write_row(csv_path, row)
        return row

    # The interference guard flags rather than discards, as in every sweep
    # since #23. Flagged data is still data; unflagged bad data is not.
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
    if label == "invalid":
        row["invalid_reason"] = f"qoe_label=invalid: {reason}"
    runner.write_row(csv_path, row)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--rtt", type=float, default=C.SWEEP_RTT_MS)
    ap.add_argument("--out-root", default=os.path.join(OUT_ROOT_DEFAULT, "runs"))
    ap.add_argument("--csv", default=os.path.join(OUT_ROOT_DEFAULT, "twitch_runs.csv"))
    ap.add_argument("--marker", default=os.path.join(OUT_ROOT_DEFAULT, "COMPLETE"))
    ap.add_argument(
        "--source-sidecar",
        default=os.path.join(OUT_ROOT_DEFAULT, "twitch_renditions.json"),
    )
    ap.add_argument(
        "--require-transcodes",
        type=int,
        default=2,
        help="minimum transcodes besides the source; the sweep aborts below "
        "this because a source-only channel shows buffering, not adaptation",
    )
    ap.add_argument(
        "--ndt-min-gap-s",
        type=int,
        default=300,
        help="wall-clock minimum between the START of consecutive NDT runs, "
        "so M-Lab does not rate-limit us into reporting a throughput that is "
        "not the link's (default 300)",
    )
    ap.add_argument("--app-only", action="store_true")
    ap.add_argument("--ndt-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    os.makedirs(args.out_root, exist_ok=True)
    os.makedirs(os.path.dirname(args.csv), exist_ok=True)
    for stale in (args.marker, args.marker + ".FAILED", args.marker + ".APPS_DONE"):
        if os.path.exists(stale):
            os.remove(stale)

    deps.install()
    drive_pramana.assert_collector_has_probe()
    log(f"collector OK: {drive_pramana.REQUIRED_COLLECTOR_IMAGE}")
    netns.start_iperf3_server()

    # ── the source gate ──────────────────────────────────────────────────
    url = twitch_url()
    channel = url.rstrip("/").rsplit("/", 1)[-1]
    log(f"channel: {url} (pinned: {C.TWITCH_PINNED_CHANNEL})")
    if channel != C.TWITCH_PINNED_CHANNEL:
        raise SystemExit(
            f"the collector would open {channel!r} but conditions pins "
            f"{C.TWITCH_PINNED_CHANNEL!r}; refusing to sweep an unpinned channel"
        )
    info = twitch_source.probe(channel)
    with open(args.source_sidecar, "w") as fh:
        json.dump(info, fh, indent=1)
    log(
        f"ladder: {info['variant_count']} variants, {info['transcode_count']} "
        f"transcodes, max {info['max_rendition_p']}p -> {args.source_sidecar}"
    )
    for v in info["ladder"]:
        log(
            f"   {v['height']:>4}p  {(v['bandwidth_bps'] or 0)/1e6:6.2f} Mb/s  "
            f"group={v['group']}{'  (SOURCE)' if twitch_source.is_source(v) else ''}"
        )
    # Jaber's 1080p source requirement, through the SAME function the YouTube
    # sweep uses, then the transcode gate this channel specifically needs.
    assert_source_high_enough(
        {
            "source_max_rendition_p": info["max_rendition_p"],
            "source_renditions": ",".join(str(h) for h in info["rendition_heights"]),
            "source_url": info["url"],
        }
    )
    twitch_source.assert_transcodes_present(info, args.require_transcodes)
    log("source gate: OK (1080p+ present, ladder has transcodes to adapt along)")

    source_row = {
        "source_url": info["url"],
        "source_renditions": ",".join(str(h) for h in info["rendition_heights"]),
        "source_max_rendition_p": info["max_rendition_p"],
        "source_has_1080p_plus": info["has_1080p_plus"],
        "source_transcode_count": info["transcode_count"],
        "source_probed_utc": info["probed_utc"],
    }

    plans = C.sweep_plan(reps=args.reps, rtt_ms=args.rtt, app=TWITCH_APP)
    if args.ndt_only and args.app_only:
        ap.error("--ndt-only and --app-only are mutually exclusive")
    if args.ndt_only:
        plans = [p for p in plans if p.run_type == "ndt"]
    if args.app_only:
        plans = [p for p in plans if p.run_type == "app"]
    if args.limit:
        plans = plans[: args.limit]

    adapter = TwitchAdapter(url)
    n_apps = sum(1 for p in plans if p.run_type == "app")
    log(
        f"=== TWITCH SWEEP: {len(plans)} runs ({n_apps} twitch + "
        f"{len(plans)-n_apps} ndt), {args.reps} reps at {args.rtt:g} ms"
    )

    stats = {"total": len(plans), "valid": 0, "invalid": 0, "suspect": 0, "flagged": []}
    apps_done, started = 0, time.time()
    last_ndt_start = 0.0
    try:
        for i, plan in enumerate(plans, 1):
            if shutil.disk_usage(args.out_root).free < MIN_FREE_BYTES:
                raise RuntimeError("disk guard: under 2 GB free")
            label = (
                f"{plan.run_type} {plan.app or 'ndt'} "
                f"{plan.condition.rate_down_mbps:g} Mb/s "
                f"{plan.condition.rtt_ms:g}ms rep{plan.rep}"
            )
            log(f"[{i}/{len(plans)}] {label}")

            if plan.run_type == "ndt" and last_ndt_start:
                wait = args.ndt_min_gap_s - (time.time() - last_ndt_start)
                if wait > 0:
                    log(f"  NDT spacing: waiting {wait:.0f}s since the last one")
                    time.sleep(wait)

            row = {}
            for attempt in (1, 2):
                try:
                    if plan.run_type == "app":
                        row = run_app_point(
                            plan,
                            adapter,
                            args.out_root,
                            args.csv,
                            twitch_reference(args.csv, rtt_ms=plan.condition.rtt_ms),
                            attempt,
                            source_row,
                        )
                    else:
                        last_ndt_start = time.time()
                        row = run_ndt_point(plan, args.out_root, args.csv, attempt)
                except Exception:
                    log(f"  attempt {attempt} raised:\n{traceback.format_exc()[-600:]}")
                    row = {
                        "valid": False,
                        "invalid_reason": f"attempt {attempt} raised",
                    }
                log(
                    f"  attempt {attempt}: valid={row.get('valid')} "
                    f"label={row.get('qoe_label') or '-'} "
                    f"res={row.get('qoe_resolution_p') or '-'}"
                    f"{' SUSPECT' if row.get('suspect') else ''} "
                    f"{str(row.get('invalid_reason') or '')[:70]}"
                )
                if row.get("valid"):
                    break
                if plan.run_type == "ndt" and attempt == 1:
                    # A failed speed test is the signature of an M-Lab rate
                    # limit as much as of a bad link, so back off before the
                    # retry rather than asking again immediately.
                    log(f"  NDT retry backoff: {args.ndt_min_gap_s}s")
                    time.sleep(args.ndt_min_gap_s)
                    last_ndt_start = time.time()

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
                    log(f"=== ALL {n_apps} TWITCH RUNS DONE ===")
        payload = {
            "mode": "TWITCH",
            "reps": args.reps,
            "rtt_ms": args.rtt,
            "channel": channel,
            "source": source_row,
            "elapsed_s": round(time.time() - started, 1),
            **stats,
        }
        with open(args.marker, "w") as fh:
            json.dump(payload, fh, indent=1)
        log(f"=== TWITCH DONE: {json.dumps(payload)[:400]}")
        return 0
    except Exception:
        with open(args.marker + ".FAILED", "w") as fh:
            fh.write(traceback.format_exc())
        log("=== RAISED:\n" + traceback.format_exc()[-1200:])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
