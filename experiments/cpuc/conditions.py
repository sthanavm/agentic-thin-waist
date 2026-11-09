"""The CPUC/Sigmetrics condition matrix, the reference run, and the IQB table.

Everything here is declarative on purpose. The matrix, the reference run and
the QoE thresholds are the parts of this study that must not move once data
collection starts, so they live in one module with no I/O and are covered by
tests that fail if a value changes.

Sources, so a reader can check a number rather than trust it:
  * IQB 2025 report, Fig 10 (printed p.16) -- `IQB_VIDEO_STREAMING` below.
  * Jaber's plan doc "CPUC Data Collection: Are IQB's Thresholds Realistic?"
    -- Stage 1 corners, the reference run, and the QoE labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, Optional

# ── IQB Fig 10, Video Streaming row ─────────────────────────────────────────
# Transcribed from the report, not inferred. "50-100 Mb/s" is how the report
# writes the high download threshold; it is a range in the source and is kept
# as one here rather than silently collapsed to a single number.
IQB_VIDEO_STREAMING = {
    "download_min_mbps": 25.0,
    "download_high_mbps": (50.0, 100.0),
    "upload_min_mbps": 10.0,
    "upload_high_mbps": 10.0,
    "latency_min_ms": 100.0,
    "latency_high_ms": 50.0,
    "loss_min_pct": 1.0,
    "loss_high_pct": 0.1,
}

# Kept for the conferencing half (Sthanav's scope) so the two halves read the
# same table rather than two transcriptions of it.
IQB_VIDEO_CONFERENCING = {
    "download_min_mbps": 10.0,
    "download_high_mbps": 100.0,
    "upload_min_mbps": 25.0,
    "upload_high_mbps": 100.0,
    "latency_min_ms": 50.0,
    "latency_high_ms": 20.0,
    "loss_min_pct": 0.5,
    "loss_high_pct": 0.1,
}

# ── UNRESOLVED: streaming high-RTT corner ───────────────────────────────────
# IQB Fig 10 gives 50 ms as the HIGH-quality latency threshold for video
# streaming. Jaber's plan doc Stage 1 table gives streaming RTTs as "100 and
# 20 ms" -- 20 ms is IQB's CONFERENCING high value, and the plan's conferencing
# row ("50 and 20 ms") matches IQB's conferencing row exactly. So the plan's
# streaming high RTT does not match the table it is derived from.
#
# This is an open question with Jaber and is NOT resolved here. The harness
# takes it as a parameter so that whichever way it lands, no code changes and
# the choice is recorded in every row. `STREAMING_HIGH_RTT_MS_UNRESOLVED`
# exists so a reader cannot mistake the default for a settled decision.
STREAMING_HIGH_RTT_MS_UNRESOLVED = True
STREAMING_HIGH_RTT_MS_PLAN_DOC = 20.0  # Jaber's plan doc
STREAMING_HIGH_RTT_MS_IQB_TABLE = 50.0  # IQB Fig 10

# Default follows the plan doc, because the plan doc is what the run matrix was
# sized against (72 streaming app runs). Flip with `high_rtt_ms=` at call time.
STREAMING_HIGH_RTT_MS_DEFAULT = STREAMING_HIGH_RTT_MS_PLAN_DOC

# Decision (2026-11-04): YouTube runs on AUTO quality. `force_max_quality`
# pins the ladder with setPlaybackQualityRange(level, level), which disables
# ABR -- the adaptation this study exists to observe -- and it failed to take
# effect in 6 of 12 archived attempts. On auto, YouTube's own archive tops out
# at 720p across 510 runs while the source carries 2160p, so a 720p ceiling at
# both Stage 1 corners is a FINDING about IQB's high threshold (the rate makes
# no difference to the rendition the player chooses), not a harness defect.
# Asserted by a test so it cannot drift mid-collection.
FORCE_MAX_QUALITY = False

LossMode = Literal["none", "iqb"]
RunType = Literal["app", "ndt"]

# Decision (2026-11-04): Vimeo replaces Puffer. The plan doc says the choice
# of applications is not strict, and the evidence is one-sided -- across 785
# archived records Puffer has 0 runs and has never been driven, while Vimeo has
# 340 (316 served). Vimeo is also the only app measured scaling with bandwidth
# (240p at 1 Mb/s to 1080p at 10 Mb/s), which separately satisfies the 1080p+
# source requirement that YouTube cannot meet on auto quality.
#
# CONFIRMED on vm4 (2026-11-07), because this claim is what stops YouTube's
# flat 720p line being read as a property of this host rather than of the
# player. One Vimeo run at 100 Mb/s / 20 ms -- the same condition as each
# sweep's reference run -- served 1920x1440 in 85 of 90 samples (480x360 for
# the first 5, the startup ramp), 1.38 s startup, 0 rebuffers, and
# link_interference clean (1.00 MB of browser_infra against 118.44 MB of app
# traffic).
#
# The codec matters here: Vimeo delivered that in AV1
# (av01.0.01M.08.0.111.01.01.01.0). So this host decodes AV1 well past 1080p,
# and YouTube's 720p ceiling on the same machine is a player-side decision,
# not a decoder limit. That also weakens the decode explanation offered for
# the 1080p-viewport supplementary set -- see results_1080p/README.md.
STREAMING_APPS_PLAN = ("youtube", "twitch", "vimeo")


@dataclass(frozen=True)
class Condition:
    """One network condition. Frozen: a condition is an identity, not a buffer."""

    rate_down_mbps: float
    rate_up_mbps: float
    rtt_ms: float
    loss_pct: float = 0.0
    loss_mode: LossMode = "none"
    qdisc: str = "pfifo"  # the reference implementation's default
    label: str = ""

    def slug(self) -> str:
        loss = f"{self.loss_pct:g}pct" if self.loss_pct else "noloss"
        return (
            f"{self.rate_down_mbps:g}dn_{self.rate_up_mbps:g}up_"
            f"{self.rtt_ms:g}ms_{loss}_{self.qdisc}"
        )

    def latency_for_platform(self) -> float:
        """What to hand `substrate_shape` as `latency_ms`.

        The worker applies the whole figure as netem delay on one direction, so
        the added RTT equals this number -- which is how all 785 archived runs
        were taken. It is deliberately NOT reduced by the base RTT to the
        measurement server. The plan doc asks for total RTT to equal the
        target, but the base differs per CDN and per M-Lab server, so
        subtracting it would make this data incomparable with the archive and
        would mean a different effective delay per app. The measured figures
        are recorded instead -- the calibration RTT is netem alone, and
        ndt_minrtt is the total to the server -- so the base stays recoverable.
        """
        return self.rtt_ms

    def trial_for(self, app: str) -> int:
        """A stable per-(condition, app) trial number for the run slug.

        Derived from the condition and app rather than a counter so that a
        rerun of the same cell lands on the same trial id and is obvious in the
        run directory listing.
        """
        digest = 0
        for ch in f"{self.slug()}|{app}":
            digest = (digest * 131 + ord(ch)) % 7919
        return 9000 + digest


# ── The reference run ───────────────────────────────────────────────────────
# "Relative to each app's reference run (100 Mb/s, 20 ms)" -- plan doc, QoE
# labels. Every QoE label is relative to this, so it is defined exactly once,
# frozen, and asserted by a test. Upload follows Stage 1 streaming (10 Mb/s);
# the plan doc names only the download rate and the RTT.
#
# NOTE where the open question above matters: the reference RTT is 20 ms
# whatever happens to the Stage 1 high corner. If the corner becomes 50 ms,
# the reference STOPS being one of the Stage 1 corners and becomes an extra
# condition per app -- see `reference_is_a_stage1_corner()`.
REFERENCE = Condition(
    rate_down_mbps=100.0,
    rate_up_mbps=10.0,
    rtt_ms=20.0,
    loss_pct=0.0,
    loss_mode="none",
    label="reference",
)


def reference_is_a_stage1_corner(
    high_rtt_ms: float = STREAMING_HIGH_RTT_MS_DEFAULT,
) -> bool:
    """Does the reference run coincide with a Stage 1 corner?

    True under the plan doc's 20 ms (the reference is the high/no-loss corner,
    so it costs no extra runs). False under IQB's 50 ms, which makes the
    reference a separate condition and adds 3 reps per app to the matrix.
    """
    return any(
        c.rate_down_mbps == REFERENCE.rate_down_mbps
        and c.rtt_ms == REFERENCE.rtt_ms
        and c.loss_mode == "none"
        for c in stage1_streaming_conditions(high_rtt_ms=high_rtt_ms)
    )


def stage1_streaming_conditions(
    high_rtt_ms: float = STREAMING_HIGH_RTT_MS_DEFAULT,
) -> list[Condition]:
    """Stage 1 streaming: 2 rates x 2 RTTs x 2 loss modes = 8 conditions.

    Rates and the loss pairing come from the plan doc's Stage 1 tables:
    streaming runs 25/10 and 100/10 Mb/s, and loss follows the rate corner --
    the min rate gets IQB's min loss (1%), the high rate gets IQB's high loss
    (0.1%). The plan doc flags that pairing as an assumption; it is reproduced
    here as an assumption, not as a fact.
    """
    corners = [
        # (rate_down, rate_up, iqb_loss_pct) -- loss follows the rate corner.
        (25.0, 10.0, IQB_VIDEO_STREAMING["loss_min_pct"]),
        (100.0, 10.0, IQB_VIDEO_STREAMING["loss_high_pct"]),
    ]
    rtts = [IQB_VIDEO_STREAMING["latency_min_ms"], float(high_rtt_ms)]

    out: list[Condition] = []
    for down, up, iqb_loss in corners:
        for rtt in rtts:
            out.append(Condition(down, up, rtt, 0.0, "none", label="stage1_noloss"))
            out.append(
                Condition(down, up, rtt, iqb_loss, "iqb", label="stage1_iqbloss")
            )
    return out


@dataclass(frozen=True)
class RunPlan:
    """One unit of work: an app, a condition, a repetition.

    `run_type` is "app" or "ndt" and they are SEPARATE rows, which is what
    makes the plan doc's Stage 1 total balance: 144 app runs (2 categories x 72)
    plus 48 NDT runs (8 conditions x 3 reps x 2 categories) = 192. An NDT run
    belongs to a (condition, rep), NOT to an app -- it runs once per
    repetition, not once per app -- so its `app` field is empty.
    """

    app: str
    condition: Condition
    rep: int
    run_type: RunType = "app"
    is_reference: bool = False


def stage1_streaming_plan(
    apps: tuple[str, ...] = STREAMING_APPS_PLAN,
    reps: int = 3,
    high_rtt_ms: float = STREAMING_HIGH_RTT_MS_DEFAULT,
) -> list[RunPlan]:
    """The 72-run Stage 1 streaming matrix: 2 x 2 x 2 x 3 apps x 3 reps.

    Order is NOT randomised here. The plan doc asks for randomised, interleaved
    reps; that belongs to the scheduler (which must record the seed), not to
    the matrix definition, so that this function stays a pure description of
    what has to run.
    """
    conds = stage1_streaming_conditions(high_rtt_ms=high_rtt_ms)
    plans: list[RunPlan] = []
    for app in apps:
        for cond in conds:
            for rep in range(1, reps + 1):
                plans.append(
                    RunPlan(
                        app=app,
                        condition=cond,
                        rep=rep,
                        is_reference=(
                            cond.rate_down_mbps == REFERENCE.rate_down_mbps
                            and cond.rtt_ms == REFERENCE.rtt_ms
                            and cond.loss_mode == "none"
                        ),
                    )
                )
    return plans


def stage1_streaming_ndt_plan(
    reps: int = 3,
    high_rtt_ms: float = STREAMING_HIGH_RTT_MS_DEFAULT,
) -> list[RunPlan]:
    """The 24 streaming NDT runs: 8 conditions x 3 reps, once per repetition.

    Not per app. The plan doc runs the speed test "in every condition ... once
    per repetition, separately from the app run", and that is the only reading
    under which Stage 1 totals 192.
    """
    out: list[RunPlan] = []
    for cond in stage1_streaming_conditions(high_rtt_ms=high_rtt_ms):
        for rep in range(1, reps + 1):
            out.append(
                RunPlan(
                    app="",
                    condition=cond,
                    rep=rep,
                    run_type="ndt",
                    is_reference=(
                        cond.rate_down_mbps == REFERENCE.rate_down_mbps
                        and cond.rtt_ms == REFERENCE.rtt_ms
                        and cond.loss_mode == "none"
                    ),
                )
            )
    return out


# ── The bandwidth-minimum sweep (2026-11-05) ────────────────────────────────
# The argument: IQB's streaming download minimum of 25 Mb/s is too high. The
# evidence is a link a speed test calls failing, on which the video is still
# good -- so every point needs BOTH an app run and an NDT run, and the contrast
# between them IS the finding.
#
# 25 Mb/s is IQB's minimum; everything below it is the argument. Latency is
# fixed at 20 ms and no loss is induced, so bandwidth is the only variable.
SWEEP_RATES_MBPS = (25.0, 12.5, 6.25, 3.0, 1.5, 1.0, 0.5)
SWEEP_RTT_MS = 20.0
SWEEP_UPLOAD_MBPS = 10.0
SWEEP_APP = "youtube"


def sweep_conditions(rtt_ms: float = SWEEP_RTT_MS) -> list[Condition]:
    """One condition per bandwidth point, highest first.

    `rtt_ms` is a parameter so the same bandwidth matrix can be swept at
    IQB's two streaming latencies (100 ms minimum, 50 ms high) as well as
    the 20 ms the first pass used. The RTT is part of Condition.slug(), so
    rows at different latencies cannot be mistaken for repetitions.
    """
    return [
        Condition(
            rate_down_mbps=r,
            rate_up_mbps=SWEEP_UPLOAD_MBPS,
            rtt_ms=rtt_ms,
            loss_pct=0.0,
            loss_mode="none",
            label="sweep",
        )
        for r in SWEEP_RATES_MBPS
    ]


def sweep_plan(reps: int = 1, rtt_ms: float = SWEEP_RTT_MS) -> list[RunPlan]:
    """Reference first, then each bandwidth point: one app run and one NDT run.

    The reference run (100 Mb/s, 20 ms) comes FIRST and is not optional: every
    QoE label is relative to it, so running it last would leave every row
    deferred. It is also the top of the curve the plot needs.
    """
    # The reference belongs to the latency it was measured at: every label is
    # relative to it, so grading a 100 ms run against a 20 ms reference would
    # charge the latency's effect to the capacity. REFERENCE itself stays
    # frozen at 20 ms; the other latencies get their own.
    reference = (
        REFERENCE if rtt_ms == SWEEP_RTT_MS else replace(REFERENCE, rtt_ms=rtt_ms)
    )
    plans: list[RunPlan] = []
    for rep in range(1, reps + 1):
        plans.append(RunPlan(SWEEP_APP, reference, rep, "app", is_reference=True))
        plans.append(RunPlan("", reference, rep, "ndt", is_reference=True))
        for cond in sweep_conditions(rtt_ms=rtt_ms):
            plans.append(RunPlan(SWEEP_APP, cond, rep, "app"))
            plans.append(RunPlan("", cond, rep, "ndt"))
    return plans


# ── The conferencing bandwidth sweep (Google Meet, attended) ────────────────
# Same argument as the streaming sweep, on the conferencing row of Fig 10:
# IQB's conferencing download minimum is 10 Mb/s and its UPLOAD minimum is
# 25 Mb/s, so 25 symmetric is the binding minimum for a call. 1 Mb/s is where
# we expect it to break; 6 and 3 are the argument.
#
# Symmetric, because a call is symmetric and IQB's conferencing thresholds are
# the only ones in Fig 10 where upload exceeds download.
MEET_RATES_MBPS = (25.0, 6.0, 3.0, 1.0)
MEET_EXTRA_RATES_MBPS = (12.5, 0.5)  # only if the first pass finishes early
MEET_RTT_MS = 20.0
MEET_APP = "meet"

# The conferencing reference, per the plan doc's "100 Mb/s, 20 ms", symmetric.
MEET_REFERENCE = Condition(
    rate_down_mbps=100.0,
    rate_up_mbps=100.0,
    rtt_ms=MEET_RTT_MS,
    loss_pct=0.0,
    loss_mode="none",
    label="meet_reference",
)


def meet_conditions(
    rates: tuple[float, ...] = MEET_RATES_MBPS, rtt_ms: float = MEET_RTT_MS
) -> list[Condition]:
    """One symmetric condition per bandwidth point, highest first.

    `rtt_ms` is a parameter rather than the module constant so the same
    matrix can be swept at IQB's two conferencing latencies (50 ms high,
    20 ms... see IQB_VIDEO_CONFERENCING) and at 100 ms. The RTT is part of
    Condition.slug(), so rows at different latencies can never be confused
    for repetitions of each other.
    """
    return [
        Condition(
            rate_down_mbps=r,
            rate_up_mbps=r,
            rtt_ms=rtt_ms,
            loss_pct=0.0,
            loss_mode="none",
            label="meet_sweep",
        )
        for r in rates
    ]


def meet_plan(
    rates: tuple[float, ...] = MEET_RATES_MBPS,
    reps: int = 1,
    rtt_ms: float = MEET_RTT_MS,
) -> list[RunPlan]:
    """Reference first, then each point: one Meet run and one NDT run.

    The reference is not optional: both conferencing High criteria are stated
    as a fraction of it (resolution AND fps at least 90% of reference), so
    without it every row defers.
    """
    # The reference belongs to the latency it was measured at: High is
    # defined as a fraction of reference resolution and fps, and a 100 ms
    # call graded against a 20 ms reference would attribute the latency's
    # effect to the capacity.
    reference = (
        MEET_REFERENCE
        if rtt_ms == MEET_RTT_MS
        else replace(MEET_REFERENCE, rtt_ms=rtt_ms)
    )
    plans: list[RunPlan] = []
    for rep in range(1, reps + 1):
        plans.append(RunPlan(MEET_APP, reference, rep, "app", is_reference=True))
        plans.append(RunPlan("", reference, rep, "ndt", is_reference=True))
        for cond in meet_conditions(rates, rtt_ms=rtt_ms):
            plans.append(RunPlan(MEET_APP, cond, rep, "app"))
            plans.append(RunPlan("", cond, rep, "ndt"))
    return plans


# ── Teams: Stage 1 + Stage 2, deduped, three RTTs (Sthanav's build) ─────────
# The plan doc's full conferencing design (not the simplified single-sweep Meet
# used above): Stage 1 runs IQB's own min/high corners with loss, Stage 2 sweeps
# below the minimum with no induced loss. RTTs are IQB's two conferencing
# latencies (50 ms min, 20 ms high) plus 100 ms, added on top of the plan doc's
# original two-RTT Stage 1 table -- same three-RTT treatment meet_plan() already
# gives Meet via its `rtt_ms` parameter, just run at all three at once here
# instead of one invocation per RTT.
TEAMS_APP = "teams"
TEAMS_RTTS_MS = (20.0, 50.0, 100.0)
# Plan doc Stage 2 conferencing rates (4, 6, 8 Mb/s) plus 25 Mb/s as the
# Stage 2 reference point, symmetric, highest first.
TEAMS_STAGE2_RATES_MBPS = (25.0, 8.0, 6.0, 4.0)


def stage1_conferencing_conditions(
    rtts: tuple[float, ...] = TEAMS_RTTS_MS,
) -> list[Condition]:
    """Stage 1 conferencing: 2 rates x len(rtts) RTTs x 2 loss modes.

    Corners and the loss pairing are IQB_VIDEO_CONFERENCING's two rows --
    25/25 Mb/s at the min-corner loss (0.5%), 100/100 Mb/s at the high-corner
    loss (0.1%) -- the same assumption `stage1_streaming_conditions` makes for
    streaming: loss follows the rate corner, flagged there and here as an
    assumption, not a fact from the plan doc.
    """
    corners = [
        (25.0, 25.0, IQB_VIDEO_CONFERENCING["loss_min_pct"]),
        (100.0, 100.0, IQB_VIDEO_CONFERENCING["loss_high_pct"]),
    ]
    out: list[Condition] = []
    for down, up, iqb_loss in corners:
        for rtt in rtts:
            out.append(Condition(down, up, rtt, 0.0, "none", label="stage1_noloss"))
            out.append(
                Condition(down, up, rtt, iqb_loss, "iqb", label="stage1_iqbloss")
            )
    return out


def stage2_conferencing_conditions(
    rates: tuple[float, ...] = TEAMS_STAGE2_RATES_MBPS,
    rtts: tuple[float, ...] = TEAMS_RTTS_MS,
) -> list[Condition]:
    """Stage 2 conferencing: below-minimum rates x RTTs, no induced loss.

    Generalizes `meet_conditions` (one RTT per call) to the full RTT set this
    build sweeps, so Stage 2 can cover all three latencies from one call.
    """
    return [
        Condition(r, r, rtt, 0.0, "none", label="stage2_sweep")
        for rtt in rtts
        for r in rates
    ]


def _is_teams_reference(cond: Condition) -> bool:
    """Does this condition coincide with the plan doc's reference (100/100,
    no loss)? The reference is not a separate run -- see `teams_plan`."""
    return (
        cond.rate_down_mbps == MEET_REFERENCE.rate_down_mbps
        and cond.rate_up_mbps == MEET_REFERENCE.rate_up_mbps
        and cond.loss_mode == "none"
    )


def teams_plan(
    rates: tuple[float, ...] = TEAMS_STAGE2_RATES_MBPS,
    rtts: tuple[float, ...] = TEAMS_RTTS_MS,
    reps: int = 1,
) -> list[RunPlan]:
    """Stage 1 + Stage 2 conferencing for Teams, deduped, one Teams run and one
    NDT run per condition and rep.

    DEDUPE: Stage 1's 100/100 Mb/s no-loss corner is the same network state as
    the plan doc's reference run (100 Mb/s, each RTT) at every RTT in `rtts`,
    and Stage 1's 25/25 Mb/s no-loss corner is the same state as Stage 2's
    25 Mb/s reference point at every RTT. Both are written once: a condition is
    deduped by `slug()`, which already encodes rate/RTT/loss, so two Condition
    objects describing the same physical link collapse to one entry regardless
    of which stage produced them. This means NO separate reference run is
    scheduled -- the 100/100 Mb/s no-loss conditions are simply flagged
    `is_reference=True` in place, which is what keeps the total at 21 unique
    conditions (not 21 + 3 extra reference runs).
    """
    seen: dict[str, Condition] = {}
    for cond in (
        *stage1_conferencing_conditions(rtts),
        *stage2_conferencing_conditions(rates, rtts),
    ):
        seen.setdefault(cond.slug(), cond)
    conditions = sorted(
        seen.values(), key=lambda c: (-c.rate_down_mbps, c.rtt_ms, c.loss_pct)
    )

    plans: list[RunPlan] = []
    for rep in range(1, reps + 1):
        for cond in conditions:
            is_ref = _is_teams_reference(cond)
            plans.append(RunPlan(TEAMS_APP, cond, rep, "app", is_reference=is_ref))
            plans.append(RunPlan("", cond, rep, "ndt", is_reference=is_ref))
    return plans


# ── Vimeo streaming sweep, same shape as teams_plan() ───────────────────────
# Decision (2026-11-08): Vimeo, not Puffer -- STREAMING_APPS_PLAN above already
# records why (0 archived Puffer runs vs 340 Vimeo, and Vimeo is the only app
# observed scaling resolution with bandwidth). This combines Stage 1's IQB
# corners (with induced loss) and the below-minimum sweep (`sweep_conditions`,
# no induced loss) into one deduped matrix, at the plan doc's streaming RTTs:
# 100 ms (IQB's minimum) and 20 ms (IQB's high per the plan doc; see
# STREAMING_HIGH_RTT_MS_UNRESOLVED above -- the IQB table itself says 50 ms,
# which is not included here for the same reason teams_plan's docstring does
# not resolve the parallel conferencing question on its own).
VIMEO_APP = "vimeo"
VIMEO_RTTS_MS = (STREAMING_HIGH_RTT_MS_DEFAULT, IQB_VIDEO_STREAMING["latency_min_ms"])


def stage2_streaming_conditions(
    rates: tuple[float, ...] = SWEEP_RATES_MBPS,
    rtts: tuple[float, ...] = VIMEO_RTTS_MS,
) -> list[Condition]:
    """`sweep_conditions()` generalised to more than one RTT, no induced loss."""
    return [
        Condition(r, SWEEP_UPLOAD_MBPS, rtt, 0.0, "none", label="stage2_sweep")
        for rtt in rtts
        for r in rates
    ]


def _is_vimeo_reference(cond: Condition) -> bool:
    return (
        cond.rate_down_mbps == REFERENCE.rate_down_mbps
        and cond.rtt_ms == REFERENCE.rtt_ms
        and cond.loss_mode == "none"
    )


def vimeo_plan(
    rates: tuple[float, ...] = SWEEP_RATES_MBPS,
    rtts: tuple[float, ...] = VIMEO_RTTS_MS,
    reps: int = 1,
    high_rtt_ms: float = STREAMING_HIGH_RTT_MS_DEFAULT,
) -> list[RunPlan]:
    """Stage 1 + the below-minimum sweep for Vimeo, deduped, one Vimeo run and
    one NDT run per condition and rep.

    DEDUPE: Stage 1's 100/10 Mb/s no-loss corner at 20 ms is the same network
    state as REFERENCE, and Stage 1's 25/10 Mb/s no-loss corner is the same
    state as the sweep's top point at the same RTT. A condition is deduped by
    `slug()`, which already encodes rate/RTT/loss, so no separate reference
    run is scheduled -- the 100/10 Mb/s no-loss conditions are simply flagged
    `is_reference=True` in place. Same approach as `teams_plan()`.
    """
    seen: dict[str, Condition] = {}
    for cond in (
        *stage1_streaming_conditions(high_rtt_ms=high_rtt_ms),
        *stage2_streaming_conditions(rates, rtts),
    ):
        seen.setdefault(cond.slug(), cond)
    conditions = sorted(
        seen.values(), key=lambda c: (-c.rate_down_mbps, c.rtt_ms, c.loss_pct)
    )

    plans: list[RunPlan] = []
    for rep in range(1, reps + 1):
        for cond in conditions:
            is_ref = _is_vimeo_reference(cond)
            plans.append(RunPlan(VIMEO_APP, cond, rep, "app", is_reference=is_ref))
            plans.append(RunPlan("", cond, rep, "ndt", is_reference=is_ref))
    return plans
