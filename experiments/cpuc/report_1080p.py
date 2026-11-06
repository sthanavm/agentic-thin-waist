"""Charts for the 1080p-viewport supplementary set.

Three points that all delivered 720p would make a flat, contentless
resolution-vs-bandwidth line. What the set actually shows is a gap, so these
charts plot the gap rather than the outcome:

  1. the viewport was raised and the rendition did not follow, with the main
     sweep's element box on the same axes as the baseline,
  2. the player's own "optimal" sitting at 1080p while it serves 720p,
  3. the dropped-frame ratio that explains why.

Run against the supplementary CSV; writes into the same directory.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from cpuc.plots import _iqb_line, _logx, _save  # noqa: E402

DL_MIN = 25.0

# Measured on the main sweep's 25 Mb/s run, which is the baseline this set is
# contrasted against: window_outer 1919x1079, window_inner 1911x988, and the
# element the player actually drew into.
MAIN_ELEM_H = 749
MAIN_DELIVERED_P = 720

C_ELEM = "#6A1B9A"
C_OPT = "#F9A825"
C_SERVED = "#1565C0"
C_MAIN = "#9E9E9E"
C_DROP = "#C62828"


def load(path: str) -> list[dict]:
    with open(path) as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        if not r.get("rate_down"):
            continue
        out.append(
            {
                "rate": float(r["rate_down"]),
                "elem_h": _h(r.get("elem_box")),
                "served_p": _h(r.get("resolution_delivered")),
                "optimal_p": _h(r.get("player_optimal")),
                "dropped_pct": _f(r.get("dropped_pct")),
                "valid": r.get("valid") == "True",
            }
        )
    out.sort(key=lambda r: r["rate"])
    return out


def _h(box: str | None) -> int | None:
    """Vertical resolution from a 'WxH' or 'WxH@fps' string."""
    if not box:
        return None
    try:
        return int(str(box).split("x")[1].split("@")[0].strip())
    except Exception:  # noqa: BLE001
        return None


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def viewport_vs_rendition(rows, path):
    """The viewport was raised; the rendition did not follow.

    Three heights on one axis: the box the player was given, what it chose,
    and what the main sweep chose with a much smaller box. The two delivered
    lines sit on top of each other at 720p, which is the point -- going from a
    1331x749 element to a 1920x1080 one changed nothing.
    """
    xs = [r["rate"] for r in rows]
    fig, ax = plt.subplots(figsize=(9.5, 5.4))
    ax.plot(
        xs,
        [r["elem_h"] for r in rows],
        "s-",
        color=C_ELEM,
        lw=2.5,
        ms=9,
        label="element the player was given (this set, fullscreen)",
    )
    ax.plot(
        xs,
        [MAIN_ELEM_H] * len(xs),
        "s--",
        color=C_MAIN,
        lw=2,
        ms=7,
        label=f"element in the main sweep ({MAIN_ELEM_H}px tall)",
    )
    ax.plot(
        xs,
        [r["served_p"] for r in rows],
        "o-",
        color=C_SERVED,
        lw=3,
        ms=11,
        label="rendition served (this set)",
        zorder=4,
    )
    ax.plot(
        xs,
        [MAIN_DELIVERED_P] * len(xs),
        "o:",
        color=C_MAIN,
        lw=4,
        ms=16,
        mfc="none",
        label="rendition served (main sweep) -- same 720p",
        zorder=5,
    )
    ax.annotate(
        "both lines sit at 720p: the box grew 44% in height\n"
        "and the rendition did not move",
        xy=(xs[len(xs) // 2], 720),
        xytext=(0, -58),
        textcoords="offset points",
        ha="center",
        fontsize=9,
        color="#424242",
        bbox=dict(boxstyle="round,pad=0.35", fc="white", ec="#BDBDBD"),
        arrowprops=dict(arrowstyle="->", color="#757575"),
    )
    ax.set_ylim(600, 1200)
    ax.set_yticks([720, 749, 1080])
    ax.set_yticklabels(["720p", "749px", "1080p"])
    ax.set_xlabel("shaped download capacity (Mb/s, log scale)")
    ax.set_ylabel("vertical resolution / element height (px)")
    _logx(ax, xs)
    _iqb_line(ax, DL_MIN, f"IQB minimum {DL_MIN:g} Mb/s")
    ax.legend(loc="upper left", fontsize=8.5)
    ax.set_title("The viewport was raised to 1080p; the rendition stayed at 720p")
    return _save(fig, path)


def optimal_vs_served(rows, path):
    """What the player says it should serve, against what it serves.

    YouTube reports a "current / optimal" pair. Optimal tracks the viewport
    and sits at 1080p at every point, so the player is not failing to notice
    the larger element -- it is declining to use it.
    """
    xs = [r["rate"] for r in rows]
    opt = [r["optimal_p"] for r in rows]
    srv = [r["served_p"] for r in rows]
    fig, ax = plt.subplots(figsize=(9.5, 5.4))
    ax.fill_between(xs, srv, opt, color="#FFF3CD", label="the gap the player chose")
    ax.plot(
        xs,
        opt,
        "^-",
        color=C_OPT,
        lw=2.5,
        ms=10,
        label="player's own 'optimal' for this viewport",
    )
    ax.plot(xs, srv, "o-", color=C_SERVED, lw=3, ms=11, label="rendition served")
    ax.set_ylim(600, 1200)
    ax.set_yticks([720, 1080])
    ax.set_yticklabels(["720p", "1080p"])
    ax.set_xlabel("shaped download capacity (Mb/s, log scale)")
    ax.set_ylabel("vertical resolution")
    _logx(ax, xs)
    _iqb_line(ax, DL_MIN, f"IQB minimum {DL_MIN:g} Mb/s")
    # Upper left: the band above the optimal line is the only empty region,
    # and anywhere on the right overlaps the IQB threshold label.
    ax.legend(loc="upper left", fontsize=9, framealpha=0.95)
    ax.set_title("YouTube knew 1080p was optimal here and served 720p anyway")
    return _save(fig, path)


def dropped_frames(rows, path):
    """The decode evidence.

    ABR backs off resolution when the client cannot decode what it is given.
    At 720p this host already drops 12-16% of frames decoding AV1 in software,
    which is the constraint the player is responding to -- not the link, which
    had spare capacity at every point.
    """
    xs = [r["rate"] for r in rows]
    ys = [r["dropped_pct"] for r in rows]
    fig, ax = plt.subplots(figsize=(9.5, 5.4))
    ax.plot(
        xs, ys, "o-", color=C_DROP, lw=2.5, ms=10, label="frames dropped at 720p (AV1)"
    )
    for x, y in zip(xs, ys):
        ax.annotate(
            f"{y:.1f}%",
            xy=(x, y),
            xytext=(0, 11),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            color=C_DROP,
        )
    ax.axhline(0, color="#9E9E9E", lw=1)
    ax.set_ylim(0, max(ys) * 1.45)
    ax.set_xlabel("shaped download capacity (Mb/s, log scale)")
    ax.set_ylabel("dropped video frames (% of total)")
    _logx(ax, xs)
    _iqb_line(ax, DL_MIN, f"IQB minimum {DL_MIN:g} Mb/s")
    ax.legend(loc="lower right", fontsize=9)
    ax.set_title("Why the player stayed at 720p: it could not decode what it had")
    return _save(fig, path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--csv", default="/home/student/cpuc_data/supp1080/supp1080_runs.csv"
    )
    ap.add_argument("--outdir", default="")
    args = ap.parse_args()

    outdir = args.outdir or os.path.dirname(os.path.abspath(args.csv))
    rows = [r for r in load(args.csv) if r["valid"]]
    if not rows:
        print("no valid rows; nothing to plot")
        return 1

    made = [
        viewport_vs_rendition(rows, os.path.join(outdir, "viewport_vs_rendition.png")),
        optimal_vs_served(rows, os.path.join(outdir, "optimal_vs_served.png")),
        dropped_frames(rows, os.path.join(outdir, "dropped_frames.png")),
    ]
    for p in made:
        print(f"  wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
