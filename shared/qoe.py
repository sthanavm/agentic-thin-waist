"""Reduce per-second player samples into real, player-side QoE metrics.

Input is the per-sample JSONL a collector writes for one app (one JSON object
per line, ``{"timestamp": <epoch>, "url": ..., "kind": ..., "stats": {...}}``).
Output is a dict shaped like ``QoEMetrics`` (see ``shared/models/README.md``)
plus timelines the plots consume.

Guiding rule: **honest nulls**. Where a signal is genuinely absent — no vendor
stats API, no byte counters, no remote peer — the field is ``None``. Never a
placeholder, never a stand-in from a different quantity.

Two mislabels this module exists to correct
-------------------------------------------
1. *Bitrate.* YouTube's ``getStatsForNerds`` reports ``bandwidth_kbps``, which is
   the player's estimate of available **connection speed**, not the bitrate of
   the media being played. Reporting it as ``mean_bitrate_mbps`` (as the older
   ``scripts/analyze_stats.py`` does) overstates the video bitrate by an order of
   magnitude — the captured samples show ``76764 Kbps`` on a 3 Mbps shaped link.
   It is surfaced here as ``connection_speed_estimate_mbps`` and never as bitrate.
2. *Rebuffering.* A stall is **not** "the network went idle" and **not** "the
   buffer hit zero". A video player bursts to fill its buffer then coasts; idle
   with a full buffer is healthy. A real rebuffer is **playback frozen**:
   ``current_time_secs`` not advancing while the player is not paused and not
   ended. That is what this module measures.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Optional

try:  # package import
    from shared import apps as _apps
except ImportError:  # pragma: no cover - standalone/script use
    try:
        from . import apps as _apps  # type: ignore[no-redef]
    except ImportError:
        import apps as _apps  # type: ignore[no-redef]


# A media clock advancing by less than this fraction of wall-clock time, while
# the player is trying to play, counts as frozen. Playback can legitimately jitter
# by a few percent between samples, so the bar is deliberately low.
_ADVANCE_RATIO = 0.20
# Below this many seconds of media progress an interval is treated as no progress
# at all, regardless of ratio (guards against tiny float noise).
_ADVANCE_FLOOR_S = 0.02
# Buffer at or under this many seconds corroborates a genuine rebuffer.
_EMPTY_BUFFER_S = 0.5
# YouTube player state 3 == BUFFERING (direct vendor rebuffer signal).
_YT_STATE_BUFFERING = 3
_YT_STATE_ENDED = 0


# ═══════════════════════════════════════════════════════════════════════════
#  loading + small parsers
# ═══════════════════════════════════════════════════════════════════════════
def load_samples(source: Any) -> list[dict[str, Any]]:
    """Load samples from a JSONL path, an open file, or an in-memory list."""
    if isinstance(source, list):
        return source
    path = Path(source)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a truncated final line if the collector was killed
    return out


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return None if isinstance(v, float) and math.isnan(v) else float(v)
    if isinstance(v, str):
        # "5120 Kbps", "53.38 s", "0 KB"
        head = v.strip().split()
        if head:
            try:
                return float(head[0])
            except ValueError:
                return None
    return None


def _stats_of(sample: dict[str, Any]) -> dict[str, Any]:
    s = sample.get("stats")
    return s if isinstance(s, dict) else {}


def parse_buffer_ahead(stats: dict[str, Any]) -> Optional[float]:
    """Seconds of media buffered ahead of the playhead."""
    v = _num(stats.get("buffer_ahead_secs"))
    if v is not None:
        return v
    return _num(stats.get("buffer_health_seconds"))  # YouTube's SFN spelling


def parse_connection_speed_mbps(stats: dict[str, Any]) -> Optional[float]:
    """YouTube's *connection speed estimate* — explicitly NOT the media bitrate."""
    kbps = _num(stats.get("bandwidth_kbps"))
    if kbps is not None and kbps > 0:
        return round(kbps / 1000.0, 4)
    samples = stats.get("bandwidth_samples")
    if isinstance(samples, list):
        vals = [float(v) for v in samples if isinstance(v, (int, float)) and v > 0]
        if vals:  # bytes/sec
            return round((sum(vals) / len(vals)) * 8 / 1e6, 4)
    return None


def _parse_bytes(stats: dict[str, Any]) -> Optional[float]:
    """Cumulative bytes the player reports having fetched, if it reports any."""
    raw = stats.get("network_activity_bytes")
    v = _num(raw)
    if v is None:
        return None
    unit = ""
    if isinstance(raw, str):
        parts = raw.strip().split()
        unit = parts[1].upper() if len(parts) > 1 else ""
    mult = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9}.get(unit, 1)
    return v * mult


def _height_of(stats: dict[str, Any]) -> Optional[int]:
    h = _num(stats.get("video_height"))
    if h is not None and h > 0:
        return int(h)
    res = stats.get("resolution")
    if isinstance(res, str) and "x" in res:
        try:
            h2 = int(res.split("x")[1].split()[0])
            return h2 if h2 > 0 else None
        except (ValueError, IndexError):
            return None
    return None


def _is_paused(stats: dict[str, Any]) -> bool:
    p = stats.get("paused")
    return bool(p) if isinstance(p, bool) else False


def _is_ended(stats: dict[str, Any], duration: Optional[float]) -> bool:
    if _num(stats.get("player_state")) == _YT_STATE_ENDED:
        return True
    cur = _num(stats.get("current_time_secs"))
    if cur is not None and duration and duration > 0:
        return cur >= duration - 0.5
    return False


# ═══════════════════════════════════════════════════════════════════════════
#  html5_video
# ═══════════════════════════════════════════════════════════════════════════
def _summarize_html5(
    samples: list[dict[str, Any]],
    spec: "_apps.AppSpec",
    play_trigger_ts: Optional[float],
) -> dict[str, Any]:
    ts: list[float] = []
    st: list[dict[str, Any]] = []
    for s in samples:
        t = _num(s.get("timestamp"))
        if t is None:
            continue
        ts.append(t)
        st.append(_stats_of(s))
    if not ts:
        return _empty(spec, "no_samples")

    t0 = ts[0]
    rel = [t - t0 for t in ts]
    n = len(ts)
    duration = next(
        (
            d
            for d in (_num(x.get("duration_secs")) for x in st)
            # YouTube reports a nonsense duration (1.2e8) before metadata loads
            if d is not None and 0 < d < 86400
        ),
        None,
    )

    cur = [_num(x.get("current_time_secs")) for x in st]
    buf = [parse_buffer_ahead(x) for x in st]
    heights = [_height_of(x) for x in st]

    # ── playback start: first interval where the media clock actually moves ──
    first_advance_idx = None
    for i in range(1, n):
        a, b = cur[i - 1], cur[i]
        if a is not None and b is not None and (b - a) > _ADVANCE_FLOOR_S:
            first_advance_idx = i
            break

    startup_ms: Optional[float] = None
    startup_basis = "none"
    if first_advance_idx is not None:
        origin = play_trigger_ts if play_trigger_ts is not None else t0
        startup_basis = (
            "play_trigger" if play_trigger_ts is not None else "first_sample"
        )
        startup_ms = round(max(0.0, (ts[first_advance_idx] - origin)) * 1000.0, 1)

    if first_advance_idx is None:
        # The page never played. Report that plainly rather than zeros.
        out = _empty(spec, "no_playback")
        out["total_samples"] = n
        out["session_seconds"] = round(rel[-1], 2)
        out["resolutions_observed"] = sorted({h for h in heights if h})
        return out

    # ── rebuffers: playback frozen while the player is trying to play ────────
    rebuffer_spans: list[tuple[float, float]] = []
    span_start: Optional[float] = None
    frozen_total = 0.0
    for i in range(first_advance_idx, n):
        dt = ts[i] - ts[i - 1]
        if dt <= 0:
            continue
        a, b = cur[i - 1], cur[i]
        if a is None or b is None:
            continue
        progressed = b - a
        state = _num(st[i].get("player_state"))
        trying = not _is_paused(st[i]) and not _is_ended(st[i], duration)
        stalled = progressed < max(_ADVANCE_FLOOR_S, _ADVANCE_RATIO * dt)
        # Corroboration: an empty buffer or the vendor's own BUFFERING state.
        corroborated = (
            (buf[i] is not None and buf[i] <= _EMPTY_BUFFER_S)
            or state == _YT_STATE_BUFFERING
            or buf[i] is None  # no buffer signal: fall back to the clock alone
        )
        if trying and stalled and corroborated:
            frozen_total += dt
            if span_start is None:
                span_start = rel[i - 1]
        elif span_start is not None:
            rebuffer_spans.append((round(span_start, 2), round(rel[i - 1], 2)))
            span_start = None
    if span_start is not None:
        rebuffer_spans.append((round(span_start, 2), round(rel[-1], 2)))

    # ── resolution ───────────────────────────────────────────────────────────
    active_heights = [h for i, h in enumerate(heights) if i >= first_advance_idx and h]
    res_p = Counter(active_heights).most_common(1)[0][0] if active_heights else None
    res_changes = 0
    prev_h = None
    res_timeline = []
    for i in range(first_advance_idx, n):
        h = heights[i]
        if h and h != prev_h:
            if prev_h is not None:
                res_changes += 1
            res_timeline.append({"t": round(rel[i], 2), "v": h})
            prev_h = h

    # ── frames: cumulative counters → ratio + derived fps ────────────────────
    dropped = _last_num(st, "dropped_video_frames")
    total_frames = _last_num(st, "total_video_frames")
    dropped_pct = None
    if dropped is not None and total_frames:
        dropped_pct = round(100.0 * dropped / total_frames, 3)

    fps = None
    fps_basis = "none"
    first_tf = _first_num(st, "total_video_frames", start=first_advance_idx)
    if first_tf is not None and total_frames is not None and total_frames > first_tf:
        span = ts[-1] - ts[first_advance_idx]
        if span > 1.0:
            fps = round((total_frames - first_tf) / span, 2)
            fps_basis = "total_video_frames_delta"

    # ── bitrate: real or null, never a stand-in ──────────────────────────────
    bitrate, bitrate_basis = _derive_bitrate(st, ts, first_advance_idx, spec)

    conn = [parse_connection_speed_mbps(x) for x in st]
    conn_vals = [c for c in conn if c is not None]

    # Use the last *available* media clock: collectors routinely lose the final
    # samples when the page is torn down mid-poll, and cur[-1] is then None.
    watched_s = None
    cur_last = next((c for c in reversed(cur) if c is not None), None)
    cur_first = next((c for c in cur[first_advance_idx:] if c is not None), None)
    if cur_first is not None and cur_last is not None:
        watched_s = round(max(0.0, cur_last - cur_first), 2)

    out: dict[str, Any] = {
        "app": spec.name,
        "kind": spec.kind,
        "player_qoe_available": True,
        "status": "ok",
        # ── QoEMetrics fields ────────────────────────────────────────────────
        "video_startup_time_ms": startup_ms,
        "mean_bitrate_mbps": bitrate,
        "max_bitrate_mbps": None,
        "min_bitrate_mbps": None,
        "mean_watched_bitrate_mbps": None,
        "bitrate_changes": None,  # no per-sample bitrate ⇒ cannot count switches
        "rebuffer_events": len(rebuffer_spans),
        "rebuffer_duration_ms": round(frozen_total * 1000.0, 1),
        "stall_duration_ms": round(frozen_total * 1000.0, 1),
        "video_resolution_p": res_p,
        "frame_rate_fps": fps,
        # ── extras (real, derived, documented) ───────────────────────────────
        "dropped_frame_pct": dropped_pct,
        "dropped_video_frames": int(dropped) if dropped is not None else None,
        "total_video_frames": int(total_frames) if total_frames is not None else None,
        "resolution_changes": res_changes,
        "resolutions_observed": sorted({h for h in active_heights}),
        "mean_buffer_ahead_secs": _mean([b for b in buf if b is not None]),
        "min_buffer_ahead_secs": (
            round(min(b for b in buf if b is not None), 2)
            if any(b is not None for b in buf)
            else None
        ),
        "connection_speed_estimate_mbps": _mean(conn_vals),
        "watched_seconds": watched_s,
        "session_seconds": round(rel[-1], 2),
        "total_samples": n,
        "is_live": bool(spec.live or _truthy(st, "is_live")),
        "video_duration_secs": None if spec.live else duration,
        # ── provenance: how each soft number was obtained ────────────────────
        "derivation": {
            "startup_basis": startup_basis,
            "bitrate_basis": bitrate_basis,
            "fps_basis": fps_basis,
            "rebuffer_rule": (
                "current_time_secs not advancing while not paused and not ended, "
                "corroborated by empty buffer or vendor BUFFERING state"
            ),
        },
        # ── timelines for the plots ──────────────────────────────────────────
        "series": {
            "buffer_ahead_secs": [
                {"t": round(rel[i], 2), "v": buf[i]}
                for i in range(n)
                if buf[i] is not None
            ],
            "resolution_p": [
                {"t": round(rel[i], 2), "v": heights[i]} for i in range(n) if heights[i]
            ],
            "current_time_secs": [
                {"t": round(rel[i], 2), "v": cur[i]}
                for i in range(n)
                if cur[i] is not None
            ],
        },
        "rebuffer_spans": rebuffer_spans,
    }
    if spec.live:
        # No finite duration ⇒ "fraction of the video delivered" is meaningless.
        out["delivered_fraction_of_video"] = None
    elif duration and watched_s is not None:
        out["delivered_fraction_of_video"] = round(min(1.0, watched_s / duration), 3)
    return out


def _derive_bitrate(
    st: list[dict[str, Any]],
    ts: list[float],
    start: int,
    spec: "_apps.AppSpec",
) -> tuple[Optional[float], str]:
    """Real media bitrate in Mbps, or (None, reason). Never fabricated.

    A bitrate is only emitted from a genuinely CUMULATIVE, non-decreasing byte
    counter. YouTube's ``network_activity_bytes`` is deliberately rejected: it is
    an instantaneous "recent activity" gauge, not a counter — observed values
    bounce (35 KB, 0, 0, 0, 71 KB, 47 KB, ...). Differencing it as though it
    accumulated yields nonsense (~0.004 Mbps for a 480p stream), which is exactly
    the kind of fabricated number this module exists to prevent.

    Note also that per-app *network throughput* is measured separately from the
    PCAP. That is not a media bitrate either — it carries audio, container and
    protocol overhead — so it is never substituted here.
    """
    byte_vals = [(ts[i], _parse_bytes(st[i])) for i in range(start, len(st))]
    byte_vals = [(t, b) for t, b in byte_vals if b is not None]
    if len(byte_vals) >= 2:
        series = [b for _t, b in byte_vals]
        monotonic = all(b >= a for a, b in zip(series, series[1:]))
        (t_a, b_a), (t_b, b_b) = byte_vals[0], byte_vals[-1]
        if monotonic and b_b > b_a and (t_b - t_a) > 1.0:
            return round((b_b - b_a) * 8 / (t_b - t_a) / 1e6, 4), "player_byte_counter"
        if not monotonic:
            return None, "unavailable (player reports an activity gauge, not a counter)"
    return None, f"unavailable ({spec.bitrate_source})"


# ═══════════════════════════════════════════════════════════════════════════
#  webrtc
# ═══════════════════════════════════════════════════════════════════════════
def _summarize_webrtc(
    samples: list[dict[str, Any]], spec: "_apps.AppSpec"
) -> dict[str, Any]:
    """Surface the per-interval WebRTC metrics a conferencing collector derives.

    Expects each sample's ``stats`` to carry inbound-rtp derived fields. If no
    sample shows inbound media from a REMOTE peer, the app is reported as
    skipped rather than describing the local camera preview as a measurement.
    """
    rows = [_stats_of(s) for s in samples]
    ts = [_num(s.get("timestamp")) for s in samples]
    ts = [t for t in ts if t is not None]
    if not rows or not ts:
        return _empty(spec, "no_samples")

    def col(*names: str) -> list[float]:
        out = []
        for r in rows:
            for nm in names:
                v = _num(r.get(nm))
                if v is not None:
                    out.append(v)
                    break
        return out

    inbound = col("inbound_bitrate_kbps", "bitrate_kbps", "inbound_bitrate")
    if not any(v > 0 for v in inbound):
        return _empty(spec, "no_remote_media (no peer publishing video)")

    frames_dropped = col("frames_dropped", "framesDropped")
    frames_decoded = col("frames_decoded", "framesDecoded")
    loss = col("packet_loss_pct", "packets_lost_pct", "fraction_lost")
    freeze_count = col("freeze_count", "freezeCount")
    freeze_dur = col("freeze_duration_secs", "total_freezes_duration")
    jitter = col("jitter", "jitter_secs")
    heights = [h for h in (_height_of(r) for r in rows) if h]

    drop_pct = None
    if frames_decoded and frames_dropped:
        dec, drp = max(frames_decoded), max(frames_dropped)
        if dec > 0:
            drop_pct = round(100.0 * drp / dec, 3)

    return {
        "app": spec.name,
        "kind": spec.kind,
        "player_qoe_available": True,
        "status": "ok",
        "video_startup_time_ms": None,
        "mean_bitrate_mbps": round(_mean(inbound) / 1000.0, 4) if inbound else None,
        "max_bitrate_mbps": round(max(inbound) / 1000.0, 4) if inbound else None,
        "min_bitrate_mbps": round(min(inbound) / 1000.0, 4) if inbound else None,
        "mean_watched_bitrate_mbps": None,
        "bitrate_changes": None,
        "rebuffer_events": int(max(freeze_count)) if freeze_count else None,
        "rebuffer_duration_ms": (
            round(max(freeze_dur) * 1000.0, 1) if freeze_dur else None
        ),
        "stall_duration_ms": (
            round(max(freeze_dur) * 1000.0, 1) if freeze_dur else None
        ),
        "video_resolution_p": (
            Counter(heights).most_common(1)[0][0] if heights else None
        ),
        "frame_rate_fps": None,
        "packet_loss_pct": _mean(loss),
        "dropped_frame_pct": drop_pct,
        "mean_jitter_secs": _mean(jitter),
        "resolutions_observed": sorted(set(heights)),
        "total_samples": len(rows),
        "is_live": True,
        "derivation": {
            "source": "RTCPeerConnection.getStats() inbound-rtp (remote peer)",
            "rebuffer_rule": "WebRTC freeze count / freeze duration",
        },
        "series": {
            "inbound_bitrate_kbps": [
                {"t": round(ts[i] - ts[0], 2), "v": inbound[i]}
                for i in range(min(len(ts), len(inbound)))
            ],
        },
        "rebuffer_spans": [],
    }


# ═══════════════════════════════════════════════════════════════════════════
#  shell
# ═══════════════════════════════════════════════════════════════════════════
def _summarize_shell(
    spec: "_apps.AppSpec", transfer: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """No player, no player QoE — a transfer summary and nothing invented."""
    t = transfer or {}
    return {
        "app": spec.name,
        "kind": spec.kind,
        "player_qoe_available": False,
        "status": "ok",
        "reason": "shell app: no browser player, so player-side QoE does not exist",
        "transfer": {
            "bytes": t.get("bytes"),
            "seconds": t.get("seconds"),
            "completed": t.get("completed"),
            "mean_throughput_mbps": (
                round(t["bytes"] * 8 / t["seconds"] / 1e6, 4)
                if t.get("bytes") and t.get("seconds")
                else None
            ),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
#  entry point
# ═══════════════════════════════════════════════════════════════════════════
def summarize(
    source: Any,
    app: str,
    play_trigger_ts: Optional[float] = None,
    transfer: Optional[dict[str, Any]] = None,
    skipped_reason: Optional[str] = None,
) -> dict[str, Any]:
    """Reduce one app's samples to a QoEMetrics-shaped dict.

    `source` is a JSONL path or a list of samples. `play_trigger_ts` is the epoch
    time the collector triggered playback (enables a true startup measurement);
    without it startup is measured from the first sample and flagged as such.
    `skipped_reason`, when given, short-circuits to a skipped record — used for
    `needs_peer` apps with no peer, or a web client that could not join.
    """
    spec = _apps.get(app)
    if skipped_reason:
        return _skipped(spec, skipped_reason)
    if spec.kind == _apps.SHELL:
        return _summarize_shell(spec, transfer)

    samples = load_samples(source)
    if not samples:
        return _empty(spec, "no_samples (collector produced no output)")

    # The collector writes a leading {"record": "meta", ...} line carrying the
    # moment playback was triggered. Prefer it over a caller-supplied value so
    # startup time is measured from the real trigger, not the first sample.
    meta = next((s for s in samples if s.get("record") == "meta"), None)
    if meta is not None:
        samples = [s for s in samples if s.get("record") != "meta"]
        if play_trigger_ts is None:
            play_trigger_ts = _num(meta.get("play_trigger_ts"))
    if not samples:
        return _empty(spec, "no_samples (only metadata written)")

    if spec.kind == _apps.WEBRTC:
        return _summarize_webrtc(samples, spec)
    return _summarize_html5(samples, spec, play_trigger_ts)


def summarize_run(
    stats_paths: dict[str, Any],
    play_triggers: Optional[dict[str, float]] = None,
    transfers: Optional[dict[str, dict[str, Any]]] = None,
    skipped: Optional[dict[str, str]] = None,
) -> dict[str, dict[str, Any]]:
    """Summarize every app in a run. Returns {app: qoe_dict}."""
    out: dict[str, dict[str, Any]] = {}
    for app, path in stats_paths.items():
        out[app] = summarize(
            path,
            app,
            play_trigger_ts=(play_triggers or {}).get(app),
            transfer=(transfers or {}).get(app),
            skipped_reason=(skipped or {}).get(app),
        )
    return out


def any_real_qoe(per_app: dict[str, dict[str, Any]]) -> bool:
    """True when at least one app produced real player-side QoE."""
    return any(v.get("player_qoe_available") for v in per_app.values())


# ═══════════════════════════════════════════════════════════════════════════
#  helpers
# ═══════════════════════════════════════════════════════════════════════════
def _mean(vals: Iterable[float]) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _last_num(st: list[dict[str, Any]], key: str) -> Optional[float]:
    for s in reversed(st):
        v = _num(s.get(key))
        if v is not None:
            return v
    return None


def _first_num(st: list[dict[str, Any]], key: str, start: int = 0) -> Optional[float]:
    for s in st[start:]:
        v = _num(s.get(key))
        if v is not None:
            return v
    return None


def _truthy(st: list[dict[str, Any]], key: str) -> bool:
    return any(bool(s.get(key)) for s in st)


def _empty(spec: "_apps.AppSpec", reason: str) -> dict[str, Any]:
    """No usable player signal — say so; do not emit zeros that read as data."""
    return {
        "app": spec.name,
        "kind": spec.kind,
        "player_qoe_available": False,
        "status": "no_data",
        "reason": reason,
        "video_startup_time_ms": None,
        "mean_bitrate_mbps": None,
        "rebuffer_events": None,
        "rebuffer_duration_ms": None,
        "video_resolution_p": None,
        "frame_rate_fps": None,
        "dropped_frame_pct": None,
        "series": {},
        "rebuffer_spans": [],
    }


def _skipped(spec: "_apps.AppSpec", reason: str) -> dict[str, Any]:
    out = _empty(spec, reason)
    out["status"] = "skipped"
    return out
