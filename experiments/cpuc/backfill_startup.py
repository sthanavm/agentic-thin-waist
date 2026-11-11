#!/usr/bin/env python3
"""Fill in the startup column that `supp1080.py` left blank, from the records.

WHAT WENT WRONG. `supp1080.py` and `yt1080_link.py` read startup as
`player_qoe[app]["startup_delay_ms"]`. That key does not exist at that level
-- it lives under the `uniform` block (schema `uniform_qoe/1`), which is where
`qoe_labels.extract_streaming_metrics` reads it for every graded sweep row. So
the lookup returned None for every run and the CSV's `startup_s` column came
out EMPTY. All three published rows in `results_1080p/supp1080_runs.csv` are
blank there, and nothing distinguished that from "startup was not measured".

It WAS measured. The figure was in each run's own `record.json` the whole
time, under `uniform.startup_delay_ms`, with its basis recorded alongside
(`requestVideoFrameCallback first presentationTime`).

WHY A BACKFILL AND NOT A RERUN. The runs are valid -- the bug was in reading
the record, not in taking it -- so rerunning them would spend an hour to
produce the same captures and would replace published rows with new ones
taken on a different day. This reads the figure out of the records those rows
already point at via their own `run_dir` column, and touches nothing else.

Both drivers are fixed, so new rows carry startup directly and this script is
only for the rows taken before the fix.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpuc import qoe_labels  # noqa: E402

FIELDS = (
    "startup_s",
    "startup_basis",
    "rebuffer_events",
    "rebuffer_ms",
    "rebuffer_ratio",
)


def metrics_from_record(run_dir: str, app: str = "youtube") -> dict | None:
    path = os.path.join(run_dir, "record.json")
    if not os.path.isfile(path):
        return None
    with open(path) as fh:
        rec = json.load(fh)
    pq = (rec.get("player_qoe") or {}).get(app) or {}
    if not pq:
        return None
    m = qoe_labels.extract_streaming_metrics(pq)
    return {
        "startup_s": m.startup_delay_s,
        "startup_basis": m.startup_basis,
        "rebuffer_events": m.rebuffer_events,
        "rebuffer_ms": m.rebuffer_duration_ms,
        "rebuffer_ratio": m.rebuffer_ratio,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv_path")
    ap.add_argument("--app", default="youtube")
    ap.add_argument(
        "--only-blank",
        action="store_true",
        default=True,
        help="only fill rows whose startup_s is empty (the default; a row "
        "that already carries a figure is never overwritten)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(args.csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        cols = list(reader.fieldnames or [])
    for f in FIELDS:
        if f not in cols:
            cols.append(f)

    filled, skipped, missing = 0, 0, []
    for r in rows:
        if args.only_blank and (r.get("startup_s") or "").strip():
            skipped += 1
            continue
        run_dir = (r.get("run_dir") or "").strip()
        if not run_dir:
            missing.append(f"{r.get('run_id')}: no run_dir column value")
            continue
        got = metrics_from_record(run_dir, args.app)
        if not got:
            missing.append(f"{r.get('run_id')}: no player_qoe at {run_dir}")
            continue
        for k, v in got.items():
            r[k] = "" if v is None else v
        filled += 1
        print(
            f"  {r.get('variant') or r.get('rate_down')}: startup "
            f"{got['startup_s']} s ({got['startup_basis']})"
        )

    print(
        f"filled {filled}, already had a figure {skipped}, could not fill {len(missing)}"
    )
    for m in missing:
        print(f"  ! {m}")
    if args.dry_run:
        print("dry run; nothing written")
        return 1 if missing else 0

    if filled:
        shutil.copy2(args.csv_path, args.csv_path + ".pre-startup-backfill")
        with open(args.csv_path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {args.csv_path} (previous kept as .pre-startup-backfill)")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
