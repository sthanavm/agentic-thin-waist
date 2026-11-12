#!/usr/bin/env python3
"""Demand over the sustained window, plus infra_share_pct, per CPUC capture.

PROVENANCE. This is the computation that produced the demand column in the
published streaming and conferencing tables. Until now it lived only as a
scratch script on vm4 (`/home/student/cpuc_verify/demand.py`), which meant a
published figure could not be recomputed from the repository -- the same
problem the manifest work was done to fix for the charts. It is committed here
unchanged in arithmetic and parameterised in scope, so a reader can rerun it.
`tests/test_harness.py` pins the window definition against a synthetic series.

WHY THE WINDOW AND NOT THE WHOLE CAPTURE. The capture starts before the page
does, so a whole-capture mean divides the app's bytes by time in which the app
did not exist. The sustained window runs from the app's first byte to its
last, which drops the page-load lead-in and any trailing silence while keeping
the app's own mid-session behaviour -- a streaming player filling a buffer and
going quiet IS its demand, and must not be edited out.

Startup is deliberately NOT recomputed here: it is measured from page load and
stays as published.

Two figures are reported because they answer different questions:
  demand_window_mbps -- total bytes / sustained window, i.e. sustained load
  demand_active_mbps -- mean over seconds that carried bytes, i.e. rate while
                        actually fetching

`infra_share_pct` is browser_infra's share of (app + infra) download bytes, so
every demand figure carries how much of the link the app actually had. A
demand figure without it is not interpretable.

The run-directory pattern deliberately EXCLUDES slugged variants (`vcap...`
for a changed viewport, `forcedq` for a pinned rendition, `noav01` for a
blocked codec). Those are different experiments and must never be averaged
into a sweep's demand -- which is exactly what a looser pattern would do
silently.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpuc import deps  # noqa: E402

DEFAULT_ROOT = os.environ.get(
    "CPUC_RUNS_ROOT",
    "/home/student/cpuc/experiments/pramana/results/pramana_runs",
)

# Slug fragments that mark a run as a variant of a different experiment. A run
# whose directory name carries any of these is skipped.
VARIANT_MARKERS = ("vcap", "forcedq", "noav01", "nocy", "blockcodec")


def run_pattern(apps: tuple[str, ...]) -> re.Pattern:
    """`<app>_<rate>mbps_<rtt>ms_0pct_pfifo_cubic_solo_t<trial>_<hash>`."""
    alt = "|".join(re.escape(a) for a in apps)
    return re.compile(
        rf"^(?P<app>{alt})_(?P<rate>[\d.]+)mbps_(?P<rtt>\d+)ms_0pct_"
        rf"pfifo_cubic_solo_t\d+_(?P<tail>[0-9a-zA-Z]+)$"
    )


def dl_mb(buckets: dict, name: str) -> float:
    d = (buckets.get(name) or {}).get("download") or {}
    return float(d.get("total_mb") or 0.0)


def sustained_window(
    mbps: list[float], times: list[float]
) -> tuple[int, int, float, float]:
    """(first, last, bin_s, span_s) of the app's own byte-carrying window.

    Split out from `main` so a test can pin it: the window is the single
    definition every published demand figure rests on.
    """
    nz = [i for i, v in enumerate(mbps) if v and v > 0]
    if not nz:
        return -1, -1, 0.0, 0.0
    first, last = nz[0], nz[-1]
    bin_s = (times[1] - times[0]) if len(times) > 1 else 1.0
    return first, last, bin_s, (last - first + 1) * bin_s


def download_series(at, app: str):
    """The app's download throughput series, whatever key it is filed under."""
    t = (at.per_app.get(app) or {}).get("download")
    if t is not None:
        return t
    for _k, v in (at.per_app.get(app) or {}).items():
        if getattr(v, "direction", "") == "download":
            return v
    return None


def rows_for(
    root: str, apps: tuple[str, ...], allow_variants: bool = False
) -> list[dict]:
    deps.install()
    import pramana_helpers as H  # noqa: PLC0415

    pat = run_pattern(apps)
    out: list[dict] = []
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        name = os.path.basename(d)
        m = pat.match(name)
        if not m:
            continue
        if not allow_variants and any(v in m.group("tail") for v in VARIANT_MARKERS):
            continue
        recp, pcap = os.path.join(d, "record.json"), os.path.join(d, "capture.pcap")
        if not (os.path.exists(recp) and os.path.exists(pcap)):
            continue
        rec = json.load(open(recp))
        cfg = rec.get("config") or {}
        app = m.group("app")
        cap = float(cfg.get("bandwidth_mbps") or m.group("rate"))
        try:
            at = H.attribute_capture(Path(pcap), [app], cap)
            li = H.link_interference(at, [app], cap)
        except Exception as exc:  # noqa: BLE001 - a failed capture is reported
            print(f"!! {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        t = download_series(at, app)
        if t is None or not getattr(t, "mbps", None):
            continue
        mbps, times = list(t.mbps), list(t.times)
        first, last, bin_s, span_s = sustained_window(mbps, times)
        if first < 0:
            continue
        total_mb = float(t.total_mb or 0.0)
        active = [mbps[i] for i in range(len(mbps)) if mbps[i] and mbps[i] > 0]

        infra_mb = dl_mb(li.buckets, "browser_infra")
        denom = total_mb + infra_mb
        pq = (rec.get("player_qoe") or {}).get(app) or {}
        out.append(
            {
                "run": name,
                "app": app,
                "cap_mbps": cap,
                "rtt_ms": float(m.group("rtt")),
                "app_total_mb": round(total_mb, 3),
                "window_start_s": round(first * bin_s, 1),
                "window_span_s": round(span_s, 1),
                "capture_span_s": round(len(mbps) * bin_s, 1),
                "demand_window_mbps": (
                    round(total_mb * 8.0 / span_s, 3) if span_s else None
                ),
                "demand_active_mbps": round(sum(active) / len(active), 3),
                "demand_whole_capture_mbps": round(
                    total_mb * 8.0 / (len(mbps) * bin_s), 3
                ),
                "peak_mbps": t.peak_throughput_mbps,
                "infra_mb": round(infra_mb, 3),
                "infra_share_pct": round(100.0 * infra_mb / denom, 1) if denom else 0.0,
                "resolution_p": pq.get("video_resolution_p"),
                "classification": t.classification,
                "basis": (
                    "mean over the sustained window (first app byte to last), "
                    "from the run's own capture"
                ),
            }
        )
    return out


def by_capacity(rows: list[dict], app: str, rtt_ms: float | None = None) -> dict:
    """`{capacity: {...}}`, the shape `report.load_demand` reads.

    Averaged across repetitions at the same capacity, because the sweep now
    runs three reps per point and a single row's figure would be an arbitrary
    choice between them. `n` is carried so a reader can see how many.
    """
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        if r["app"] != app:
            continue
        if rtt_ms is not None and abs(r["rtt_ms"] - rtt_ms) > 1e-6:
            continue
        buckets.setdefault(f"{r['cap_mbps']:g}", []).append(r)
    out = {}
    for cap, rs in buckets.items():
        n = len(rs)
        out[cap] = {
            f"{app}_avg_mbps": round(sum(r["demand_window_mbps"] for r in rs) / n, 3),
            f"{app}_active_mbps": round(
                sum(r["demand_active_mbps"] for r in rs) / n, 3
            ),
            "infra_share_pct": round(sum(r["infra_share_pct"] for r in rs) / n, 1),
            "n": n,
            "runs": [r["run"] for r in rs],
            "basis": rs[0]["basis"],
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--apps", default="youtube,meet,twitch,vimeo")
    ap.add_argument("--out", default="")
    ap.add_argument(
        "--by-capacity-for",
        default="",
        help="also write <out>.bycap.json in the shape report.load_demand "
        "reads, for this app",
    )
    ap.add_argument("--rtt", type=float, default=None)
    ap.add_argument("--allow-variants", action="store_true")
    args = ap.parse_args()

    apps = tuple(a.strip() for a in args.apps.split(",") if a.strip())
    rows = rows_for(args.root, apps, args.allow_variants)
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(rows, fh, indent=1)
        print(f"wrote {args.out}: {len(rows)} runs\n")
    if args.by_capacity_for and args.out:
        path = args.out + ".bycap.json"
        with open(path, "w") as fh:
            json.dump(by_capacity(rows, args.by_capacity_for, args.rtt), fh, indent=1)
        print(f"wrote {path}")

    hdr = (
        f"{'app':<8}{'cap':>7}{'rtt':>5}{'res':>6}{'app_MB':>8}{'win0':>6}"
        f"{'span':>6}{'whole':>7}{'window':>8}{'active':>8}{'infra%':>8}"
    )
    print(hdr)
    for r in sorted(rows, key=lambda r: (r["app"], r["rtt_ms"], -r["cap_mbps"])):
        print(
            f"{r['app']:<8}{r['cap_mbps']:>7g}{r['rtt_ms']:>5g}"
            f"{str(r['resolution_p'] or '-'):>6}{r['app_total_mb']:>8.2f}"
            f"{r['window_start_s']:>6.1f}{r['window_span_s']:>6.0f}"
            f"{r['demand_whole_capture_mbps']:>7.2f}"
            f"{r['demand_window_mbps']:>8.2f}{r['demand_active_mbps']:>8.2f}"
            f"{r['infra_share_pct']:>7.1f}%"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
