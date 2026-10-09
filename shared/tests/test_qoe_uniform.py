"""The uniform streaming-QoE derivations: one method, any site.

Each test pins a decision that was made from a measurement on the real apps
rather than from the spec alone, because the spec allows several readings and
the sites do not all behave the same way.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from shared import qoe  # noqa: E402


def _samples(rows, interval=1.0, start=1_000_000.0):
    """rows: list of dicts merged into an otherwise-playing sample."""
    out = []
    for i, r in enumerate(rows):
        st = {
            "current_time_secs": float(i),
            "total_video_frames": 24 * (i + 1),
            "dropped_video_frames": 0,
            "paused": False,
            "ready_state": 4,
            "video_width": 1280,
            "video_height": 720,
            "resolution": "1280x720",
            "sampling_interval_s": interval,
        }
        st.update(r)
        out.append({"timestamp": start + i * interval, "stats": st})
    return out


def _st(samples):
    return [s["stats"] for s in samples]


def _ts(samples):
    return [s["timestamp"] for s in samples]


# ── delivered bitrate ───────────────────────────────────────────────────────
def test_video_bitrate_excludes_the_audio_sourcebuffer():
    """Measured on this host: YouTube's audio buffer held 26% of appended bytes.

    A combined total would have reported a video bitrate about a third too high,
    so audio must be a separate figure.
    """
    rows = [
        {"mse_video_bytes": 0, "mse_audio_bytes": 0},
        {"mse_video_bytes": 1_000_000, "mse_audio_bytes": 250_000},
        {"mse_video_bytes": 2_000_000, "mse_audio_bytes": 500_000},
    ]
    s = _samples(rows)
    v, a, basis = qoe.delivered_bitrate(_st(s), _ts(s), 0)
    # 2 MB over 2 s = 8 Mbps video; 0.5 MB over 2 s = 2 Mbps audio
    assert v == 8.0
    assert a == 2.0
    assert "sourcebuffer" in basis


def test_bitrate_is_none_and_says_why_when_the_player_uses_a_worker():
    """Twitch plays with MediaSource inside a Worker: 0 main-world
    constructions, 2 Workers, srcObject set. A main-world hook is structurally
    blind there, so the honest answer is None with a reason, not 0.
    """
    rows = [{"uses_worker_media": True}, {"uses_worker_media": True}]
    s = _samples(rows)
    v, a, basis = qoe.delivered_bitrate(_st(s), _ts(s), 0)
    assert v is None and a is None
    assert "Worker" in basis


def test_zero_appended_bytes_is_unavailable_not_zero_mbps():
    """Regression: Twitch plays via a Worker, the probe emits 0 for its empty
    buffer list, and the first version of this code reported 0.0 Mbps - a
    fabricated measurement. Zero bytes observed means the hook saw nothing.
    """
    rows = [
        {"mse_video_bytes": 0, "mse_audio_bytes": 0, "uses_worker_media": True},
        {"mse_video_bytes": 0, "mse_audio_bytes": 0, "uses_worker_media": True},
    ]
    s = _samples(rows)
    v, a, basis = qoe.delivered_bitrate(_st(s), _ts(s), 0)
    assert v is None and a is None
    assert "Worker" in basis


def test_zero_appended_bytes_without_a_worker_also_reports_unavailable():
    rows = [{"mse_video_bytes": 0}, {"mse_video_bytes": 0}]
    s = _samples(rows)
    v, _, basis = qoe.delivered_bitrate(_st(s), _ts(s), 0)
    assert v is None
    assert "no bytes appended" in basis


def test_bitrate_rejects_a_counter_that_goes_backwards():
    rows = [{"mse_video_bytes": 5_000_000}, {"mse_video_bytes": 1_000}]
    s = _samples(rows)
    v, _, basis = qoe.delivered_bitrate(_st(s), _ts(s), 0)
    assert v is None
    assert "did not advance" in basis


# ── rebuffering ─────────────────────────────────────────────────────────────
def test_rebuffers_come_from_waiting_to_playing_spans():
    spans = [
        {"start": 1000, "end": 2500, "dur_ms": 1500, "ct": 4.0, "during_seek": False},
        {"start": 9000, "end": 9400, "dur_ms": 400, "ct": 11.2, "during_seek": False},
    ]
    s = _samples([{}, {"waiting_spans": spans}])
    n, ms, real, basis = qoe.event_rebuffers(_st(s))
    assert n == 2
    assert ms == 1900.0
    assert "waiting" in basis


def test_initial_buffering_is_startup_not_a_rebuffer():
    """Regression. Measured across 12 ladder runs: every run had exactly one
    `waiting` span at currentTime 0 - the initial buffer fill - so counting it
    inflated every rebuffer figure by one. YouTube at 1.5/3/6/10Mbps reported
    one rebuffer and actually had none.
    """
    spans = [
        {"start": 500, "end": 1936, "dur_ms": 1436, "ct": 0, "during_seek": False},
        {"start": 9000, "end": 11021, "dur_ms": 2021, "ct": 27.5, "during_seek": False},
    ]
    s = _samples([{"waiting_spans": spans}])
    n, ms, real, basis = qoe.event_rebuffers(_st(s))
    assert n == 1
    assert ms == 2021.0
    assert "initial buffering excluded" in basis
    assert qoe.initial_buffering_ms(_st(s)) == 1436.0


def test_a_run_whose_only_stall_was_the_initial_fill_has_zero_rebuffers():
    spans = [{"start": 0, "end": 676, "dur_ms": 676, "ct": 0, "during_seek": False}]
    s = _samples([{"waiting_spans": spans}])
    n, ms, _, _ = qoe.event_rebuffers(_st(s))
    assert n == 0
    assert ms == 0.0
    assert qoe.initial_buffering_ms(_st(s)) == 676.0


def test_a_seek_is_not_a_rebuffer():
    """Seeking also fires `waiting`; counting it would invent stalls."""
    spans = [
        {"start": 1000, "end": 2000, "dur_ms": 1000, "ct": 8.0, "during_seek": True},
        {"start": 5000, "end": 5500, "dur_ms": 500, "ct": 9.5, "during_seek": False},
    ]
    s = _samples([{"waiting_spans": spans}])
    n, ms, _, _ = qoe.event_rebuffers(_st(s))
    assert n == 1
    assert ms == 500.0


def test_rebuffers_report_unavailable_rather_than_zero_without_the_probe():
    s = _samples([{}, {}])
    n, ms, _, basis = qoe.event_rebuffers(_st(s))
    assert n is None and ms is None
    assert "unavailable" in basis


# ── rendered frame rate ─────────────────────────────────────────────────────
def test_rendered_fps_uses_presented_frames_not_decoded():
    """presentedFrames counts frames submitted for composition; totalVideoFrames
    counts decoded ones. Here decode runs at 24fps while only 12fps reach the
    compositor, and the rendered figure must report 12.
    """
    rows = [{"presented_frames": 0}, {"presented_frames": 12}, {"presented_frames": 24}]
    s = _samples(rows)
    fps, basis = qoe.presented_frame_rate(_st(s), _ts(s), 0)
    assert fps == 12.0
    assert "presentedFrames" in basis
    # the decoded series in the same samples advances at 24/s
    assert _st(s)[-1]["total_video_frames"] - _st(s)[0]["total_video_frames"] == 48


def test_rendered_fps_unavailable_without_rvfc():
    s = _samples([{"presented_frames": None}, {"presented_frames": None}])
    fps, basis = qoe.presented_frame_rate(_st(s), _ts(s), 0)
    assert fps is None
    assert "requestVideoFrameCallback" in basis


# ── switch events ───────────────────────────────────────────────────────────
def test_switches_are_timestamped_and_carry_direction():
    rows = [
        {"video_height": 720, "resolution": "1280x720"},
        {"video_height": 360, "resolution": "640x360"},
        {"video_height": 720, "resolution": "1280x720"},
    ]
    s = _samples(rows)
    ev, basis = qoe.switch_events(_st(s), _ts(s), 0)
    assert [e["direction"] for e in ev] == ["down", "up"]
    assert ev[0]["from_p"] == 720 and ev[0]["to_p"] == 360
    assert ev[1]["from_p"] == 360 and ev[1]["to_p"] == 720
    assert ev[0]["t"] == 1.0 and ev[1]["t"] == 2.0
    assert "decoded_height" in basis


def test_a_codec_change_is_reported_even_at_constant_resolution():
    """changeType switches codec without a new SourceBuffer, and no resolution
    series would reveal it."""
    log = [{"t": 4000, "mime": 'video/mp4; codecs="avc1.4d401f"'}]
    s = _samples([{}, {"mse_mime_switch_log": log}])
    ev, _ = qoe.switch_events(_st(s), _ts(s), 0)
    codec = [e for e in ev if e["direction"] == "codec_change"]
    assert len(codec) == 1
    assert codec[0]["t"] == 4.0
    assert "avc1" in codec[0]["to_mime"]


def test_no_switches_when_the_rendition_never_changes():
    s = _samples([{}, {}, {}])
    ev, _ = qoe.switch_events(_st(s), _ts(s), 0)
    assert ev == []
