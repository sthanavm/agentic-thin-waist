#!/usr/bin/env python3
"""Three-layer validation for the Zoom sweeps, plus the run-level invariants.

Layers, each independent:
  L1  record.json       - player_qoe and per_app_stats as the pipeline wrote them
  L2  qoe/*.jsonl       - the per-second samples the collector actually recorded
  L3  capture.pcap      - re-attributed from scratch, not trusting L1's numbers

Run-level invariants:
  * the shaped cap must hold on COMBINED traffic for a concurrent cell, summed
    on a 1s grid. Summing per-app peaks is wrong - they land at different
    instants - and that mistake once produced a false "cap exceeded" on three
    perfectly good cells.
  * the capture window must cover the whole call.
  * a conferencing cell must have measured the REMOTE peer's main tile, never
    the collector's own self-view. This is read from the record's stored
    provenance, not re-derived, so the committed artifact is what gets checked.

Classification:
  VERIFIED               every check passes
  REAL-CONDITION-FINDING checks pass, but a caveat materially limits what the
                         numbers mean (host CPU saturation, a player-side
                         ceiling) - the measurement is sound, the condition is
                         not purely the network
  PIPELINE-BUG           a check failed: the numbers describe the measurement
                         rather than the system under test
"""

import collections
import json
import os
import pathlib
import sys

H_ = pathlib.Path.home() / "sthanav-agentic-thin-waist"
sys.path.insert(0, str(H_ / "experiments/pramana"))
sys.path.insert(0, str(H_))
import pramana_helpers as H  # noqa: E402

BASE = H_ / "experiments/pramana/results/pramana_runs"
# Host CPU demand above this share of total capacity means the cell was
# competing for the host, so its player numbers are not purely network-driven.
VM_LIMIT_PCT = 85.0
# Share of samples on a local self-view above which a cell is not a measurement
# of the call. Below it the contamination is recorded as a caveat instead.
LOCAL_SHARE_FAIL = 0.20
_LOCAL_LABEL_HINTS = ("fake_device",)


def _label_is_local(label: str) -> bool:
    low = (label or "").lower()
    return any(h in low for h in _LOCAL_LABEL_HINTS)


CKPT = pathlib.Path(os.environ.get("CKPT", "/tmp/sweep_m1.json"))


def _checkpoint_dirs():
    """cell tag -> run_dir, as recorded when the cell actually ran.

    Globbing for the dir is not safe: an AV1-blocked concurrent slug and a solo
    slug both match earlier sweeps of the same regime, so a glob picked up runs
    from previous days and validated those instead - every cell then reported
    "collector predates element census", which was true of the old runs and
    false of the new ones. The checkpoint is the only record of which directory
    each cell produced.
    """
    try:
        d = json.loads(CKPT.read_text())
    except Exception:
        return {}
    out = {}
    for e in d.get("done", []):
        if e.get("run_dir"):
            out[e["cell"]] = pathlib.Path(e["run_dir"])
    return out


_CKPT_DIRS = _checkpoint_dirs()


def find_run(apps, bw, lat):
    tag = f"{'+'.join(apps)}_{bw}M_{lat}ms"
    d = _CKPT_DIRS.get(tag)
    if d is not None and (d / "record.json").exists():
        return d
    return None


def samples(d, app):
    p = d / "qoe" / f"{app}_stats.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip() or '"meta"' in line:
            continue
        try:
            st = (json.loads(line) or {}).get("stats") or {}
        except Exception:
            continue
        if st:
            out.append(st)
    return out


def cpu_digest(d):
    p = d / "cpu_per_process.jsonl"
    if not p.exists():
        return {}
    rows = []
    for line in p.read_text(errors="replace").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if "cpu_pct" in r:
            rows.append(r)
    if not rows:
        return {}
    tot = [r.get("cpu_pct_total_of_host", 0.0) for r in rows]
    peaks = {}
    for r in rows:
        for k, v in r["cpu_pct"].items():
            peaks[k] = max(peaks.get(k, 0.0), v)
    return {
        "host_peak_pct": round(max(tot), 1),
        "host_mean_pct": round(sum(tot) / len(tot), 1),
        "peer_chrome_peak": round(peaks.get("peer_chrome", 0.0), 1),
        "collector_unknown_peak": round(peaks.get("collector_chrome_unknown", 0.0), 1),
    }


def check(apps, bw, lat):
    d = find_run(apps, bw, lat)
    label = f"{'+'.join(apps)}_{bw}M_{lat}ms"
    if d is None:
        return {"cell": label, "status": "NOT RUN"}
    rec = json.loads((d / "record.json").read_text())
    fails, caveats = [], []

    if rec.get("analysis_deferred"):
        fails.append("analysis still deferred (run replot_run)")

    at = H.attribute_capture(d / "capture.pcap", apps, float(bw))

    # combined cap, from the summed 1s series
    grid = collections.defaultdict(float)
    for a in apps:
        t = at.traffic(a, "download")
        if t:
            for ts, mb in zip(t.times, t.mbps):
                grid[round(ts)] += mb
    cpeak = max(grid.values()) if grid else 0.0
    if cpeak > bw * 1.15:
        fails.append(f"combined peak {cpeak:.2f} > cap {bw}")

    if apps == ["zoom", "vimeo"]:
        blocked = (rec.get("app_blocked_codecs") or {}).get("vimeo")
        if blocked != ["av01"]:
            fails.append(f"vimeo AV1 block not recorded: {blocked}")

    per_app = {}
    for app in apps:
        q = (rec.get("player_qoe") or {}).get(app) or {}
        st = samples(d, app)

        # L3 vs L1
        dl = ((rec.get("per_app_stats") or {}).get(app) or {}).get("download") or {}
        t = at.traffic(app, "download")
        drift = None
        if t:
            drift = abs((t.total_mb or 0) - (dl.get("total_mb") or 0))
            if drift > 0.05:
                fails.append(f"{app}: pcap/record drift {drift:.3f} MB")

        # L2: the sample count must back the reported total
        if (q.get("total_samples") or 0) != len(st):
            fails.append(
                f"{app}: record says {q.get('total_samples')} samples, jsonl has {len(st)}"
            )

        # capture must cover the call
        sess = q.get("session_start_epoch")
        shift = (sess - at.t0_epoch) if sess and at.t0_epoch else None
        if (
            shift is None
            or shift < -1
            or (shift + (q.get("session_seconds") or 0) > at.duration_s + 2)
        ):
            fails.append(f"{app}: capture did not cover the call")

        if app == "zoom":
            if q.get("mean_bitrate_mbps") is not None:
                fails.append("zoom: bitrate invented (the web client exposes none)")
            me = q.get("measured_element") or {}
            if not me.get("available"):
                fails.append(f"zoom: no element provenance ({me.get('reason')})")
            else:
                # A few join-time samples land on the self-view before the
                # main view is laid out. That is a caveat on the cell, not a
                # broken measurement; what would invalidate it is the self-view
                # DOMINATING. Measured across 12 cells: 24 of 1638 samples
                # (1.5%), worst cell 8 of 127 (6.3%), and the dominant element
                # was the remote main tile in every cell.
                n_local = me.get("local_samples") or 0
                n_cens = me.get("samples_with_census") or 1
                share = n_local / n_cens
                dom_local = _label_is_local(str(me.get("dominant_label") or ""))
                if dom_local or share >= LOCAL_SHARE_FAIL:
                    fails.append(
                        f"zoom: the measured element was the LOCAL self-view in "
                        f"{n_local}/{n_cens} samples ({share:.0%})"
                    )
                elif n_local:
                    caveats.append(
                        f"zoom: {n_local}/{n_cens} samples ({share:.1%}) measured "
                        "the self-view at join, before the main view laid out"
                    )
                if (me.get("box_area_max") or 0) < int(1756 * 988 * 0.35):
                    fails.append(
                        f"zoom: tile too small to be the main view "
                        f"(max area {me.get('box_area_max')})"
                    )

        per_app[app] = {
            "res": q.get("video_resolution_p"),
            "fps": q.get("frame_rate_fps"),
            "dropped": q.get("dropped_frame_pct"),
            "reb": q.get("rebuffer_events"),
            "watched": q.get("watched_seconds"),
            "samples": len(st),
            "drift_mb": None if drift is None else round(drift, 3),
            "measured": (q.get("measured_element") or {}).get("dominant"),
        }
        # a player-side ceiling, not a network result
        if app == "vimeo":
            w, ss = q.get("watched_seconds"), q.get("session_seconds")
            if w and ss and w < 0.8 * ss:
                caveats.append(
                    f"vimeo advanced only {w:.0f}s of content in {ss:.0f}s "
                    "(decode ceiling, not the link)"
                )

    cpu = cpu_digest(d)
    if cpu and cpu.get("host_peak_pct", 0) >= VM_LIMIT_PCT:
        caveats.append(
            f"host CPU peaked at {cpu['host_peak_pct']}% of capacity (VM-limited)"
        )

    status = (
        "PIPELINE-BUG"
        if fails
        else ("REAL-CONDITION-FINDING" if caveats else "VERIFIED")
    )
    return {
        "cell": label,
        "dir": d.name,
        "status": status,
        "combined_peak": round(cpeak, 2),
        "cap": bw,
        "apps": per_app,
        "fails": fails,
        "caveats": caveats,
        "cpu": cpu,
        "charts": len(list(d.glob("*.png"))),
    }


SUITES = {
    "A (zoom+vimeo concurrent)": [["zoom", "vimeo"]],
    "B (zoom solo)": [["zoom"]],
}
ORDER = [(6, 150), (10, 50), (3, 150), (3, 50), (6, 50), (10, 150)]

if __name__ == "__main__":
    results = []
    for name, (apps,) in SUITES.items():
        print(f"\n{'=' * 70}\n{name}\n{'=' * 70}")
        for bw, lat in ORDER:
            r = check(apps, bw, lat)
            results.append(r)
            if r["status"] == "NOT RUN":
                print(f"{r['cell']:<26} NOT RUN")
                continue
            print(f"\n{r['cell']:<26} {r['status']}   [{r['dir'][-10:]}]")
            print(
                f"  combined peak {r['combined_peak']:.2f} / cap {r['cap']}   "
                f"charts={r['charts']}   cpu_host_peak={r['cpu'].get('host_peak_pct')}%"
            )
            for a, v in r["apps"].items():
                print(
                    f"  {a:<6} res={v['res']:<5} fps={v['fps']:<6} "
                    f"drop={v['dropped']:<7} reb={v['reb']:<3} "
                    f"n={v['samples']:<4} drift={v['drift_mb']}"
                )
                if v["measured"]:
                    print(f"         measured element: {v['measured']}")
            for f in r["fails"]:
                print(f"  FAIL: {f}")
            for c in r["caveats"]:
                print(f"  CAVEAT: {c}")
    print(f"\n{'=' * 70}\nSUMMARY")
    for r in results:
        print(f"  {r['cell']:<26} {r['status']}")
    json.dump(results, open("/tmp/validation_all.json", "w"), indent=2)
    print("\nwrote /tmp/validation_all.json")
