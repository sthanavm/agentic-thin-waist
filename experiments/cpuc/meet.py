#!/usr/bin/env python3
"""Google Meet bandwidth sweep: is IQB's conferencing minimum too high?

Four symmetric bandwidth points plus the reference, each with a Meet run AND an
NDT run, because the argument is the contrast: a link a speed test calls
failing, on which the call is still fine.

THE BOT CLIP IS THE THING MOST LIKELY TO INVALIDATE THIS. The bot publishes
whatever the Y4M it is given contains, and `make_test_clip.py` defaults to
640x480 -- which would cap the received resolution at every bandwidth point for
a reason that has nothing to do with the link, and make the whole curve
meaningless. So the clip is passed explicitly, its header is checked before the
first run, and the resolution the bot reports PUBLISHING is pulled out of the
bot log and written into every row. If the bot is not publishing what we think,
the data says so.

Note on env vars: bot_peer.py has no BOT_CLIP_WIDTH/BOT_CLIP_HEIGHT. It reads
BOT_CLIP, a path, and the resolution comes from that file's Y4M header. The
supported route is cfg.peers[app]["y4m"], which is what this uses.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
from cpuc import ndt as ndt_mod  # noqa: E402
from cpuc.runner import AppRunResult  # noqa: E402
from cpuc.runner import (  # noqa: E402
    run_ndt_point,
    verify_shaper_from_record,
)

MIN_FREE_BYTES = 2 * 1024**3
# The clip the bot publishes. REAL content at 720p, not synthetic 1080p.
#
# 1080p was tried first and failed measurably: a 1920x1080 synthetic texture
# clip has almost no spatial or temporal redundancy, so encoding it in software
# inside the bot container runs far below real time. Run 1 on that clip
# delivered 6 frames / 16,922 bytes in the whole window, Meet never promoted
# the peer to the main view, and the remote-tile gate failed with area 0 and
# no measurable element.
#
# bbb_720p24_10s.y4m is real video, which encodes cheaply, and it is the asset
# the existing archive was collected with. 720p is still well above what Meet
# sends for a single remote participant, so the bot is not the binding cap --
# which was the actual concern behind asking for 1080p. The clip's real
# dimensions are asserted below and written into every row, so if it ever IS
# the cap, the data says so rather than hiding it.
CLIP = os.environ.get(
    "CPUC_MEET_CLIP", "/home/student/pramana-assets/bbb_720p24_10s.y4m"
)
# Floor for "the bot is not the binding cap". Meet tops out well below this for
# one remote participant.
MIN_CLIP_HEIGHT = int(os.environ.get("CPUC_MEET_MIN_CLIP_H", "720"))
ROOM = os.environ.get("CPUC_MEET_ROOM", "https://meet.google.com/sgg-hmgg-iix")
BOT_NAME = "pramana-bot"
JOIN_DEADLINE_S = int(os.environ.get("CPUC_MEET_JOIN_DEADLINE_S", "150"))


class NoRemoteInboundMedia(RuntimeError):
    """The run produced no remote inbound video, so it measured nothing."""


# The collector's remote-tile gate is disabled for Meet, and replaced by
# `assert_remote_inbound_media` below. This is NOT a convenience bypass.
#
# That gate reads three DOM fields -- measured_box_area, measured_is_local and
# video_remote_decoded_count -- to prove the thing being measured is the remote
# peer's tile and not the local self-view thumbnail. Measured on this run,
# Meet's stats path emits NONE of those three fields (n=0 of 21 samples), so
# the gate cannot pass for Meet regardless of how the call goes: run 1 failed
# with "area 0, None@None, decoded=None" while the call was healthy and
# receiving 3.7 MB of the bot's video.
#
# The reason it cannot apply is that Meet's metrics come from inbound-rtp
# getStats, which is REMOTE media by construction -- local video appears in
# outbound-rtp. The self-view confusion the gate exists to prevent is not
# reachable on this path. So the gate is replaced by the check that IS
# meaningful here: the run must show remote inbound video actually arriving.
os.environ.setdefault("PRAMANA_REMOTE_TILE_MIN_AREA", "0")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def clip_header(path: str) -> dict:
    """Read the Y4M header so the published resolution is known, not assumed."""
    if not os.path.isfile(path):
        raise RuntimeError(f"bot clip missing: {path}")
    with open(path, "rb") as fh:
        head = fh.read(120).decode("latin-1", "replace")
    m = re.search(r"\bW(\d+)\s+H(\d+)", head)
    if not m:
        raise RuntimeError(
            f"bot clip {path} has no parseable Y4M header: {head[:60]!r}"
        )
    w, h = int(m.group(1)), int(m.group(2))
    return {"clip": path, "clip_w": w, "clip_h": h, "header": head.split("\n")[0]}


def assert_clip_tall_enough(path: str = CLIP) -> dict:
    """Fail before run 1 if the clip could cap the measurement.

    The failure this prevents is the quiet one: a 640x480 clip (make_test_clip's
    default) would hold every bandwidth point at 480p for a reason that has
    nothing to do with the link, and the curve would look like a result.
    """
    info = clip_header(path)
    if info["clip_h"] < MIN_CLIP_HEIGHT:
        raise RuntimeError(
            f"bot clip is {info['clip_w']}x{info['clip_h']}, below the "
            f"{MIN_CLIP_HEIGHT}p floor, so it could cap the received resolution "
            f"at every bandwidth point for a reason that is not the link."
        )
    return info


def assert_remote_inbound_media(record: dict, app: str) -> dict:
    """The Meet-appropriate replacement for the remote-tile gate.

    A run is only a measurement if remote video actually arrived. Checked on
    inbound-rtp evidence, which cannot be satisfied by the local self-view:
    a positive received-byte count, at least one inbound video stream, and a
    decoded frame rate.
    """
    pq = (record.get("player_qoe") or {}).get(app) or {}
    streams = pq.get("inbound_streams") or []
    bytes_rx = 0
    for s in streams if isinstance(streams, list) else []:
        if isinstance(s, dict):
            try:
                bytes_rx = max(bytes_rx, int(s.get("bytes_received") or 0))
            except (TypeError, ValueError):
                pass
    res = pq.get("video_resolution_p")
    fps = pq.get("frame_rate_fps")
    info = {
        "remote_bytes_received": bytes_rx,
        "remote_inbound_streams": len(streams) if isinstance(streams, list) else 0,
        "remote_media_basis": (
            "inbound-rtp getStats; remote by construction (local video is "
            "outbound-rtp), which is why the DOM tile gate does not apply"
        ),
    }
    if not bytes_rx or not res or not fps:
        raise NoRemoteInboundMedia(
            f"{app}: no remote inbound video to measure "
            f"(bytes_received={bytes_rx}, resolution={res}, fps={fps})"
        )
    return info


def bot_published_size(run_dir: str) -> str:
    """What the bot reported PUBLISHING, from its own log."""
    bots = os.path.join(run_dir, "bots")
    if not os.path.isdir(bots):
        return ""
    best = ""
    for name in os.listdir(bots):
        try:
            txt = open(os.path.join(bots, name), errors="replace").read()
        except OSError:
            continue
        for m in re.finditer(r"publishing=(\d+x\d+)", txt):
            best = m.group(1)
        for m in re.finditer(r"size=(\d+x\d+)", txt):
            best = best or m.group(1)
    return best


def drive_meet(app: str, condition, duration_s: int, out_dir: str) -> AppRunResult:
    """One Meet run: bot publishes the 1080p clip, Chrome in ns1 receives it."""
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


class MeetAdapter:
    name = "meet"
    category = "conferencing"

    def run_app(self, app, condition, duration_s, out_dir):
        return drive_meet(app, condition, duration_s, out_dir)

    def label(self, result, app, reference):
        pq = result.player_qoe.get(app, result.player_qoe)
        m = qoe_labels.extract_conferencing_metrics(pq or {})
        ref = (reference or {}).get(app, {})
        lab, reason = qoe_labels.label_conferencing(
            m, ref.get("resolution_p"), ref.get("fps")
        )
        return lab, reason, m.as_row()


def meet_reference(csv_path: str) -> dict:
    """Reference resolution AND fps, read back from the rows already written."""
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
    run_id = f"meet_{group}_a{attempt}_{os.urandom(3).hex()}"
    out_dir = os.path.join(out_root, run_id)
    row = runner._base_row(plan, run_id, group, out_dir, "meet_20ms", time.gmtime())
    row.update({"suspect": False, "suspect_reason": "", "attempt": attempt})
    row.update(clip_info)

    runner.apply_condition(cond)
    quiet, mbps = netns.link_idle_wait()
    row["link_idle_mbps_before"] = round(mbps, 3)

    try:
        res = adapter.run_app(plan.app, cond, runner.APP_WINDOW_S, out_dir)
    except Exception as exc:  # noqa: BLE001
        row["invalid_reason"] = f"meet run raised {type(exc).__name__}: {exc}"
        runner.write_row(csv_path, row)
        return row
    if not res.ok:
        row["invalid_reason"] = f"meet run failed: {res.error}"
        runner.write_row(csv_path, row)
        return row

    record = res.attribution if isinstance(res.attribution, dict) else {}
    row.update(verify_shaper_from_record(record, cond.rate_down_mbps))

    # The gate that replaces the collector's DOM tile check.
    try:
        row.update(assert_remote_inbound_media(record, plan.app))
    except NoRemoteInboundMedia as exc:
        row["invalid_reason"] = f"NoRemoteInboundMedia: {exc}"
        runner.write_row(csv_path, row)
        return row
    # What the bot actually published, not what we asked for.
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
    ap.add_argument("--extra", action="store_true", help="add 12.5 and 0.5 Mb/s")
    ap.add_argument("--out-root", default="/home/student/cpuc_data/meet/runs")
    ap.add_argument("--csv", default="/home/student/cpuc_data/meet/cpuc_runs.csv")
    ap.add_argument("--marker", default="/home/student/cpuc_data/meet/COMPLETE")
    args = ap.parse_args()

    os.makedirs(args.out_root, exist_ok=True)
    os.makedirs(os.path.dirname(args.csv), exist_ok=True)
    for stale in (args.marker, args.marker + ".FAILED", args.marker + ".APPS_DONE"):
        if os.path.exists(stale):
            os.remove(stale)

    clip_info = assert_clip_tall_enough()
    log(
        f"bot clip OK: {clip_info['clip_w']}x{clip_info['clip_h']} ({clip_info['clip']})"
    )
    drive_pramana.assert_collector_has_probe()
    log(f"collector OK: {drive_pramana.REQUIRED_COLLECTOR_IMAGE}")
    log(f"room: {ROOM}")

    rates = C.MEET_RATES_MBPS + (C.MEET_EXTRA_RATES_MBPS if args.extra else ())
    plans = C.meet_plan(rates=tuple(sorted(rates, reverse=True)))
    if args.limit:
        plans = plans[: args.limit]
    adapter = MeetAdapter()
    n_apps = sum(1 for p in plans if p.run_type == "app")
    log(f"=== MEET SWEEP: {len(plans)} runs ({n_apps} meet + {len(plans)-n_apps} ndt)")

    stats = {"total": len(plans), "valid": 0, "invalid": 0, "suspect": 0, "flagged": []}
    apps_done, started = 0, time.time()
    try:
        for i, plan in enumerate(plans, 1):
            if shutil.disk_usage(args.out_root).free < MIN_FREE_BYTES:
                raise RuntimeError("disk guard")
            label = (
                f"{plan.run_type} {plan.app or 'ndt'} "
                f"{plan.condition.rate_down_mbps:g}/{plan.condition.rate_up_mbps:g} Mb/s"
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
                            meet_reference(args.csv),
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
                    log(f"=== ALL {n_apps} MEET RUNS DONE ===")
        payload = {
            "mode": "MEET",
            "elapsed_s": round(time.time() - started, 1),
            **stats,
        }
        with open(args.marker, "w") as fh:
            json.dump(payload, fh, indent=1)
        log(f"=== MEET DONE: {json.dumps(payload)}")
        return 0
    except Exception:
        with open(args.marker + ".FAILED", "w") as fh:
            fh.write(traceback.format_exc())
        log("=== RAISED:\n" + traceback.format_exc()[-1200:])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
