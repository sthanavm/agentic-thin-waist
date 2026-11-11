#!/usr/bin/env python3
"""Is the LINK what holds YouTube at 720p? Jaber's 300 Mb/s / 1 ms test.

THE QUESTION. Every YouTube row collected so far tops out at 720p, at every
capacity from 0.5 to 100 Mb/s and at a 1920x1080 player element
(`results_1080p/`). Two explanations have already been ruled out there: not
the viewport (the element was raised to 1920x1080 and the rendition did not
follow) and not the source (the video offers 2160p, recorded in every row).
The decode explanation was offered and then withdrawn, because one Vimeo run
on this host served 1920x1440 in AV1.

That leaves the link itself, which is the one thing never tested with real
headroom: the highest capacity ever run was 100 Mb/s at 20 ms. Jaber's test
removes the link from the argument -- 300 Mb/s in BOTH directions, 1 ms of
base latency, nothing else shaped. If 1080p does not appear there, no capacity
will produce it and the cause is on the client side.

WHY FOUR RUNS AND NOT ONE. "Does YouTube reach 1080p at 300 Mb/s" is one
question, but a single 720p answer cannot say WHICH of the three remaining
causes produced it, and that is what the report has to state. So the same link
is driven four ways, each one removing exactly one candidate:

  A  default viewport, ABR on     Jaber's spec, run verbatim. The element is
                                  ~1331x749 here, so 720p is the CORRECT
                                  adaptive choice and this run alone cannot
                                  rule anything in -- it is the control that
                                  shows the link was not the difference.
  B  fullscreen, ABR on           element 1920x1080, so the viewport is no
                                  longer the binding constraint. A 720p result
                                  here, on a 300 Mb/s link, is a player-side
                                  decision by elimination.
  C  fullscreen, rendition PINNED Asks the player for hd1080 directly. If
     to hd1080                    1080p arrives, then the stream exists, the
                                  host decodes it, and OUR COLLECTION CAN SEE
                                  IT -- so B's 720p was ABR choosing, not a
                                  ceiling. If it does NOT arrive, the ladder
                                  is not really available to this client.
  D  fullscreen, av01 hidden      The caveat `results_1080p/README.md` leaves
                                  open: with AV1 blocked the player must serve
                                  VP9 or H.264. If 1080p appears only here,
                                  the AV1 decode path is what bound it.

C and D are DIAGNOSTICS, not sweep rows: C turns ABR off and D changes the
codec, so neither is comparable with any collected row and `run_direct` slugs
them `_forcedq` / `_noav01` so they cannot be mixed in. They are reported as
what they are.

NOT A QoE LABEL. Labels are defined against a reference run at 100 Mb/s /
20 ms and this set runs at 300 Mb/s / 1 ms, so every row is `deferred` for
the same reason the 1080p-viewport set is: grading against a reference taken
on a different link would charge the link's effect to the player.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpuc import conditions as C  # noqa: E402
from cpuc import deps, drive_pramana, guard as guard_mod  # noqa: E402
from cpuc import netns, qoe_labels, runner  # noqa: E402
from cpuc.conditions import Condition  # noqa: E402
from cpuc.runner import tc_drop_stats, verify_shaper_from_record  # noqa: E402
from cpuc.supp1080 import geometry_row, read_samples  # noqa: E402
from cpuc.sweep import assert_source_high_enough, load_renditions  # noqa: E402

# Jaber's condition, verbatim: 300 Mb/s both directions, 1 ms base latency,
# no loss, no AQM beyond the reference pfifo. "Nothing else shaped."
JABER = Condition(
    rate_down_mbps=300.0,
    rate_up_mbps=300.0,
    rtt_ms=1.0,
    loss_pct=0.0,
    loss_mode="none",
    qdisc="pfifo",
    label="jaber_1080p_link",
)

# The element box B, C and D must reach. A run that does not reach it has
# measured the viewport again, so it is marked invalid rather than quietly
# recorded as another 720p result -- same rule as supp1080.py.
WANT_ELEM_W, WANT_ELEM_H = 1920, 1080

# (name, viewport, force_quality, block_codecs, what it removes)
VARIANTS: tuple[tuple[str, str | None, str | None, list[str] | None, str], ...] = (
    ("A_default_viewport", None, None, None, "nothing: Jaber's spec verbatim"),
    ("B_fullscreen", "fullscreen", None, None, "the viewport"),
    ("C_fullscreen_forced_1080", "fullscreen", "hd1080", None, "the player's ABR"),
    ("D_fullscreen_no_av01", "fullscreen", None, ["av01"], "the AV1 decode path"),
)

OUT_ROOT = os.environ.get("CPUC_YT1080_OUT", "/home/student/cpuc_data/yt1080_link")


def log(msg: str) -> None:
    print(f"[yt1080 {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def available_levels(samples: list[dict]) -> dict:
    """What the PLAYER said it could offer, from this run's own samples.

    `renditions.json` is a probe taken once for the whole study; this is the
    same question asked inside the run that produced the row, which is what
    makes "the source offered 1080p and the player served 720p" a measurement
    rather than two facts from different days.

    The collector writes it on the `meta` line of the samples file (not into
    `player_qoe`), and only when the run asked for a forced quality -- the
    level list comes back from the same JS that does the pinning. So this is
    populated for variant C and empty for the others, which is why C exists.
    """
    meta = next((s for s in samples if s.get("record") == "meta"), {})
    fq = meta.get("force_quality_result") or {}
    levels = fq.get("levels")
    return {
        "player_levels": ",".join(str(x) for x in levels) if levels else "",
        "player_quality_after": str(fq.get("quality_after") or ""),
        "player_force_applied": (
            bool(fq.get("range_applied") or fq.get("single_applied")) if fq else None
        ),
        "player_force_asked": meta.get("force_quality_level") or "",
    }


def run_one(name, viewport, force_quality, block_codecs, removes, csv_path) -> dict:
    app = "youtube"
    cond = JABER
    group = runner.group_id_for(cond, 1)
    run_id = f"yt1080_{name}_{os.urandom(3).hex()}"
    out_dir = os.path.join(OUT_ROOT, "runs", run_id)
    os.makedirs(out_dir, exist_ok=True)

    plan = C.RunPlan(app=app, condition=cond, rep=1, run_type="app")
    row = runner._base_row(
        plan, run_id, group, out_dir, "jaber_300mbps_1ms", time.gmtime()
    )
    row["variant"] = name
    row["removes"] = removes
    row["viewport_mode"] = viewport or "default"
    row["forced_quality"] = force_quality or ""
    row["blocked_codecs"] = ",".join(block_codecs or [])
    row["abr_on"] = not force_quality
    row.update(load_renditions())

    runner.apply_condition(cond)
    quiet, mbps = netns.link_idle_wait()
    row["link_idle_mbps_before"] = round(mbps, 3)
    row["link_idle_quiet"] = quiet
    row.update({f"tc_{k}": v for k, v in tc_drop_stats().items()})

    # Per-app viewport is a module-level dict on drive_pramana, as in
    # supp1080.py. Set per variant and restored after, so A really does run at
    # the default and is not contaminated by a previous fullscreen run.
    prev_window = drive_pramana.APP_WINDOW
    drive_pramana.APP_WINDOW = {app: viewport} if viewport else None
    try:
        res = drive_pramana.drive(
            app,
            cond,
            runner.APP_WINDOW_S,
            out_dir,
            force_quality=force_quality,
            block_codecs=block_codecs,
        )
    except Exception as exc:  # noqa: BLE001
        row["invalid_reason"] = f"run raised {type(exc).__name__}: {exc}"
        runner.write_row(csv_path, row)
        return row
    finally:
        drive_pramana.APP_WINDOW = prev_window

    if not res.ok:
        row["invalid_reason"] = f"run failed: {res.error}"
        runner.write_row(csv_path, row)
        return row

    record = res.attribution if isinstance(res.attribution, dict) else {}
    row.update(verify_shaper_from_record(record, cond.rate_down_mbps))
    row["run_dir"] = (
        os.path.dirname(((record.get("artifacts") or {}).get("pcap") or "").strip())
        or ""
    )
    row["pcap"] = ((record.get("artifacts") or {}).get("pcap") or "").strip()
    samples = read_samples(record, app)
    row.update(geometry_row(samples))
    row.update(available_levels(samples))

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
    row["qoe_reason"] = "no reference run at 300 Mb/s / 1 ms; see module docstring"

    # Validity means "this variant measured what it set out to measure".
    # A is valid whatever box it got -- the box IS its result. B/C/D are only
    # valid at a 1920x1080 element, or the viewport bound the ladder again.
    if viewport == "fullscreen":
        if not row.get("elem_h"):
            row["valid"] = False
            row["invalid_reason"] = "no element box in any sample"
        elif row["elem_w"] < WANT_ELEM_W or row["elem_h"] < WANT_ELEM_H:
            row["valid"] = False
            row["invalid_reason"] = (
                f"viewport not applied: element {row['elem_box']}, "
                f"want >={WANT_ELEM_W}x{WANT_ELEM_H}"
            )
        else:
            row["valid"] = True
    else:
        row["valid"] = bool(row.get("n_samples"))
        if not row["valid"]:
            row["invalid_reason"] = "no samples"

    runner.write_row(csv_path, row)
    return row


def main() -> int:
    global OUT_ROOT
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=OUT_ROOT)
    ap.add_argument(
        "--variants",
        default="",
        help="comma-separated variant prefixes to run (A,B,C,D); default all",
    )
    args = ap.parse_args()

    OUT_ROOT = args.out
    os.makedirs(os.path.join(OUT_ROOT, "runs"), exist_ok=True)
    csv_path = os.path.join(OUT_ROOT, "yt1080_link_runs.csv")

    deps.install()
    drive_pramana.assert_collector_has_probe()
    netns.start_iperf3_server()
    rend = load_renditions()
    assert_source_high_enough(rend)
    log(f"source OK: max {rend['source_max_rendition_p']}p -- {rend['source_url']}")
    log(f"condition: {JABER.slug()} (Jaber: 300 Mb/s both ways, 1 ms, nothing else)")

    want = [v.strip().upper() for v in args.variants.split(",") if v.strip()]
    variants = [v for v in VARIANTS if not want or v[0][0] in want]

    results = []
    for i, (name, viewport, fq, bc, removes) in enumerate(variants, 1):
        log(f"[{i}/{len(variants)}] {name}  (removes: {removes})")
        row = run_one(name, viewport, fq, bc, removes, csv_path)
        results.append(row)
        log(
            f"  valid={row.get('valid')} elem={row.get('elem_box') or '-'} "
            f"delivered={row.get('resolution_delivered') or '-'} "
            f"sfn={row.get('sfn_resolution_last') or '-'} "
            f"codec={row.get('video_codec') or '-'} "
            f"drop={row.get('dropped_pct')}% "
            f"{row.get('invalid_reason') or ''}"
        )

    with open(os.path.join(OUT_ROOT, "COMPLETE"), "w") as fh:
        json.dump(
            {
                "condition": JABER.slug(),
                "variants": len(results),
                "valid": sum(1 for r in results if r.get("valid")),
                "delivered": {
                    r.get("variant"): r.get("resolution_delivered") for r in results
                },
                "sfn": {
                    r.get("variant"): r.get("sfn_resolution_last") for r in results
                },
                "codec": {r.get("variant"): r.get("video_codec") for r in results},
                "pcap": {r.get("variant"): r.get("pcap") for r in results},
            },
            fh,
            indent=1,
        )
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
