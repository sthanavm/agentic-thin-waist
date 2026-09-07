#!/usr/bin/env python3
"""Backfill ``per_app_stats[app].player_qoe_available`` in saved records.

The run writer used to hardcode that flag to False, so every record disagreed
with its own ``player_qoe`` payload — always in the same direction (False on
the mirror, real metrics in the payload). This rewrites the mirrored flag to
agree with the payload.

Exactly one field per app is touched. Every other byte of the record is
re-serialised with the writer's own settings (``indent=2, default=str``), so a
run whose flag was already correct comes out byte-identical.

Usage:
    python3 experiments/pramana/backfill_player_flag.py [results_root] [--apply]

Without ``--apply`` it is a dry run and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def truth(pq: dict | None) -> bool:
    """Does this app's player payload carry real, advancing playback data?"""
    if not isinstance(pq, dict):
        return False
    return bool(pq.get("player_qoe_available"))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    default_root = (
        Path(
            os.environ.get(
                "PRAMANA_RESULTS_DIR", str(Path(__file__).parent / "results")
            )
        )
        / "pramana_runs"
    )
    ap.add_argument("root", nargs="?", default=str(default_root))
    ap.add_argument(
        "--apply", action="store_true", help="write the change (default: dry run)"
    )
    ap.add_argument(
        "--index",
        action="store_true",
        help="also rewrite the same flag inside dataset_index.jsonl",
    )
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    changed_runs = 0
    changed_apps = 0
    scanned = 0

    for d in sorted(root.iterdir()):
        rec_path = d / "record.json"
        if not d.is_dir() or not rec_path.exists():
            continue
        scanned += 1
        try:
            rec = json.loads(rec_path.read_text())
        except Exception as exc:
            print(f"  ! skipping {d.name}: unreadable ({type(exc).__name__})")
            continue

        player = rec.get("player_qoe") or {}
        per_app = rec.get("per_app_stats") or {}
        edits = []
        for app, st in per_app.items():
            if not isinstance(st, dict):
                continue
            want = truth(player.get(app))
            if bool(st.get("player_qoe_available")) != want:
                edits.append((app, st.get("player_qoe_available"), want))
                st["player_qoe_available"] = want

        if not edits:
            continue
        changed_runs += 1
        changed_apps += len(edits)
        print(f"  {d.name}")
        for app, was, now in edits:
            print(f"      {app}: {was!r} -> {now!r}")
        if args.apply:
            rec_path.write_text(json.dumps(rec, indent=2, default=str))

    verb = "rewrote" if args.apply else "would rewrite"
    print(
        f"\n{verb} {changed_apps} flag(s) across {changed_runs} run(s) "
        f"(scanned {scanned})"
    )

    if args.index:
        idx = root / "dataset_index.jsonl"
        if idx.exists():
            rows, n = [], 0
            for line in idx.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                j = json.loads(line)
                player = j.get("player_qoe") or {}
                for app, st in (j.get("per_app_stats") or {}).items():
                    if not isinstance(st, dict):
                        continue
                    want = truth(player.get(app))
                    if bool(st.get("player_qoe_available")) != want:
                        st["player_qoe_available"] = want
                        n += 1
                rows.append(j)
            print(f"{verb} {n} flag(s) in dataset_index.jsonl")
            if args.apply:
                idx.write_text(
                    "\n".join(json.dumps(r, default=str) for r in rows) + "\n"
                )

    if not args.apply:
        print("\n(dry run — pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
