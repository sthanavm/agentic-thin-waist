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


LAT_KEY = "rtt_configured_ms"

# One colour per configured latency, dark to light. Latency is an ordered
# variable, so the ramp carries the order; separate hues would not.
LAT_COLOURS = {20.0: "#0D47A1", 50.0: "#1E88E5", 100.0: "#64B5F6"}
LAT_MARKERS = {20.0: "o", 50.0: "s", 100.0: "^"}


def latencies(rows) -> list[float]:
    """Configured latencies present, ascending. Empty when none are tagged."""
    return sorted({r[LAT_KEY] for r in rows if r.get(LAT_KEY) is not None})


def _lat_style(lat, i=0):
    return (
        LAT_COLOURS.get(lat, f"C{i}"),
        LAT_MARKERS.get(lat, "o"),
    )


def _lat_label(rows, lat) -> str:
    """ "50 ms set (52.8 ms measured)" - both numbers in the legend."""
    meas = [
        r["ndt_minrtt_ms"]
        for r in rows
        if r.get(LAT_KEY) == lat and r.get("ndt_minrtt_ms")
    ]
    if not meas:
        return f"{lat:g} ms set"
    return f"{lat:g} ms set ({sum(meas) / len(meas):.1f} ms measured)"


def _by_lat(rows):
    """[(latency, rows)] ascending; [(None, rows)] when nothing is tagged."""
    lats = latencies(rows)
    if not lats:
        return [(None, rows)]
    return [(lat, [r for r in rows if r.get(LAT_KEY) == lat]) for lat in lats]


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


def _contrast_panel(ax, pts, iqb_min, rate_key, iqb_key, show_legend=True):
    """One latency's worth of the contrast chart, drawn onto `ax`."""
    pts = sorted(pts, key=lambda r: r[rate_key])
    xs = [r[rate_key] for r in pts]
    qoe = [_tier(r.get("qoe_label"), QOE_TIERS) for r in pts]
    iqb = [_tier(r.get(iqb_key), IQB_TIERS) for r in pts]
    if not any(v is not None for v in qoe):
        return False
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
            # step up in height; and a callout centred on a point that sits
            # under the IQB line runs straight through it, so that one also
            # shifts left. Shifting left alone pushed this label onto its
            # neighbour, which is what the stagger is for.
            #
            # Three heights rather than two: when the chart is facetted by
            # latency each panel is ~6 inches instead of 9.5, and with two
            # heights the run of three ringed points at 6.25, 12.5 and 25
            # put two labels back on the same line and printed them over
            # each other.
            near = abs(math.log10(x) - math.log10(iqb_min)) < 0.08
            dy = (26, 52, 78)[ring % 3]
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
    note_i = 0
    for x, q, r in zip(xs, qoe, pts):
        if q is not None:
            continue
        raw = str(r.get("qoe_label", "")).strip().lower()
        note = {
            "deferred": "reference run\n(the baseline,\nnot graded)",
            "not collected": "QoE not\ncollected",
        }.get(raw, raw or "no QoE label")
        ax.axvline(x, color="#9E9E9E", ls=":", lw=1.2, zorder=1)
        # Adjacent ungraded points (0.5 and 1 Mb/s both fail at high latency)
        # sit close enough on a log axis for their notes to collide, so they
        # alternate height the way the orange callouts do.
        ny = 0.62 if note_i % 2 == 0 else 0.44
        note_i += 1
        ax.annotate(
            note,
            xy=(x, ny),
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
    # Headroom above the top tier for the stacked callouts: three of them
    # reach 78pt above y=2, which ran into the panel title at 2.75.
    ax.set_ylim(-0.45, 3.25)
    ax.set_xlabel("shaped download capacity (Mb/s, log scale)")
    ax.set_ylabel("verdict (higher is better)")
    _logx(ax, xs)
    _iqb_line(ax, iqb_min, f"IQB minimum {iqb_min:g} Mb/s")
    if show_legend:
        ax.legend(loc="lower right", fontsize=9)
    return True


def contrast(rows, path, iqb_min, rate_key, iqb_key, title):
    """IQB's verdict against the measured QoE label, per bandwidth point.

    This is the argument in one picture: wherever the red line sits at "fails
    minimum" while the blue line sits at "high", a speed test condemns a link
    the application is serving perfectly. Those points are ringed.

    With more than one latency the chart becomes one panel per latency on a
    shared axis rather than six overlapping lines. Each panel carries the
    same two series, so the comparison across latencies is read vertically:
    the capacity at which the blue line steps down is the whole question.
    """
    pts = [r for r in rows if r.get(rate_key) and r.get("qoe_label")]
    if not pts:
        return None
    groups = _by_lat(pts)
    n = len(groups)
    fig, axes = plt.subplots(
        1, n, figsize=(9.5 if n == 1 else 6.0 * n, 5.4), sharey=True, squeeze=False
    )
    drew = False
    for i, ((lat, grp), ax) in enumerate(zip(groups, axes[0])):
        ok = _contrast_panel(
            ax, grp, iqb_min, rate_key, iqb_key, show_legend=(i == n - 1)
        )
        drew = drew or ok
        # With one latency the suptitle already names it; a panel title
        # would just repeat it.
        if lat is not None and n > 1:
            ax.set_title(_lat_label(pts, lat), fontsize=10)
        if i:
            ax.set_ylabel("")
    if not drew:
        plt.close(fig)
        return None
    fig.suptitle(title, y=0.99)
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
    xs = sorted({r[rate_key] for r in pts})

    fig, ax = plt.subplots(figsize=(9.5, 5.4))
    ax.plot(xs, xs, color=C_CAP, lw=1.8, ls="--", label="capacity offered by the link")
    groups = _by_lat(pts)
    multi = len(groups) > 1
    for i, (lat, grp) in enumerate(groups):
        grp = sorted(grp, key=lambda r: r[rate_key])
        gx = [r[rate_key] for r in grp]
        c, m = _lat_style(lat, i) if lat is not None else (C_APP, "o")
        suffix = f" at {lat:g} ms" if multi else ""
        ax.plot(
            gx,
            [r[demand_key] for r in grp],
            marker=m,
            ls="-",
            color=c,
            lw=2.5,
            ms=9,
            label=f"{app_label} mean demand{suffix}",
        )
        if peak_key:
            peaks = [r.get(peak_key) for r in grp]
            if any(pk for pk in peaks):
                ax.plot(
                    gx,
                    peaks,
                    marker="^",
                    ls=":",
                    color=("#00897B" if not multi else c),
                    alpha=(1.0 if not multi else 0.55),
                    lw=2,
                    ms=8,
                    label=f"{app_label} peak demand{suffix}",
                )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{x:g}" for x in xs])
    ax.grid(alpha=0.25, which="both")
    ax.set_xlabel("shaped capacity (Mb/s, log scale)")
    ax.set_ylabel("throughput (Mb/s, log scale)")
    _iqb_line(ax, iqb_min, f"IQB minimum {iqb_min:g} Mb/s")
    if multi:
        # The mean is taken over the whole capture window, and an adaptive
        # player fills its buffer and then goes quiet, so the window includes
        # an idle tail that ranges from 6 s to 72 s across these runs. That
        # makes the mean sound for "how much did a session need" -- the claim
        # against the IQB minimum -- and unsound for comparing one latency
        # against another, which is exactly what three curves invite. Say so
        # on the chart rather than only in the write-up.
        ax.annotate(
            "Mean is over the whole window, which includes the idle time after\n"
            "the player fills its buffer (6-72 s here). Read each curve against\n"
            "the IQB line, not against the other curves.",
            xy=(0.5, 0.02),
            xycoords="axes fraction",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#424242",
            bbox=dict(boxstyle="round,pad=0.35", fc="#FFFDE7", ec="#BDBDBD"),
        )
    ax.legend(loc="upper left", fontsize=8 if multi else 9)
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
    xs = sorted({r[rate_key] for r in pts})

    fig, ax = plt.subplots(figsize=(9.5, 5.0))
    groups = _by_lat(pts)
    for i, (lat, grp) in enumerate(groups):
        grp = sorted(grp, key=lambda r: r[rate_key])
        gx = [r[rate_key] for r in grp]
        gy = [r[metric_key] * scale for r in grp]
        if lat is None:
            ax.plot(gx, gy, "o-", color=colour, lw=2.5, ms=9)
        else:
            c, m = _lat_style(lat, i)
            ax.plot(
                gx,
                gy,
                marker=m,
                ls="-",
                color=c,
                lw=2.5,
                ms=9,
                label=_lat_label(pts, lat),
            )
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
    if len(groups) > 1:
        ax.legend(fontsize=9, title="configured RTT")
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
    xs = sorted({r[rate_key] for r in pts})
    # The ladder is a property of the video, so it is one line whatever the
    # latency; only the chosen rendition gets a series per latency.
    offered = [
        max(r["source_max_rendition_p"] for r in pts if r[rate_key] == x) for x in xs
    ]

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
    groups = _by_lat(pts)
    multi = len(groups) > 1
    for i, (lat, grp) in enumerate(groups):
        grp = sorted(grp, key=lambda r: r[rate_key])
        gx = [r[rate_key] for r in grp]
        gy = [r["resolution_p"] for r in grp]
        if lat is None:
            ax.plot(gx, gy, "o-", color=C_QOE, lw=2.5, ms=9, label="player chose")
        else:
            c, m = _lat_style(lat, i)
            ax.plot(
                gx,
                gy,
                marker=m,
                ls="-",
                color=c,
                lw=2.5,
                ms=9,
                label=f"player chose, {_lat_label(pts, lat)}",
            )
    # Shade against the latency whose curve sits lowest at each capacity, so
    # the band is the headroom the player left unused in the worst case
    # rather than an average across latencies that no single run produced.
    worst = [min(r["resolution_p"] for r in pts if r[rate_key] == x) for x in xs]
    ax.fill_between(
        xs,
        worst,
        offered,
        color="#FFE082",
        alpha=0.45,
        label="unused headroom" + (" (worst latency)" if multi else ""),
        zorder=0,
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
    ax.legend(loc="center left", fontsize=8 if len(groups) > 1 else 9)
    ax.set_title(title)
    return _save(fig, path)
