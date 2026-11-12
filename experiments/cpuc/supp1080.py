"""Supplementary set: YouTube at a 1080p viewport, ABR left on.

Why this exists, and why it is NOT part of the main sweep.

The main sweep ran with the window at 1920x1080 on a 1920x1080 virtual
screen, which is the collector's default. That is not the box the player
draws into: YouTube's watch layout spends the window on a sidebar and
browser chrome and leaves the video element at 1331x749 (measured, 25 Mb/s
run of the main sweep: window_outer 1919x1079, window_inner 1911x988,
player_elem 1331x749). An adaptive player picks its rendition to fit the
element, so for a 1331x749 box 720p IS the correct choice. The main sweep's
flat 720p line is therefore a statement about that viewport, not only about
the link.

This set puts the player itself into fullscreen, so the element becomes the
whole screen -- exactly 1920x1080 -- and leaves ABR free. It answers one
question: at the same capacities, what does the same link deliver when the
viewport is not the binding constraint?

A different viewport is a different experiment, so these runs go to their
own directory and their own CSV and are never merged with the 720p sweep.
The collector also slugs a changed viewport (`_vcap...`), so the two cannot
mix even by accident.

No NDT here: the speed tests at these capacities are already in the main
sweep and the link is unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpuc import conditions as C  # noqa: E402
from cpuc import drive_pramana, guard as guard_mod  # noqa: E402
from cpuc import netns, qoe_labels, runner  # noqa: E402
from cpuc.conditions import Condition  # noqa: E402
from cpuc.runner import tc_drop_stats, verify_shaper_from_record  # noqa: E402
from cpuc.sweep import load_renditions  # noqa: E402

# The three points Jaber asked for. Upload and RTT match the main sweep so
# the only deliberate difference between the two sets is the viewport.
SUPP_RATES_MBPS = (25.0, 12.5, 6.25)
SUPP_UP_MBPS = 10.0
SUPP_RTT_MS = 20.0

# The element box this set exists to achieve. A run that does not reach it
# has measured the viewport again, not the link, so it fails loudly rather
# than quietly recording another 720p row.
WANT_ELEM_W, WANT_ELEM_H = 1920, 1080

# What the main sweep actually ran at, measured rather than assumed. Written
# into every row so the comparison carries its own baseline.
MAIN_SWEEP_ELEM = "1331x749"

OUT_ROOT = os.environ.get("CPUC_SUPP_OUT", "/home/student/cpuc_data/supp1080")


class ViewportNotApplied(RuntimeError):
    """The player never reached the 1080p element box this set requires."""


def log(msg: str) -> None:
    print(f"[supp1080 {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def supp_conditions() -> list[Condition]:
    return [
        Condition(
            rate_down_mbps=r,
            rate_up_mbps=SUPP_UP_MBPS,
            rtt_ms=SUPP_RTT_MS,
            loss_pct=0.0,
            qdisc="pfifo",
        )
        for r in SUPP_RATES_MBPS
    ]


def _run_dir_of(record: dict) -> str:
    """Where the collector actually wrote this run.

    `record["run_dir"]` is not populated, so the pcap's directory is the
    reliable handle on the run's own output.
    """
    pcap = ((record.get("artifacts") or {}).get("pcap") or "").strip()
    return os.path.dirname(pcap) if pcap else ""


def read_samples(record: dict, app: str) -> list[dict]:
    run_dir = _run_dir_of(record)
    if not run_dir:
        return []
    path = os.path.join(run_dir, "qoe", f"{app}_stats.jsonl")
    try:
        with open(path) as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except Exception:  # noqa: BLE001 - absent samples are reported, not raised
        return []


def geometry_row(samples: list[dict]) -> dict:
    """The viewport and the delivered rendition, straight from the samples.

    Measured during playback rather than taken from the run-level
    `window_geometry`, which is captured before navigation when no <video>
    element exists yet and whose player_w/player_h are therefore null. A
    check against that field would "verify" a null and pass.
    """
    row = {
        "elem_w": None,
        "elem_h": None,
        "elem_box": "",
        "window_outer": "",
        "window_inner": "",
        "resolution_delivered": "",
        "resolution_mode": "",
        "resolutions_seen": "",
        "decoding_w": None,
        "decoding_h": None,
        "n_samples": len(samples),
        "main_sweep_elem_box": MAIN_SWEEP_ELEM,
        "sfn_resolution_last": "",
        "player_serving": "",
        "player_optimal": "",
        "video_codec": "",
        "dropped_frames": None,
        "total_frames": None,
        "dropped_pct": None,
    }
    stats = [s.get("stats") or {} for s in samples]
    boxes = [
        (st.get("player_elem_w"), st.get("player_elem_h"))
        for st in stats
        if st.get("player_elem_w") and st.get("player_elem_h")
    ]
    if boxes:
        w, h = max(boxes, key=lambda b: b[0] * b[1])
        row.update(elem_w=w, elem_h=h, elem_box=f"{w}x{h}")
    for st in stats:
        if st.get("window_outer_w"):
            row["window_outer"] = f"{st['window_outer_w']}x{st['window_outer_h']}"
            row["window_inner"] = f"{st['window_inner_w']}x{st['window_inner_h']}"
            break

    res = [str(st.get("resolution")) for st in stats if st.get("resolution")]
    if res:
        counts = Counter(res)
        row["resolution_mode"] = counts.most_common(1)[0][0]
        row["resolutions_seen"] = ",".join(
            f"{r}:{n}" for r, n in sorted(counts.items(), key=lambda kv: -kv[1])
        )
        # The best rendition the player actually reached, by pixel count.
        best = max(res, key=lambda r: _px(r))
        row["resolution_delivered"] = best
    dec = [
        (st.get("decoding_video_width"), st.get("decoding_video_height"))
        for st in stats
        if st.get("decoding_video_height")
    ]
    if dec:
        w, h = max(dec, key=lambda d: d[0] * d[1])
        row.update(decoding_w=w, decoding_h=h)

    # YouTube's own "serving / optimal" pair, the codec it chose, and the
    # dropped-frame ratio. Together these separate the three reasons a player
    # can stay low: the link, the element it draws into, and the host's
    # ability to decode what it is offered. Without them a 720p row cannot
    # say which one bound it.
    sfn = [str(st.get("sfn_resolution")) for st in stats if st.get("sfn_resolution")]
    if sfn:
        row["sfn_resolution_last"] = sfn[-1]
        cur_opt = sfn[-1].split("/")
        row["player_serving"] = cur_opt[0].strip()
        row["player_optimal"] = cur_opt[1].strip() if len(cur_opt) > 1 else ""
    codecs = [str(st.get("sfn_codecs")) for st in stats if st.get("sfn_codecs")]
    row["video_codec"] = codecs[-1].split("/")[0].strip() if codecs else ""
    last = stats[-1] if stats else {}
    dropped = last.get("dropped_video_frames")
    total = last.get("total_video_frames")
    row["dropped_frames"] = dropped
    row["total_frames"] = total
    row["dropped_pct"] = (
        round(100.0 * dropped / total, 2) if (dropped is not None and total) else None
    )
    return row


def _px(res: str) -> int:
    try:
        w, h = res.lower().split("x")
        return int(w) * int(h)
    except Exception:  # noqa: BLE001
        return 0


def run_point(cond: Condition, adapter, csv_path: str, attempt: int = 1) -> dict:
    app = "youtube"
    group = runner.group_id_for(cond, 1)
    run_id = f"supp1080_{app}_{group}_a{attempt}_{os.urandom(3).hex()}"
    out_dir = os.path.join(OUT_ROOT, "runs", run_id)
    os.makedirs(out_dir, exist_ok=True)

    plan = C.RunPlan(app=app, condition=cond, rep=1, run_type="app")
    row = runner._base_row(
        plan, run_id, group, out_dir, "supp_1080p_viewport", time.gmtime()
    )
    row["viewport_mode"] = "fullscreen"
    row["viewport_target"] = f"{WANT_ELEM_W}x{WANT_ELEM_H}"
    row["attempt"] = attempt
    row.update(load_renditions())

    runner.apply_condition(cond)
    quiet, mbps = netns.link_idle_wait()
    row["link_idle_mbps_before"] = round(mbps, 3)
    row.update({f"tc_{k}": v for k, v in tc_drop_stats().items()})

    try:
        res = adapter.run_app(app, cond, runner.APP_WINDOW_S, out_dir)
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
    row["run_dir"] = _run_dir_of(record)

    samples = read_samples(record, app)
    row.update(geometry_row(samples))

    # Suppression stays fatal, exactly as in the main sweep.
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
    row["suspect"] = not verdict.ok
    row["suspect_reason"] = (
        ""
        if verdict.ok
        else ("background-traffic guard failed: " + "; ".join(verdict.offenders))[:300]
    )

    # Player metrics are recorded, but NOT labelled: a QoE label is defined
    # against a reference run, and the only reference that exists was taken
    # at the other viewport. Grading these against it would compare two
    # different experiments.
    per_app = (res.player_qoe or {}).get(app, res.player_qoe) or {}
    # Startup and rebuffering come from `qoe_labels.extract_streaming_metrics`,
    # which reads the `uniform` block (schema uniform_qoe/1) -- the SAME
    # definition every graded sweep row uses.
    #
    # This used to read `per_app["startup_delay_ms"]` directly. That key does
    # not exist at that level (it lives under `uniform`), so it returned None
    # for every run and the CSV's startup column came out EMPTY -- which is
    # what the three committed rows in `results_1080p/supp1080_runs.csv` show.
    # Another absent-reads-as-nothing bug: no error, no zero, just a blank
    # column nobody could tell from "startup was not measured".
    m = qoe_labels.extract_streaming_metrics(per_app)
    row["startup_s"] = m.startup_delay_s
    row["startup_basis"] = m.startup_basis
    row["rebuffer_events"] = m.rebuffer_events
    row["rebuffer_ms"] = m.rebuffer_duration_ms
    row["rebuffer_ratio"] = m.rebuffer_ratio
    row["qoe_label"] = "deferred"
    row["qoe_reason"] = "no same-viewport reference run in this set"

    # The viewport assertion. This is the whole point of the set: if the
    # element never reached 1920x1080 then the rendition was bounded by the
    # box again and the row must not read as a clean 1080p-viewport result.
    if not row["elem_h"]:
        row["valid"] = False
        row["invalid_reason"] = (
            "no element box in any sample, so the viewport could not be verified"
        )
    elif row["elem_w"] < WANT_ELEM_W or row["elem_h"] < WANT_ELEM_H:
        row["valid"] = False
        row["invalid_reason"] = (
            f"viewport not applied: element {row['elem_box']}, "
            f"want >={WANT_ELEM_W}x{WANT_ELEM_H}"
        )
    else:
        row["valid"] = True

    runner.write_row(csv_path, row)
    return row


def main() -> int:
    global OUT_ROOT
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=OUT_ROOT)
    ap.add_argument("--rates", default="")
    ap.add_argument(
        "--allow-capped",
        action="store_true",
        help="continue after a run whose element box stayed below 1920x1080 "
        "instead of stopping; the rows stay marked invalid either way",
    )
    args = ap.parse_args()

    OUT_ROOT = args.out
    os.makedirs(os.path.join(OUT_ROOT, "runs"), exist_ok=True)
    csv_path = os.path.join(OUT_ROOT, "supp1080_runs.csv")

    conds = supp_conditions()
    if args.rates:
        want = [float(x) for x in args.rates.split(",") if x.strip()]
        conds = [c for c in conds if c.rate_down_mbps in want]

    # Fullscreen player, ABR left on. force_max_quality stays False: pinning
    # the rendition would disable the adaptation this set exists to observe.
    drive_pramana.APP_WINDOW = {"youtube": "fullscreen"}
    assert not C.FORCE_MAX_QUALITY, "ABR must stay on for this set"

    adapter = runner.StreamingAdapter(drive_pramana.drive)
    log(f"{len(conds)} points, fullscreen viewport, no NDT -> {csv_path}")

    results = []
    for i, cond in enumerate(conds, 1):
        log(f"[{i}/{len(conds)}] {cond.slug()}")
        row = run_point(cond, adapter, csv_path)
        results.append(row)
        log(
            f"  valid={row.get('valid')} elem={row.get('elem_box') or '-'} "
            f"delivered={row.get('resolution_delivered') or '-'} "
            f"{row.get('invalid_reason') or ''}"
        )
        if not row.get("valid") and not args.allow_capped:
            log("STOPPING: the viewport was not applied, so the rest would")
            log("measure the same thing the main sweep already measured.")
            break

    with open(os.path.join(OUT_ROOT, "COMPLETE"), "w") as fh:
        json.dump(
            {
                "points": len(results),
                "valid": sum(1 for r in results if r.get("valid")),
                # The row key is "rate_down", set by runner._base_row.
                "delivered": {
                    f"{r['rate_down']:g}": r.get("resolution_delivered")
                    for r in results
                    if r.get("rate_down")
                },
            },
            fh,
            indent=1,
        )
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
