#!/usr/bin/env python3
"""Read-only validator for the pramana run dataset.

Walks every run directory under ``results/pramana_runs/`` (or a root given on
the command line), reads each ``record.json``, and reports whether the run is
internally consistent — that what the record *claims* is supported by what it
*measured*.

Nothing is written, moved or regenerated: every rule here is a pure read. Runs
are evidence, and a validator that edits its evidence is worthless.

Rules are of two kinds:

  FAIL rules  — a contradiction inside the run. The record says something that
                its own numbers do not support.
  WARN rules  — measurement implausibility across runs. Not provably wrong,
                but the kind of thing that should be eyeballed before a run is
                used as a result.

Usage
-----
    python3 experiments/pramana/validate_runs.py [results_root] [--verbose]

Exit status is 1 if any run failed, else 0, so it can gate a pipeline.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

# ── tolerances ───────────────────────────────────────────────────────────────
SHAPING_TOLERANCE = 1.10  # peak may exceed the cap by 10% (binning overshoot)
PCAP_DIVERGENCE = 0.25  # record vs pcap bytes may differ by 25%
CLOCK_FLOOR_S = 0.0  # "advanced" means strictly greater than this

# Rule identifiers, kept short so the table stays readable.
R_PARSE = "RECORD_PARSE"
R_SHAPING = "SHAPING"
R_LABEL = "LABEL_VS_PLAYER"
R_FLAGS = "PLAYER_FLAG_MISMATCH"
R_WATCHED = "PLAYED_BUT_WATCHED_ZERO"
R_NOPLAY = "NO_PLAYBACK_LOGIC"
R_BITRATE = "BITRATE_SANITY"
R_ARTIFACTS = "ARTIFACTS"
R_DURATION = "DURATION"
W_QUALITY = "QUALITY_VS_BW"
W_PCAP = "TPUT_VS_PCAP"
W_DUP = "DUPLICATE_CONFIG"
W_PCAP_OMITTED = "PCAP_OMITTED"

# A run directory carrying this file is a reference copy distributed without
# its capture. The record still says pcap_saved=true, because it did save one
# on the VM — the marker explains the absence instead of falsifying the record.
PCAP_OMITTED_MARKER = "PCAP_OMITTED.md"


def _num(v: Any) -> Optional[float]:
    """Coerce to float, or None for anything that is not a real number."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # drop NaN


class RunReport:
    """Per-run verdict: the failures and warnings it accumulated."""

    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        self.record: dict[str, Any] = {}
        self.timestamp: Optional[float] = None
        self.failures: list[tuple[str, str]] = []  # (rule, detail)
        self.warnings: list[tuple[str, str]] = []

    def fail(self, rule: str, detail: str) -> None:
        self.failures.append((rule, detail))

    def warn(self, rule: str, detail: str) -> None:
        self.warnings.append((rule, detail))

    @property
    def status(self) -> str:
        if self.failures:
            return "FAIL:" + ",".join(sorted({r for r, _ in self.failures}))
        if self.warnings:
            return "WARN:" + ",".join(sorted({r for r, _ in self.warnings}))
        return "PASS"

    @property
    def passed(self) -> bool:
        return not self.failures


# ── player-clock evidence ────────────────────────────────────────────────────
def clock_advanced(run_dir: Path, app: str) -> Optional[bool]:
    """Did this app's media clock ever move past 0?

    Returns True/False from the raw per-second samples, or None when there are
    no samples to judge from (absence of evidence, not evidence of absence).
    """
    path = run_dir / "qoe" / f"{app}_stats.jsonl"
    if not path.exists():
        return None
    seen = False
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("record") == "meta":
            continue
        val = _num((row.get("stats") or {}).get("current_time_secs"))
        if val is None:
            continue
        seen = True
        if val > CLOCK_FLOOR_S:
            return True
    return False if seen else None


# ── per-run rules ────────────────────────────────────────────────────────────
def check_run(run_dir: Path) -> RunReport:
    rep = RunReport(run_dir)
    rec_path = run_dir / "record.json"

    # R0 — record.json parses.
    if not rec_path.exists():
        rep.fail(R_PARSE, "record.json is missing")
        return rep
    try:
        rec = json.loads(rec_path.read_text())
    except Exception as exc:
        rep.fail(R_PARSE, f"record.json does not parse: {type(exc).__name__}: {exc}")
        return rep
    rep.record = rec
    rep.timestamp = _num(rec.get("timestamp"))

    cfg = rec.get("config") or {}
    cap = _num(cfg.get("bandwidth_mbps")) or 0.0
    want_dur = _num(cfg.get("duration_s"))
    net = rec.get("network_stats") or {}
    per_app = rec.get("per_app_stats") or {}
    player = rec.get("player_qoe") or {}
    artifacts = rec.get("artifacts") or {}

    # R1 — shaping: nothing may exceed the configured cap (plus tolerance).
    if cap > 0:
        bound = cap * SHAPING_TOLERANCE
        peak = _num(net.get("peak_throughput_mbps"))
        if peak is not None and peak > bound:
            rep.fail(
                R_SHAPING,
                f"aggregate peak {peak:.3f} Mbps > cap {cap:g} x "
                f"{SHAPING_TOLERANCE} = {bound:.3f} Mbps",
            )
        for app, st in sorted(per_app.items()):
            dl = (st or {}).get("download") or {}
            apeak = _num(dl.get("peak_throughput_mbps"))
            if apeak is not None and apeak > bound:
                rep.fail(
                    R_SHAPING,
                    f"{app} download peak {apeak:.3f} Mbps > {bound:.3f} Mbps",
                )

    # R2 — a run without player data may not claim the app streamed.
    #
    # Truth comes from the player_qoe payload, not from the per_app_stats
    # mirror of the flag: the two can disagree inside one record, and the
    # payload is the side that actually carries measurements.
    for app, st in sorted(per_app.items()):
        st = st or {}
        pq = player.get(app) if isinstance(player.get(app), dict) else {}
        has_player = bool(pq.get("player_qoe_available"))
        cls = str(st.get("classification") or "")
        if not has_player and cls in ("served", "streamed"):
            rep.fail(
                R_LABEL,
                f"{app}: classification={cls!r} but no player data "
                f"(player_qoe_available=false, status="
                f"{pq.get('status') or st.get('player_qoe_status')!r}) "
                "— nothing shows it played",
            )

    # R2b — the record must not disagree with itself about whether the player
    # answered. A mirrored flag that contradicts the payload silently poisons
    # every downstream consumer that happens to read the other one.
    for app, st in sorted(per_app.items()):
        st = st or {}
        pq = player.get(app) if isinstance(player.get(app), dict) else None
        if pq is None:
            continue
        mirrored = st.get("player_qoe_available")
        actual = pq.get("player_qoe_available")
        if mirrored is not None and bool(mirrored) != bool(actual):
            rep.fail(
                R_FLAGS,
                f"{app}: per_app_stats.player_qoe_available={mirrored!r} but "
                f"player_qoe.player_qoe_available={actual!r} "
                f"(status={pq.get('status')!r}, "
                f"resolution={pq.get('video_resolution_p')!r}, "
                f"watched={pq.get('watched_seconds')!r})",
            )

    # R3 — played, yet watched nothing.
    for app, pq in sorted(player.items()):
        if not isinstance(pq, dict):
            continue
        res = pq.get("video_resolution_p")
        startup = _num(pq.get("video_startup_time_ms"))
        watched = _num(pq.get("watched_seconds"))
        played = (res is not None) or (startup is not None)
        if played and (watched is None or watched == 0):
            rep.fail(
                R_WATCHED,
                f"{app}: resolution={res} startup={startup} indicate playback "
                f"but watched_seconds={pq.get('watched_seconds')!r} "
                f"(expected > 0)",
            )

    # R4 — a clock that never moved must be reported as no_playback.
    for app in sorted(set(per_app) | set(player)):
        advanced = clock_advanced(run_dir, app)
        if advanced is not False:
            continue  # advanced, or nothing to judge from
        pq = player.get(app) if isinstance(player.get(app), dict) else {}
        st = per_app.get(app) or {}
        status = str(pq.get("status") or st.get("player_qoe_status") or "")
        reason = str(pq.get("reason") or "")
        cls = str(st.get("classification") or "")
        says_no_playback = status == "no_playback" or reason.startswith("no_playback")
        if not says_no_playback:
            rep.fail(
                R_NOPLAY,
                f"{app}: current_time_secs never advanced past 0, but "
                f"status={status or 'None'!r} reason={reason or 'None'!r} "
                f"classification={cls!r} (expected no_playback)",
            )
        elif cls in ("served", "streamed"):
            rep.fail(
                R_NOPLAY,
                f"{app}: clock never advanced and status is no_playback, "
                f"yet classification={cls!r}",
            )

    # R5 — a bitrate above the link cap is a mis-derived metric, not a result.
    if cap > 0:
        for app, pq in sorted(player.items()):
            if not isinstance(pq, dict):
                continue
            br = _num(pq.get("mean_bitrate_mbps"))
            if br is not None and br > cap:
                rep.fail(
                    R_BITRATE,
                    f"{app}: mean_bitrate_mbps={br:.3f} > cap {cap:g} Mbps "
                    "(likely a connection-speed estimate, not a bitrate)",
                )

    # R6 — the artifacts the record promises must be on disk.
    if artifacts.get("pcap_saved"):
        pcap = run_dir / "capture.pcap"
        if not pcap.exists():
            alt = sorted(list(run_dir.glob("*.pcap")) + list(run_dir.glob("*.pcapng")))
            pcap = alt[0] if alt else pcap
        if not pcap.exists() and (run_dir / PCAP_OMITTED_MARKER).exists():
            # A reference run shipped in git: captures are hundreds of MB and
            # cannot live in a repo, so the run travels without its pcap and
            # says so out loud. Reported, never hidden — and never by editing
            # the record to claim it saved no capture.
            rep.warn(
                W_PCAP_OMITTED,
                f"capture intentionally omitted for distribution (see "
                f"{PCAP_OMITTED_MARKER}); the pcap exists on the VM that "
                "produced this run — re-validate there for full evidence",
            )
        elif not pcap.exists():
            rep.fail(
                R_ARTIFACTS,
                "record says pcap_saved=true but no .pcap/.pcapng in the run dir",
            )
        elif pcap.stat().st_size == 0:
            rep.fail(R_ARTIFACTS, f"{pcap.name} is 0 bytes")
    for label, ppath in sorted((artifacts.get("plots") or {}).items()):
        if not (run_dir / Path(str(ppath)).name).exists():
            rep.fail(
                R_ARTIFACTS,
                f"plot {label!r} listed in record but missing: "
                f"{Path(str(ppath)).name}",
            )

    # R7 — a capture shorter than the configured run was cut off.
    got_dur = _num(net.get("duration_s"))
    if want_dur is not None and got_dur is not None and got_dur < want_dur:
        rep.fail(
            R_DURATION,
            f"capture duration {got_dur:.2f}s < configured {want_dur:g}s "
            f"(short by {want_dur - got_dur:.2f}s)",
        )

    # W9 — the record's byte count should resemble the capture on disk.
    total_mb = _num(net.get("total_mb"))
    pcaps = sorted(list(run_dir.glob("*.pcap")) + list(run_dir.glob("*.pcapng")))
    if total_mb and pcaps:
        disk_mb = pcaps[0].stat().st_size / 1e6
        if disk_mb > 0:
            rel = abs(disk_mb - total_mb) / max(disk_mb, total_mb)
            if rel > PCAP_DIVERGENCE:
                rep.warn(
                    W_PCAP,
                    f"record total_mb={total_mb:.2f} vs {pcaps[0].name} "
                    f"{disk_mb:.2f} MB on disk ({100 * rel:.0f}% divergence, "
                    f"threshold {100 * PCAP_DIVERGENCE:.0f}%)",
                )
    return rep


# ── cross-run rules ──────────────────────────────────────────────────────────
def _is_solo(rec: dict[str, Any]) -> bool:
    cfg = rec.get("config") or {}
    conc = cfg.get("concurrency")
    apps = cfg.get("apps") or []
    return (conc in (None, 1, "1", "solo")) and len(apps) == 1


def _resolution(rec: dict[str, Any], app: str) -> Optional[float]:
    pq = (rec.get("player_qoe") or {}).get(app)
    if not isinstance(pq, dict):
        return None
    return _num(pq.get("video_resolution_p"))


def check_quality_vs_bandwidth(reports: list[RunReport]) -> None:
    """Warn where a higher bandwidth tier produced a *lower* resolution.

    Grouped by (app, latency, loss, aqm, cca) so only comparable runs are put
    side by side. Ties are fine; only an inversion is reported.
    """
    groups: dict[tuple, list[tuple[float, float, RunReport]]] = defaultdict(list)
    for rep in reports:
        rec = rep.record
        if not rec or not _is_solo(rec):
            continue
        cfg = rec.get("config") or {}
        app = (cfg.get("apps") or [None])[0]
        bw = _num(cfg.get("bandwidth_mbps"))
        res = _resolution(rec, app) if app else None
        if app is None or bw is None or res is None:
            continue
        key = (
            app,
            cfg.get("latency_ms"),
            cfg.get("loss_pct"),
            cfg.get("aqm"),
            cfg.get("cca"),
        )
        groups[key].append((bw, res, rep))

    for key, rows in sorted(groups.items(), key=lambda kv: str(kv[0])):
        rows.sort(key=lambda r: r[0])
        best_bw, best_res = None, None
        for bw, res, rep in rows:
            if best_res is not None and res < best_res and bw > best_bw:
                app, lat = key[0], key[1]
                rep.warn(
                    W_QUALITY,
                    f"{app} @ {bw:g} Mbps/{lat}ms rendered {res:g}p, but "
                    f"{best_bw:g} Mbps rendered {best_res:g}p — quality fell "
                    "as bandwidth rose",
                )
            if best_res is None or res > best_res:
                best_bw, best_res = bw, res


def config_signature(rec: dict[str, Any]) -> tuple:
    cfg = rec.get("config") or {}
    return (
        tuple(cfg.get("apps") or []),
        cfg.get("bandwidth_mbps"),
        cfg.get("upload_mbps"),
        cfg.get("latency_ms"),
        cfg.get("loss_pct"),
        cfg.get("aqm"),
        cfg.get("buffer_packets"),
        cfg.get("cca"),
        cfg.get("duration_s"),
        cfg.get("concurrency"),
        cfg.get("trial"),
    )


def find_duplicates(reports: list[RunReport]) -> dict[tuple, list[RunReport]]:
    groups: dict[tuple, list[RunReport]] = defaultdict(list)
    for rep in reports:
        if rep.record:
            groups[config_signature(rep.record)].append(rep)
    return {k: v for k, v in groups.items() if len(v) > 1}


# ── reporting ────────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    default_root = (
        Path(
            os.environ.get(
                "PRAMANA_RESULTS_DIR", str(Path(__file__).parent / "results")
            )
        )
        / "pramana_runs"
    )
    ap.add_argument(
        "root",
        nargs="?",
        default=str(default_root),
        help=f"results/pramana_runs directory (default: {default_root})",
    )
    ap.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="also print the detail lines for warnings",
    )
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    # A run that died before writing its record is exactly the case worth
    # catching, so a directory that *looks* like a run counts as one even
    # without a record.json — it fails RECORD_PARSE instead of vanishing.
    def looks_like_run(d: Path) -> bool:
        if (d / "record.json").exists():
            return True
        return any(
            (d / m).exists()
            for m in ("qoe", "capture.pcap", "capture.pcapng", "collector.log")
        )

    run_dirs = sorted(d for d in root.iterdir() if d.is_dir() and looks_like_run(d))
    if not run_dirs:
        print(
            f"error: no run directories (with a record.json) under {root}",
            file=sys.stderr,
        )
        return 2

    reports = [check_run(d) for d in run_dirs]
    check_quality_vs_bandwidth(reports)
    dups = find_duplicates(reports)

    # Duplicate configs are a property of the set, not of one run; annotate each
    # member and name the newest as canonical.
    for sig, members in dups.items():
        members.sort(key=lambda r: (r.timestamp or 0))
        canonical = members[-1]
        for rep in members:
            others = [m.name for m in members if m is not rep]
            mark = " (canonical: newest)" if rep is canonical else ""
            rep.warn(
                W_DUP,
                f"same config as {len(others)} other run(s){mark}; "
                f"canonical = {canonical.name}",
            )

    width = max(len(r.name) for r in reports)
    width = min(width, 78)
    print(f"\nValidating {len(reports)} run(s) under {root}\n")
    print(f"{'#':>3}  {'run':<{width}}  status")
    print(f"{'-' * 3}  {'-' * width}  {'-' * 40}")
    for i, rep in enumerate(reports, 1):
        name = rep.name if len(rep.name) <= width else rep.name[: width - 1] + "…"
        print(f"{i:>3}  {name:<{width}}  {rep.status}")

    n_fail = sum(1 for r in reports if r.failures)
    n_warn = sum(1 for r in reports if r.warnings and not r.failures)
    n_pass = sum(1 for r in reports if r.status == "PASS")

    if any(r.failures for r in reports):
        print("\n" + "=" * 100)
        print("FAILURES — offending value vs expected bound")
        print("=" * 100)
        for rep in reports:
            if not rep.failures:
                continue
            print(f"\n{rep.name}")
            for rule, detail in rep.failures:
                print(f"    [{rule}] {detail}")

    warned = [r for r in reports if r.warnings]
    if warned:
        print("\n" + "=" * 100)
        print("WARNINGS — plausibility, not proof")
        print("=" * 100)
        for rep in warned:
            shown = [(ru, d) for ru, d in rep.warnings if args.verbose or ru != W_DUP]
            if not shown:
                continue
            print(f"\n{rep.name}")
            for rule, detail in shown:
                print(f"    [{rule}] {detail}")

    if dups:
        print("\n" + "=" * 100)
        print(f"DUPLICATE CONFIGS — {len(dups)} config(s) run more than once")
        print("=" * 100)
        for sig, members in sorted(dups.items(), key=lambda kv: str(kv[0])):
            apps, bw, up, lat, loss, aqm, buf, cca, dur, conc, trial = sig
            print(
                f"\n  {'+'.join(apps)} @ {bw}Mbps/{lat}ms/{loss}pct/{aqm}/{cca} "
                f"dur={dur} conc={conc} trial={trial}  → {len(members)} runs"
            )
            for m in members:
                tag = "canonical" if m is members[-1] else "         "
                print(f"      {tag}  {m.name}  ({m.status})")

    n_any_warn = sum(1 for r in reports if r.warnings)
    print("\n" + "=" * 100)
    print(
        f"{n_pass} passed, {n_fail} failed, {n_warn} warned "
        f"(of {len(reports)} runs)"
    )
    if n_any_warn > n_warn:
        print(
            f"  note: {n_any_warn} run(s) carry warnings in total; "
            f"{n_any_warn - n_warn} of those also failed a hard rule"
        )
    print("=" * 100)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
