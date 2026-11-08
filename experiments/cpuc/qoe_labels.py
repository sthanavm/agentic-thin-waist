"""Streaming QoE metrics and the plan doc's QoE labels.

The labels are transcribed from the plan doc and are PRE-REGISTERED: they are
fixed before any data exists and must not be adjusted after seeing results.
`qoe_labels.THRESHOLD_FINGERPRINT` is written into every row so that a later
change is detectable in the data rather than only in git history.

Plan doc, QoE labels (relative to each app's reference run, 100 Mb/s / 20 ms):

  High       resolution at least 90% of reference, no rebuffering,
             startup under 5 s
  Unusable   rebuffer over 5%, startup over 10 s, or no playback
  Acceptable everything between

Three metrics only -- startup delay, resolution, rebuffer ratio -- per the
plan doc's logging section. Bitrate is deliberately NOT one of them, which is
why Twitch is usable here despite appending media inside a Worker where a
main-world hook cannot observe byte counts (shared/qoe.py records that as
`unavailable` with a basis rather than a fabricated 0.0).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

# "deferred" is not a failure. Every label is relative to the app's reference
# run (100 Mb/s, 20 ms), and the instructed Stage 1 order runs the 100 ms
# conditions FIRST -- the reference is a 20 ms condition, so it is one of the
# last to run. A row measured before its reference exists therefore cannot be
# labelled at write time, which is where the plan doc puts labelling anyway
# ("confirm with Ray before analysis"). The metrics are recorded and the label
# is assigned afterwards from the CSV; the row is still valid data.
QoeLabel = Literal["high", "acceptable", "unusable", "invalid", "deferred"]

# ── pre-registered thresholds ───────────────────────────────────────────────
STREAMING_HIGH_RESOLUTION_FRACTION_OF_REF = 0.90
STREAMING_HIGH_STARTUP_MAX_S = 5.0
STREAMING_UNUSABLE_STARTUP_MIN_S = 10.0
STREAMING_UNUSABLE_REBUFFER_RATIO = 0.05

CONFERENCING_HIGH_FRACTION_OF_REF = 0.90  # resolution AND fps
CONFERENCING_HIGH_FREEZE_MAX_RATIO = 0.01
CONFERENCING_UNUSABLE_FPS_FRACTION_OF_REF = 0.50
CONFERENCING_UNUSABLE_FREEZE_RATIO = 0.10


def _fingerprint() -> str:
    payload = json.dumps(
        {
            "streaming": {
                "high_res_frac": STREAMING_HIGH_RESOLUTION_FRACTION_OF_REF,
                "high_startup_s": STREAMING_HIGH_STARTUP_MAX_S,
                "unusable_startup_s": STREAMING_UNUSABLE_STARTUP_MIN_S,
                "unusable_rebuffer_ratio": STREAMING_UNUSABLE_REBUFFER_RATIO,
            },
            "conferencing": {
                "high_frac": CONFERENCING_HIGH_FRACTION_OF_REF,
                "high_freeze": CONFERENCING_HIGH_FREEZE_MAX_RATIO,
                "unusable_fps_frac": CONFERENCING_UNUSABLE_FPS_FRACTION_OF_REF,
                "unusable_freeze": CONFERENCING_UNUSABLE_FREEZE_RATIO,
            },
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


THRESHOLD_FINGERPRINT = _fingerprint()


@dataclass
class StreamingMetrics:
    """The three metrics the plan doc requires, plus what they were derived from."""

    startup_delay_s: Optional[float] = None
    resolution_p: Optional[int] = None
    rebuffer_ratio: Optional[float] = None
    rebuffer_events: Optional[int] = None
    rebuffer_duration_ms: Optional[float] = None
    session_seconds: Optional[float] = None
    played: bool = False
    startup_basis: str = ""
    rebuffer_basis: str = ""
    resolutions_observed: tuple[int, ...] = ()
    notes: str = ""

    def as_row(self, prefix: str = "qoe_") -> dict:
        return {
            f"{prefix}startup_delay_s": self.startup_delay_s,
            f"{prefix}resolution_p": self.resolution_p,
            f"{prefix}rebuffer_ratio": self.rebuffer_ratio,
            f"{prefix}rebuffer_events": self.rebuffer_events,
            f"{prefix}rebuffer_duration_ms": self.rebuffer_duration_ms,
            f"{prefix}session_seconds": self.session_seconds,
            f"{prefix}played": self.played,
            f"{prefix}resolutions_observed": ",".join(
                str(r) for r in self.resolutions_observed
            ),
            f"{prefix}startup_basis": self.startup_basis,
            f"{prefix}rebuffer_basis": self.rebuffer_basis,
            f"{prefix}notes": self.notes,
        }


def extract_streaming_metrics(player_qoe: dict[str, Any]) -> StreamingMetrics:
    """Read the three metrics out of shared/qoe.py's output for one app.

    Reads the `uniform` block (schema `uniform_qoe/1`) for startup and
    rebuffering, because those are the definitions that are the same across
    apps, and the top-level `video_resolution_p` for resolution. A missing
    value stays None: shared/qoe.py emits null for signals a player genuinely
    does not expose, and turning that into 0 would invent a measurement.
    """
    if not isinstance(player_qoe, dict):
        return StreamingMetrics(notes="no player_qoe for this app")

    uni = player_qoe.get("uniform") or {}
    m = StreamingMetrics()

    su_ms = uni.get("startup_delay_ms")
    if isinstance(su_ms, (int, float)):
        m.startup_delay_s = round(float(su_ms) / 1000.0, 3)
    m.startup_basis = str(uni.get("startup_basis") or "")

    res = player_qoe.get("video_resolution_p")
    if isinstance(res, (int, float)) and res > 0:
        m.resolution_p = int(res)
    obs = player_qoe.get("resolutions_observed") or []
    m.resolutions_observed = tuple(int(x) for x in obs if isinstance(x, (int, float)))

    m.rebuffer_events = (
        int(uni["rebuffer_events"])
        if isinstance(uni.get("rebuffer_events"), (int, float))
        else None
    )
    m.rebuffer_duration_ms = (
        float(uni["rebuffer_duration_ms"])
        if isinstance(uni.get("rebuffer_duration_ms"), (int, float))
        else None
    )
    m.rebuffer_basis = str(uni.get("rebuffer_basis") or "")

    sess = player_qoe.get("session_seconds") or uni.get("session_seconds")
    if isinstance(sess, (int, float)) and sess > 0:
        m.session_seconds = float(sess)

    # Rebuffer RATIO, which is what the label thresholds are stated in. The
    # denominator is the measured session, not the nominal 60 s window: a run
    # that produced 40 s of session must not be scored as though it had 60.
    if m.rebuffer_duration_ms is not None and m.session_seconds:
        m.rebuffer_ratio = round(
            (m.rebuffer_duration_ms / 1000.0) / m.session_seconds, 5
        )

    # "no playback" is the Unusable trigger, so it needs a positive definition.
    m.played = bool(
        player_qoe.get("player_qoe_available")
        and (m.resolution_p or m.resolutions_observed)
        and (m.session_seconds or 0) > 0
    )
    return m


def label_streaming(
    metrics: StreamingMetrics,
    reference_resolution_p: Optional[int],
) -> tuple[QoeLabel, str]:
    """Apply the plan doc's streaming labels. Returns (label, reason).

    Unusable is tested FIRST. A run with 100% of the reference resolution and a
    12 s startup is Unusable, not High; testing High first would mislabel it.
    """
    # Unusable
    if not metrics.played:
        return "unusable", "no playback"
    if (
        metrics.rebuffer_ratio is not None
        and metrics.rebuffer_ratio > STREAMING_UNUSABLE_REBUFFER_RATIO
    ):
        return (
            "unusable",
            f"rebuffer ratio {metrics.rebuffer_ratio:.4f} > "
            f"{STREAMING_UNUSABLE_REBUFFER_RATIO}",
        )
    if (
        metrics.startup_delay_s is not None
        and metrics.startup_delay_s > STREAMING_UNUSABLE_STARTUP_MIN_S
    ):
        return (
            "unusable",
            f"startup {metrics.startup_delay_s:.2f} s > "
            f"{STREAMING_UNUSABLE_STARTUP_MIN_S} s",
        )

    # High requires all three, and cannot be decided without the pieces.
    missing = [
        n
        for n, v in (
            ("startup_delay_s", metrics.startup_delay_s),
            ("resolution_p", metrics.resolution_p),
            ("rebuffer_ratio", metrics.rebuffer_ratio),
        )
        if v is None
    ]
    if missing:
        return "invalid", f"cannot label: missing {', '.join(missing)}"
    if not reference_resolution_p:
        return (
            "deferred",
            "no reference run for this app yet; label assigned in analysis",
        )

    res_ok = metrics.resolution_p >= (
        STREAMING_HIGH_RESOLUTION_FRACTION_OF_REF * reference_resolution_p
    )
    no_rebuffer = (metrics.rebuffer_events or 0) == 0 and (
        metrics.rebuffer_duration_ms or 0.0
    ) == 0.0
    startup_ok = metrics.startup_delay_s < STREAMING_HIGH_STARTUP_MAX_S

    if res_ok and no_rebuffer and startup_ok:
        return (
            "high",
            f"resolution {metrics.resolution_p}p >= 90% of "
            f"{reference_resolution_p}p, no rebuffering, startup "
            f"{metrics.startup_delay_s:.2f} s < {STREAMING_HIGH_STARTUP_MAX_S} s",
        )

    unmet = []
    if not res_ok:
        unmet.append(
            f"resolution {metrics.resolution_p}p < 90% of {reference_resolution_p}p"
        )
    if not no_rebuffer:
        unmet.append(f"{metrics.rebuffer_events} rebuffer event(s)")
    if not startup_ok:
        unmet.append(f"startup {metrics.startup_delay_s:.2f} s >= 5 s")
    return "acceptable", "; ".join(unmet)


@dataclass
class ConferencingMetrics:
    """The signals the plan doc's conferencing labels are stated in.

    Read from shared/qoe.py's WebRTC path, which derives freezes from the
    getStats freeze counters ("WebRTC freeze count / freeze duration") rather
    than from a media clock -- a conferencing stream has no media clock to
    stall, so a <video>-style rebuffer rule would score every call as one
    continuous stall.
    """

    resolution_p: Optional[int] = None
    fps: Optional[float] = None
    freeze_count: Optional[int] = None
    freeze_duration_ms: Optional[float] = None
    freeze_ratio: Optional[float] = None
    mean_jitter_ms: Optional[float] = None
    session_seconds: Optional[float] = None
    connected: bool = False
    notes: str = ""

    def as_row(self, prefix: str = "qoe_") -> dict:
        return {
            f"{prefix}resolution_p": self.resolution_p,
            f"{prefix}fps": self.fps,
            f"{prefix}freeze_count": self.freeze_count,
            f"{prefix}freeze_duration_ms": self.freeze_duration_ms,
            f"{prefix}freeze_ratio": self.freeze_ratio,
            f"{prefix}mean_jitter_ms": self.mean_jitter_ms,
            f"{prefix}session_seconds": self.session_seconds,
            f"{prefix}connected": self.connected,
            f"{prefix}notes": self.notes,
        }


def extract_conferencing_metrics(player_qoe: dict[str, Any]) -> ConferencingMetrics:
    """Pull resolution, fps, freezes and jitter out of one app's WebRTC QoE."""
    if not isinstance(player_qoe, dict):
        return ConferencingMetrics(notes="no player_qoe for this app")
    m = ConferencingMetrics()

    res = player_qoe.get("video_resolution_p")
    if isinstance(res, (int, float)) and res > 0:
        m.resolution_p = int(res)
    fps = player_qoe.get("frame_rate_fps")
    if isinstance(fps, (int, float)) and fps > 0:
        m.fps = round(float(fps), 2)

    # shared/qoe.py reports WebRTC freezes through the rebuffer_* fields.
    fc = player_qoe.get("rebuffer_events")
    if isinstance(fc, (int, float)):
        m.freeze_count = int(fc)
    fd = player_qoe.get("rebuffer_duration_ms")
    if isinstance(fd, (int, float)):
        m.freeze_duration_ms = float(fd)

    j = player_qoe.get("mean_jitter_secs")
    if isinstance(j, (int, float)):
        m.mean_jitter_ms = round(float(j) * 1000.0, 2)

    sess = player_qoe.get("session_seconds")
    if isinstance(sess, (int, float)) and sess > 0:
        m.session_seconds = float(sess)

    # Freeze RATIO, which is the unit the labels are stated in ("freezes under
    # 1%"). Denominator is the measured session, not the nominal window.
    if m.freeze_duration_ms is not None and m.session_seconds:
        m.freeze_ratio = round((m.freeze_duration_ms / 1000.0) / m.session_seconds, 5)

    # "call drops" is an Unusable trigger, so connectedness needs a positive
    # definition rather than being inferred from a missing number.
    m.connected = bool(
        player_qoe.get("player_qoe_available")
        and m.resolution_p
        and (m.session_seconds or 0) > 0
    )
    return m


def label_conferencing(
    metrics: ConferencingMetrics,
    reference_resolution_p: Optional[int],
    reference_fps: Optional[float],
) -> tuple[QoeLabel, str]:
    """The plan doc's conferencing labels. Returns (label, reason).

    High       resolution and fps at least 90% of reference, freezes under 1%
    Unusable   fps under 50% of reference, freezes over 10%, or the call drops
    Acceptable everything between

    Unusable is tested first, for the same reason as streaming: a call holding
    reference resolution while freezing 20% of the time is Unusable, and
    testing High first would mislabel it.
    """
    if not metrics.connected:
        return "unusable", "call did not connect or dropped"
    if (
        metrics.freeze_ratio is not None
        and metrics.freeze_ratio > CONFERENCING_UNUSABLE_FREEZE_RATIO
    ):
        return (
            "unusable",
            f"freezes {metrics.freeze_ratio:.3%} > "
            f"{CONFERENCING_UNUSABLE_FREEZE_RATIO:.0%}",
        )
    if (
        metrics.fps is not None
        and reference_fps
        and metrics.fps < CONFERENCING_UNUSABLE_FPS_FRACTION_OF_REF * reference_fps
    ):
        return (
            "unusable",
            f"fps {metrics.fps:.1f} < 50% of reference {reference_fps:.1f}",
        )

    missing = [
        n
        for n, v in (
            ("resolution_p", metrics.resolution_p),
            ("fps", metrics.fps),
            ("freeze_ratio", metrics.freeze_ratio),
        )
        if v is None
    ]
    if missing:
        return "invalid", f"cannot label: missing {', '.join(missing)}"
    if not reference_resolution_p or not reference_fps:
        return (
            "deferred",
            "no reference run for this app yet; label assigned in analysis",
        )

    res_ok = metrics.resolution_p >= (
        CONFERENCING_HIGH_FRACTION_OF_REF * reference_resolution_p
    )
    fps_ok = metrics.fps >= CONFERENCING_HIGH_FRACTION_OF_REF * reference_fps
    freeze_ok = metrics.freeze_ratio < CONFERENCING_HIGH_FREEZE_MAX_RATIO

    if res_ok and fps_ok and freeze_ok:
        return (
            "high",
            f"resolution {metrics.resolution_p}p and fps {metrics.fps:.1f} both "
            f">= 90% of reference ({reference_resolution_p}p, "
            f"{reference_fps:.1f}), freezes {metrics.freeze_ratio:.3%} < 1%",
        )

    unmet = []
    if not res_ok:
        unmet.append(
            f"resolution {metrics.resolution_p}p < 90% of {reference_resolution_p}p"
        )
    if not fps_ok:
        unmet.append(f"fps {metrics.fps:.1f} < 90% of {reference_fps:.1f}")
    if not freeze_ok:
        unmet.append(f"freezes {metrics.freeze_ratio:.3%} >= 1%")
    return "acceptable", "; ".join(unmet)
