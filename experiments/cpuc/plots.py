"""Charts for the CPUC decks. Shared by the streaming and conferencing reports.

One chart per claim, rather than several claims crowded onto one pair of axes.
Every chart marks IQB's threshold, labels both axes with units, and puts
bandwidth on a log x-axis because the points span 0.5 to 100 Mb/s and a linear
axis compresses everything below 25 into the left margin.

The ordering of the QoE and IQB scales is fixed here rather than inferred from
the data, so a chart cannot silently reorder its own categories between runs.
"""

from __future__ import annotations

import math

from typing import Any, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as ticker  # noqa: E402

# Tiers, worst to best, so "higher on the chart is better" holds on both axes.
QOE_TIERS = ["unusable", "acceptable", "high"]
IQB_TIERS = ["fails minimum", "meets minimum only", "meets high"]

C_QOE = "#1565C0"
C_IQB = "#C62828"
C_IQB_LINE = "#B71C1C"
C_APP = "#2E7D32"
C_CAP = "#616161"


def _tier(value: str, scale: list[str]) -> Optional[int]:
    if not value:
        return None
    v = str(value).strip().lower()
    for i, name in enumerate(scale):
        if v == name:
            return i
    return None


def _logx(ax, xs) -> None:
    ax.set_xscale("log")
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{x:g}" for x in xs])
    # Matplotlib keeps labelling log MINOR ticks ("6 x 10^0") underneath the
    # rate labels set above, which collide with them whenever the points are
    # few or close together. The majors are the rates we actually ran.
    ax.xaxis.set_minor_formatter(ticker.NullFormatter())
    ax.grid(alpha=0.25, which="both")


def _iqb_line(ax, iqb_min: float, label: str) -> None:
    ax.axvline(iqb_min, color=C_IQB_LINE, ls=":", lw=2)
    ax.annotate(
        label,
        xy=(iqb_min, 0.5),
        xycoords=("data", "axes fraction"),
        xytext=(-8, 0),
        textcoords="offset points",
        color=C_IQB_LINE,
        fontsize=9,
        rotation=90,
        va="center",
        ha="right",
    )


def _save(fig, path: str) -> str:
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def contrast(rows, path, iqb_min, rate_key, iqb_key, title):
    """IQB's verdict against the measured QoE label, per bandwidth point.

    This is the argument in one picture: wherever the red line sits at "fails
    minimum" while the blue line sits at "high", a speed test condemns a link
    the application is serving perfectly. Those points are ringed.
    """
    pts = [r for r in rows if r.get(rate_key) and r.get("qoe_label")]
    pts.sort(key=lambda r: r[rate_key])
    xs = [r[rate_key] for r in pts]
    qoe = [_tier(r.get("qoe_label"), QOE_TIERS) for r in pts]
    iqb = [_tier(r.get(iqb_key), IQB_TIERS) for r in pts]
    if not any(v is not None for v in qoe):
        return None

    fig, ax = plt.subplots(figsize=(9.5, 5.4))
    ax.plot(xs, qoe, "o-", color=C_QOE, lw=2.5, ms=9, label="measured QoE", zorder=3)
    ax.plot(
        xs,
        iqb,
        "s--",
        color=C_IQB,
        lw=2.5,
        ms=8,
        label="IQB verdict from the speed test",
        zorder=3,
    )

    # Ring the disagreements: IQB fails, QoE high.
    ring = 0
    for x, q, i in zip(xs, qoe, iqb):
        if q == 2 and i == 0:
            ax.scatter(
                [x],
                [2],
                s=420,
                facecolors="none",
                edgecolors="#F9A825",
                lw=3,
                zorder=4,
            )
            # Two readability problems at slide size, and fixing one by
            # itself causes the other. Adjacent ringed points are close
            # enough on a log axis that their callouts overlap, so they
            # alternate height; and a callout centred on a point that sits
            # under the IQB line runs straight through it, so that one also
            # shifts left. Shifting left alone pushed this label onto its
            # neighbour, which is what the stagger is for.
            near = abs(math.log10(x) - math.log10(iqb_min)) < 0.08
            dy = 26 if ring % 2 == 0 else 52
            ring += 1
            ax.annotate(
                "IQB fails,\nQoE high",
                xy=(x, 2),
                xytext=(-26 if near else 0, dy),
                textcoords="offset points",
                ha="right" if near else "center",
                fontsize=8.5,
                color="#F57F17",
                fontweight="bold",
            )

    # A point with no QoE tier is a hole in the blue line, and a hole that is
    # not labelled reads as lost data. Name the reason on the chart instead:
    # the reference run is the baseline every other run is graded against, so
    # it is deliberately not graded itself, and a point whose app run failed
    # still has an IQB verdict from its own NDT run.
    for x, q, r in zip(xs, qoe, pts):
        if q is not None:
            continue
        raw = str(r.get("qoe_label", "")).strip().lower()
        note = {
            "deferred": "reference run\n(the baseline,\nnot graded)",
            "not collected": "QoE not\ncollected",
        }.get(raw, raw or "no QoE label")
        ax.axvline(x, color="#9E9E9E", ls=":", lw=1.2, zorder=1)
        ax.annotate(
            note,
            xy=(x, 0.62),
            xycoords=("data", "axes fraction"),
            ha="center",
            va="center",
            fontsize=8.5,
            color="#616161",
            style="italic",
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#BDBDBD", lw=0.8),
        )

    ax.set_yticks(range(len(QOE_TIERS)))
    ax.set_yticklabels(["unusable / fails min", "acceptable / meets min", "high"])
    ax.set_ylim(-0.45, 2.75)
    ax.set_xlabel("shaped download capacity (Mb/s, log scale)")
    ax.set_ylabel("verdict (higher is better)")
    _logx(ax, xs)
    _iqb_line(ax, iqb_min, f"IQB minimum {iqb_min:g} Mb/s")
    ax.legend(loc="lower right", fontsize=9)
    ax.set_title(title)
    return _save(fig, path)


def demand_vs_capacity(
    rows, path, iqb_min, rate_key, demand_key, app_label, title, peak_key=None
):
    """What the application actually used against what the link offered.

    The point is the vertical distance to IQB's threshold: if the app never
    draws anywhere near the minimum, the minimum is not describing the app's
    needs. Peak is drawn as well as mean, because an app whose peak reaches the
    cap is capacity-limited even when its mean looks modest.
    """
    pts = [r for r in rows if r.get(rate_key) and r.get(demand_key)]
    pts.sort(key=lambda r: r[rate_key])
    if not pts:
        return None
    xs = [r[rate_key] for r in pts]
    used = [r[demand_key] for r in pts]

    fig, ax = plt.subplots(figsize=(9.5, 5.4))
    ax.plot(xs, xs, color=C_CAP, lw=1.8, ls="--", label="capacity offered by the link")
    ax.plot(xs, used, "o-", color=C_APP, lw=2.5, ms=9, label=f"{app_label} mean demand")
    if peak_key:
        peaks = [r.get(peak_key) for r in pts]
        if any(p for p in peaks):
            ax.plot(
                xs,
                peaks,
                "^:",
                color="#00897B",
                lw=2,
                ms=8,
                label=f"{app_label} peak demand",
            )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{x:g}" for x in xs])
    ax.grid(alpha=0.25, which="both")
    ax.set_xlabel("shaped capacity (Mb/s, log scale)")
    ax.set_ylabel("throughput (Mb/s, log scale)")
    _iqb_line(ax, iqb_min, f"IQB minimum {iqb_min:g} Mb/s")
    ax.legend(loc="upper left", fontsize=9)
    ax.set_title(title)
    return _save(fig, path)


def single_metric(
    rows,
    path,
    iqb_min,
    rate_key,
    metric_key,
    ylabel,
    title,
    thresholds=(),
    scale=1.0,
    colour=C_QOE,
):
    """One metric against bandwidth, on its own axes.

    `thresholds` are the label boundaries this metric decides, drawn so a
    reader can see which side of them each point falls -- the 3 Mb/s streaming
    point is "acceptable" only because startup crossed 5 s, and that is
    invisible unless the 5 s line is on the chart.
    """
    pts = [r for r in rows if r.get(rate_key) and r.get(metric_key) is not None]
    pts.sort(key=lambda r: r[rate_key])
    if not pts:
        return None
    xs = [r[rate_key] for r in pts]
    ys = [r[metric_key] * scale for r in pts]

    fig, ax = plt.subplots(figsize=(9.5, 5.0))
    ax.plot(xs, ys, "o-", color=colour, lw=2.5, ms=9)
    for value, text, style in thresholds:
        ax.axhline(value, color="#8D6E63", ls=style, lw=1.6)
        ax.annotate(
            text,
            xy=(0.995, value),
            xycoords=("axes fraction", "data"),
            xytext=(0, 4),
            textcoords="offset points",
            ha="right",
            fontsize=8.5,
            color="#5D4037",
        )
    ax.set_xlabel("shaped download capacity (Mb/s, log scale)")
    ax.set_ylabel(ylabel)
    _logx(ax, xs)
    _iqb_line(ax, iqb_min, f"IQB minimum {iqb_min:g} Mb/s")
    ax.set_ylim(bottom=0)
    ax.set_title(title)
    return _save(fig, path)


def source_vs_delivered(rows, path, iqb_min, rate_key, title):
    """What the source offered against what the player chose.

    The gap between the two lines is the whole point: the content carried 2160p
    at every capacity, so the rendition actually played is the player's own ABR
    decision and not a ceiling imposed by the source or the link.
    """
    pts = [
        r
        for r in rows
        if r.get(rate_key) and r.get("resolution_p") and r.get("source_max_rendition_p")
    ]
    pts.sort(key=lambda r: r[rate_key])
    if not pts:
        return None
    xs = [r[rate_key] for r in pts]
    offered = [r["source_max_rendition_p"] for r in pts]
    chosen = [r["resolution_p"] for r in pts]

    fig, ax = plt.subplots(figsize=(9.5, 5.2))
    ax.plot(
        xs,
        offered,
        "s--",
        color=C_IQB,
        lw=2.2,
        ms=8,
        label="source offers (max rendition)",
    )
    ax.plot(xs, chosen, "o-", color=C_QOE, lw=2.5, ms=9, label="player chose")
    ax.fill_between(
        xs, chosen, offered, color="#FFE082", alpha=0.45, label="unused headroom"
    )
    ax.set_yscale("log")
    ax.set_yticks([144, 240, 360, 480, 720, 1080, 1440, 2160])
    ax.set_yticklabels(
        ["144p", "240p", "360p", "480p", "720p", "1080p", "1440p", "2160p"]
    )
    ax.set_xlabel("shaped download capacity (Mb/s, log scale)")
    ax.set_ylabel("video resolution")
    _logx(ax, xs)
    _iqb_line(ax, iqb_min, f"IQB minimum {iqb_min:g} Mb/s")
    ax.legend(loc="center left", fontsize=9)
    ax.set_title(title)
    return _save(fig, path)
