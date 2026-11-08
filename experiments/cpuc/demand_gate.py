#!/usr/bin/env python3
"""Does each published demand figure cost what that resolution actually costs?

This exists because a wrong one shipped. YouTube's demand was published as
0.51 Mb/s while the player was delivering 720p -- which no codec does -- and it
reached a PR body, a README and a chart annotation before anyone noticed. The
cause was a hostname rule attributing YouTube's own media to `browser_infra`
(fixed in #25) on top of a mean taken over a window that started before the
page did.

So no demand figure is published without passing this. Run it on the generated
table, not on intermediate data:

    python3 -m cpuc.demand_gate results/streaming_iqb_vs_qoe_table.csv
    python3 -m cpuc.demand_gate results/meet_iqb_vs_qoe_table.csv

Exit status is 1 if anything is flagged, so it can gate a publish step.

The bands are deliberately wide. They are not a model of any codec; they are
the range outside which a figure is not a plausible cost for that resolution
and needs a stated reason before it goes out. The top is generous because a
session that downshifted carries its earlier, larger segments in the same byte
total, and a capacity-limited row is judged against the cap -- at a 0.5 Mb/s cap
no resolution can draw more than 0.5 Mb/s, and flagging that would be flagging
physics.
"""

from __future__ import annotations

import csv
import sys

# resolution -> (floor, ceiling) in Mb/s
BANDS = {
    144: (0.05, 0.60),
    240: (0.15, 1.40),
    360: (0.25, 2.00),
    480: (0.40, 3.00),
    720: (0.80, 5.00),
    1080: (1.50, 9.00),
    1440: (4.00, 16.0),
    2160: (8.00, 40.0),
}

# Streaming and conferencing tables name these differently.
CAP_KEYS = ("rate_down_mbps", "rate_mbps_symmetric")
DEMAND_KEYS = ("app_demand_mbps", "meet_demand_mbps")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _first(row, keys):
    for k in keys:
        if k in row:
            got = _f(row[k])
            if got is not None:
                return got
    return None


def check(path: str) -> int:
    rows = list(csv.DictReader(open(path)))
    counts = {"PASS": 0, "FLAG": 0, "SKIP": 0}
    flags = []

    print(f"{'cap':>7}{'rtt':>6}{'res':>6}{'demand':>8}{'infra%':>8}  verdict")
    for r in rows:
        cap = _first(r, CAP_KEYS)
        demand = _first(r, DEMAND_KEYS)
        res = _f(r.get("resolution_p"))
        infra = _f(r.get("infra_share_pct"))
        rtt = _f(r.get("rtt_configured_ms"))

        if demand is None or not res or cap is None:
            counts["SKIP"] += 1
            print(
                f"{cap or 0:>7g}{rtt or 0:>6g}{'-':>6}{'-':>8}"
                f"{'-':>8}  skip (no demand or no resolution)"
            )
            continue

        lo, hi = BANDS.get(int(res), (0.0, float("inf")))
        lo_eff, hi_eff = min(lo, 0.80 * cap), min(hi, cap)
        if demand < lo_eff:
            verdict = "FLAG"
            why = f"{demand:.2f} below the {lo_eff:.2f} floor for {int(res)}p"
        elif demand > hi_eff:
            verdict = "FLAG"
            why = f"{demand:.2f} above the {hi_eff:.2f} ceiling for {int(res)}p"
        else:
            verdict = "PASS"
            why = f"within [{lo_eff:.2f}, {hi_eff:.2f}] for {int(res)}p"
        counts[verdict] += 1
        if verdict == "FLAG":
            flags.append((cap, rtt, why))
        print(
            f"{cap:>7g}{rtt or 0:>6g}{int(res):>6}{demand:>8.2f}"
            f"{(infra if infra is not None else 0):>7.0f}%  "
            f"{'ok  ' if verdict == 'PASS' else 'FLAG'} {why}"
        )

    print(
        f"\nPASS {counts['PASS']}   FLAG {counts['FLAG']}   "
        f"SKIP {counts['SKIP']}   of {len(rows)}"
    )
    for cap, rtt, why in flags:
        print(f"  FLAGGED {cap:g} Mb/s @ {rtt or 0:g} ms: {why}")
    return 1 if flags else 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    worst = 0
    for path in sys.argv[1:]:
        print(f"=== {path}")
        worst = max(worst, check(path))
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
