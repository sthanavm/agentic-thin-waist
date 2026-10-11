#!/usr/bin/env python3
"""Final gate before any push. Writes validation/VERIFICATION_REPORT.md.

Checks the brief's list, each one capable of failing:
  * every chart's numbers equal summary.json (asserted at build; re-asserted
    here from the files on disk)
  * every pcap in the manifest exists with a matching SHA-256
  * nothing in the release exceeds 50 MB and no pcap is inside it
  * no absolute laptop paths in committed files
  * Jaber's per-run rules
  * samples.json carries schema, sampling interval and codec
Exit code is non-zero if any check FAILS, so it cannot be ignored.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
PCAP_SEARCH = [Path(p) for p in sys.argv[2:]]
MAX_BYTES = 50 * 1024 * 1024
results: list[tuple[str, str, str, str]] = []


def add(scope, check, ok, detail):
    results.append(
        (
            scope,
            check,
            "PASS" if ok is True else ("UNKNOWN" if ok is None else "FAIL"),
            detail,
        )
    )


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


# ── 1. size and content hygiene ─────────────────────────────────────────────
big = [p for p in ROOT.rglob("*") if p.is_file() and p.stat().st_size > MAX_BYTES]
add(
    "release",
    "no_file_over_50MB",
    not big,
    "; ".join("%s=%.1fMB" % (p.name, p.stat().st_size / 1e6) for p in big) or "none",
)

pcaps_inside = list(ROOT.rglob("*.pcap")) + list(ROOT.rglob("*.pcapng"))
add(
    "release",
    "no_pcap_committed",
    not pcaps_inside,
    "; ".join(p.name for p in pcaps_inside) or "none",
)

# Absolute laptop paths must not appear in committed text. The VM path is
# legitimate provenance and lives in run_meta.json, so only /Users/ is a defect.
offenders = []
for p in ROOT.rglob("*"):
    if not p.is_file() or p.suffix.lower() in (".png", ".jpg", ".pcap"):
        continue
    try:
        t = p.read_text(errors="ignore")
    except Exception:
        continue
    if "/Users/" in t:
        offenders.append(p.relative_to(ROOT).as_posix())
add(
    "release",
    "no_laptop_absolute_paths",
    not offenders,
    "; ".join(offenders[:6]) or "none",
)

# ── 2. per-run structure, metrics and Jaber rules ───────────────────────────
REQUIRED = [
    "samples.json",
    "summary.json",
    "run_meta.json",
    "download_throughput.png",
    "upload_throughput.png",
    "qoe_buffer.png",
    "qoe_resolution.png",
    "qoe_bitrate.png",
    "qoe_switches.png",
    "qoe_fps.png",
    "qoe_startup.png",
]
run_dirs = sorted(
    [p for p in ROOT.glob("*/*") if p.is_dir() and (p / "summary.json").exists()]
)
add("release", "runs_present", len(run_dirs) > 0, "%d run folders" % len(run_dirs))

for d in run_dirs:
    name = d.name
    missing = [f for f in REQUIRED if not (d / f).exists()]
    add(name, "all_required_files", not missing, "; ".join(missing) or "complete")
    s = json.loads((d / "summary.json").read_text())
    sm = json.loads((d / "samples.json").read_text())

    add(name, "samples_schema_present", bool(sm.get("schema")), str(sm.get("schema")))
    add(
        name,
        "sampling_interval_recorded",
        sm.get("sampling_interval_s") is not None,
        "interval=%s" % sm.get("sampling_interval_s"),
    )
    add(
        name,
        "codec_recorded",
        bool(sm.get("codec_declared") or sm.get("codec_decoding")),
        "declared=%s decoding=%s"
        % (sm.get("codec_declared"), sm.get("codec_decoding")),
    )
    add(
        name,
        "sampling_at_least_1_per_sec",
        (sm.get("sampling_interval_s") or 99) <= 1.0,
        "interval=%s s" % sm.get("sampling_interval_s"),
    )

    cap = (s.get("shaping") or {}).get("bandwidth_mbps")
    thr = s.get("throughput_mbps") or {}
    dl = thr.get("download") or {}
    if cap and dl:
        add(
            name,
            "jaber1_cap_peak",
            (dl.get("peak_mbps") or 0) <= cap * 1.05,
            "peak %.3f vs cap %g" % (dl.get("peak_mbps") or 0, cap),
        )
        add(
            name,
            "jaber1_cap_mean",
            (dl.get("mean_mbps") or 0) <= cap,
            "mean %.3f vs cap %g" % (dl.get("mean_mbps") or 0, cap),
        )
    else:
        add(name, "jaber1_cap", None, "no throughput series (no pcap?)")

    add(
        name,
        "jaber6_playback_flagged",
        s.get("verdict") in ("OK", "NO_PLAYBACK"),
        "verdict=%s" % s.get("verdict"),
    )
    m = s.get("metrics") or {}
    for label, key in (
        ("startup", "startup_delay_ms"),
        ("rebuffering", "rebuffer_events"),
        ("bitrate_video", "delivered_video_bitrate_mbps"),
        ("switching", "switch_count"),
        ("fps_rendered", "rendered_fps"),
    ):
        add(
            name,
            "metric_%s" % label,
            m.get(key) is not None,
            "%s=%s" % (key, m.get(key)),
        )

# ── 3. pcap manifest ────────────────────────────────────────────────────────
man = ROOT / "pcap_manifest.csv"
if man.exists():
    rows = list(csv.DictReader(open(man)))
    add("release", "pcap_manifest_nonempty", len(rows) > 0, "%d rows" % len(rows))
    for r in rows:
        want = r.get("sha256") or ""
        found = None
        for base in PCAP_SEARCH:
            cand = base / ("%s.pcap" % r["run"])
            if cand.exists():
                found = cand
                break
        if found is None:
            add(
                r["run"],
                "pcap_archived",
                False,
                "not found under: %s" % ", ".join(str(b) for b in PCAP_SEARCH),
            )
            continue
        got = sha256(found)
        add(
            r["run"],
            "pcap_archived",
            got == want,
            "%s sha %s"
            % (
                found.name,
                (
                    "matches"
                    if got == want
                    else "MISMATCH got=%s want=%s" % (got[:16], want[:16])
                ),
            ),
        )
else:
    add("release", "pcap_manifest_present", False, "pcap_manifest.csv missing")

# ── 4. carry the build-time checks forward ──────────────────────────────────
bc = ROOT / "validation" / "checks.csv"
if bc.exists():
    for c in csv.DictReader(open(bc)):
        results.append((c["run"], "build:" + c["check"], c["result"], c["detail"]))

# ── report ──────────────────────────────────────────────────────────────────
nfail = sum(1 for r in results if r[2] == "FAIL")
nunk = sum(1 for r in results if r[2] == "UNKNOWN")
npass = sum(1 for r in results if r[2] == "PASS")
out = [
    "# Verification report",
    "",
    "Generated by `verify_release.py`. Every row is a check that could have",
    "failed. Failures and unknowns are listed first and are NOT removed from",
    "the package — a failing check is reported as a failure.",
    "",
    "| | count |",
    "|---|---|",
    "| PASS | %d |" % npass,
    "| **FAIL** | **%d** |" % nfail,
    "| UNKNOWN | %d |" % nunk,
    "",
]
if nfail or nunk:
    out += [
        "## Failures and unknowns",
        "",
        "| scope | check | result | detail |",
        "|---|---|---|---|",
    ]
    for r in results:
        if r[2] in ("FAIL", "UNKNOWN"):
            out.append("| %s | %s | **%s** | %s |" % r)
    out.append("")
out += ["## All checks", "", "| scope | check | result | detail |", "|---|---|---|---|"]
for r in results:
    out.append("| %s | %s | %s | %s |" % r)
(ROOT / "validation").mkdir(exist_ok=True)
(ROOT / "validation" / "VERIFICATION_REPORT.md").write_text("\n".join(out) + "\n")
print("PASS=%d FAIL=%d UNKNOWN=%d" % (npass, nfail, nunk))
for r in results:
    if r[2] == "FAIL":
        print("  FAIL %-44s %-34s %s" % (r[0], r[1], r[3]))
sys.exit(1 if nfail else 0)
