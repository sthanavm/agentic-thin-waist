#!/usr/bin/env python3
"""Audit the Zoom resolution label against the stored samples.

`video_resolution_p` is the MODAL active decoded height. For a tile that changes
size mid-call that single number can sit far from what the call looked like, and
it is what made one cell read 180p while a report quoted its 640x360 tile. This
prints, per cell and from the samples only, the decoded-size distribution, the
rendered box, and what the label resolves to.
"""

import collections
import json
import pathlib
import sys

CKPT = pathlib.Path("/tmp/sweep_m1.json")


def samples(d, app):
    p = pathlib.Path(d) / "qoe" / f"{app}_stats.jsonl"
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


def main():
    ck = json.loads(CKPT.read_text())
    print(
        "%-26s %-34s %-16s %-7s %-7s %s"
        % ("cell", "decoded sizes (share)", "box", "label", "mode%", "observed")
    )
    for e in ck["done"]:
        st = samples(e["run_dir"], "zoom")
        if not st:
            print("%-26s no samples" % e["cell"])
            continue
        res = collections.Counter(
            s.get("resolution") for s in st if s.get("resolution") and s.get("resolution") != "0x0"
        )
        box = collections.Counter(
            f"{s.get('player_elem_w')}x{s.get('player_elem_h')}" for s in st
        )
        heights = [
            s.get("video_height") for s in st if s.get("video_height")
        ]
        hc = collections.Counter(heights)
        label = hc.most_common(1)[0][0] if hc else None
        share = (hc[label] / len(heights) * 100) if heights else 0
        top = ", ".join(
            f"{k}:{v*100//len(st)}%" for k, v in res.most_common(3)
        )
        rec = json.loads((pathlib.Path(e["run_dir"]) / "record.json").read_text())
        q = (rec.get("player_qoe") or {}).get("zoom") or {}
        print(
            "%-26s %-34s %-16s %-7s %-7s %s"
            % (
                e["cell"],
                top,
                box.most_common(1)[0][0],
                q.get("video_resolution_p"),
                f"{share:.0f}%",
                q.get("resolutions_observed"),
            )
        )
        if label != q.get("video_resolution_p"):
            print(f"    !! recomputed mode {label} != recorded {q.get('video_resolution_p')}")


if __name__ == "__main__":
    sys.exit(main())
