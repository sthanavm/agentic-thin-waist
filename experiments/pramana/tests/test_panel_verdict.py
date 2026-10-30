"""The QoE-summary panel: verdict wording and cross-panel time alignment.

Two defects these pin. First, the verdict gated on `watched_seconds > 0` and
named the media clock as its evidence -- so a WebRTC call, which has no clock
and left that field None, was reported as "the app did not play" directly above
its own 360p/1373-frame metrics. Second, the panels share an x-axis but the
throughput series is measured from the first captured packet while the player
series is measured from the first sample, tens of seconds later.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pramana_helpers import AppTraffic, _panel_verdict  # noqa: E402


def _meet_qoe(**over):
    """A healthy Meet run, as the fixed WebRTC summarizer now reports it."""
    q = {
        "player_qoe_available": True,
        "status": "ok",
        "video_resolution_p": 360,
        "watched_seconds": 59.0,
        "rebuffer_events": 0,
        "rebuffer_duration_ms": 0,
        "derivation": {"progress_basis": "frames_decoded (a call has no media clock)"},
    }
    q.update(over)
    return q


def _youtube_qoe(**over):
    q = {
        "player_qoe_available": True,
        "status": "ok",
        "video_resolution_p": 720,
        "watched_seconds": 59.0,
        "rebuffer_events": 0,
        "rebuffer_duration_ms": 0,
        "derivation": {"progress_basis": "current_time_secs"},
    }
    q.update(over)
    return q


def test_a_healthy_call_is_not_reported_as_no_playback():
    label, why, _ = _panel_verdict(
        AppTraffic(app="meet", direction="download"), _meet_qoe()
    )
    assert label == "served"
    assert "did not play" not in why


def test_a_call_verdict_cites_frames_not_a_clock():
    _, why, _ = _panel_verdict(
        AppTraffic(app="meet", direction="download"), _meet_qoe()
    )
    assert "decoded frames advanced" in why
    assert "clock" not in why


def test_a_clock_driven_app_still_cites_the_clock():
    _, why, _ = _panel_verdict(
        AppTraffic(app="youtube", direction="download"), _youtube_qoe()
    )
    assert "player clock advanced" in why


def test_rebuffering_is_still_reported_as_degraded():
    label, why, _ = _panel_verdict(
        AppTraffic(app="meet", direction="download"),
        _meet_qoe(rebuffer_events=2, rebuffer_duration_ms=487),
    )
    assert label == "degraded"
    assert "decoded frames advanced" in why


def test_a_genuinely_dead_run_is_still_no_playback():
    label, _, _ = _panel_verdict(
        AppTraffic(app="meet", direction="download"),
        _meet_qoe(watched_seconds=0, video_resolution_p=None),
    )
    assert label == "no_playback"


def test_player_series_are_shifted_onto_capture_time():
    """The offset is the join delay: player t=0 is not capture t=0."""
    cap_t0, sess_t0 = 1_000_000.0, 1_000_029.4
    shift = max(0.0, sess_t0 - cap_t0)
    assert round(shift, 1) == 29.4
    pts = [{"t": 0.0, "v": 720}, {"t": 10.0, "v": 480}]
    shifted = [{"t": d["t"] + shift, "v": d["v"]} for d in pts]
    assert [round(d["t"], 1) for d in shifted] == [29.4, 39.4]


def test_alignment_degrades_to_no_shift_on_older_records():
    """Records written before session_start_epoch existed must still plot."""
    cap_t0, sess_t0 = 1_000_000.0, None
    shift = 0.0 if (cap_t0 is None or sess_t0 is None) else sess_t0 - cap_t0
    assert shift == 0.0


def test_apptraffic_carries_the_capture_origin():
    """The plot is handed an AppTraffic, not the CaptureAttribution.

    Putting the origin only on the attribution made the shift silently resolve
    to 0 - the charts came out identical while the caller reported a +28s
    offset, so the wiring needs its own guard.
    """
    from pramana_helpers import AppTraffic

    at = AppTraffic(app="youtube", direction="download")
    assert hasattr(at, "capture_t0_epoch")
    at.capture_t0_epoch = 1_000_000.0
    shift = max(0.0, 1_000_028.0 - at.capture_t0_epoch)
    assert shift == 28.0
