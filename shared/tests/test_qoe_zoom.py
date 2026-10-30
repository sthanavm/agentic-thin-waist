"""Zoom: frame-driven liveness and the frame-based rebuffer rule.

Zoom's web client renders remote camera video into a MediaStream-backed
``<video>`` whose ``currentTime`` stays pinned at 0 for the entire call. Every
clock-based rule in ``_summarize_html5`` therefore reads a perfectly healthy
meeting as dead. These tests pin the two consequences of that -- liveness and
rebuffering must come from ``total_video_frames`` -- and, just as importantly,
that the switch is scoped by the registry flag so no other app moves.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from shared import apps as _apps  # noqa: E402
from shared import qoe  # noqa: E402


def _samples(frames, *, start=1_000_000.0, step=1.0, w=640, h=360):
    """Zoom-shaped samples: the clock is frozen, only the frame counter moves."""
    return [
        {
            "timestamp": start + i * step,
            "stats": {
                "current_time_secs": 0,  # pinned at 0 for the whole call
                "total_video_frames": f,
                "dropped_video_frames": 0,
                "paused": False,
                "ready_state": 4,
                "video_width": w,
                "video_height": h,
                "resolution": f"{w}x{h}",
            },
        }
        for i, f in enumerate(frames)
    ]


def test_zoom_is_registered_as_frame_driven():
    spec = _apps.get("zoom")
    assert spec.progress_from_frames is True
    assert spec.kind == _apps.HTML5_VIDEO  # no getStats() path exists
    assert spec.bitrate_source == "none"


def test_advancing_frames_are_playback_despite_a_frozen_clock():
    out = qoe.summarize(_samples(range(0, 600, 20)), "zoom")
    assert out["status"] == "ok"
    assert out["video_resolution_p"] == 360
    assert out["total_video_frames"] == 580
    assert out["derivation"]["progress_basis"].startswith("total_video_frames")


def test_no_phantom_rebuffer_while_frames_advance():
    """The bug this guards: the clock rule scored a whole 60s call as one stall."""
    out = qoe.summarize(_samples(range(0, 600, 20)), "zoom")
    assert out["rebuffer_events"] == 0
    assert out["rebuffer_duration_ms"] == 0.0
    assert out["watched_seconds"] > 25
    assert out["derivation"]["rebuffer_rule"].startswith("total_video_frames")


def test_a_real_freeze_is_still_caught():
    """Frames standing still is a genuine stall and must be reported as one."""
    frames = [0, 20, 40, 60, 60, 60, 60, 80, 100, 120]
    out = qoe.summarize(_samples(frames), "zoom")
    assert out["rebuffer_events"] == 1
    assert out["rebuffer_duration_ms"] == 3000.0
    # Frozen seconds are not credited as watched.
    assert out["watched_seconds"] < out["session_seconds"]


def test_frames_never_advancing_is_not_playback():
    out = qoe.summarize(_samples([10] * 8), "zoom")
    assert out["status"] != "ok"


def test_clock_driven_apps_are_untouched():
    """The same frozen-clock samples must still read as dead for YouTube."""
    assert _apps.get("youtube").progress_from_frames is False
    out = qoe.summarize(_samples(range(0, 600, 20)), "youtube")
    assert out["status"] != "ok"


def test_clock_driven_rebuffer_rule_is_unchanged():
    playing = [
        {
            "timestamp": 1_000_000.0 + i,
            "stats": {
                "current_time_secs": float(i),
                "total_video_frames": i * 30,
                "dropped_video_frames": 0,
                "paused": False,
                "ready_state": 4,
                "buffered_end_secs": float(i) + 8.0,
                "video_width": 1280,
                "video_height": 720,
            },
        }
        for i in range(10)
    ]
    out = qoe.summarize(playing, "youtube")
    assert out["status"] == "ok"
    assert out["rebuffer_events"] == 0
    assert out["derivation"]["progress_basis"] == "current_time_secs"
    assert out["derivation"]["rebuffer_rule"].startswith("current_time_secs")


def test_join_url_is_rewritten_to_the_web_client():
    """A /j/ invite lands on a launcher whose join click is swallowed."""
    assert (
        _apps.normalize_join_url("zoom", "https://us04web.zoom.us/j/781?pwd=AbC.1")
        == "https://us04web.zoom.us/wc/781/join?pwd=AbC.1"
    )
    # Already a web-client URL, no password, and other apps: all left alone.
    wc = "https://us04web.zoom.us/wc/781/join?pwd=AbC.1"
    assert _apps.normalize_join_url("zoom", wc) == wc
    assert (
        _apps.normalize_join_url("zoom", "https://us04web.zoom.us/j/781")
        == "https://us04web.zoom.us/wc/781/join"
    )
    meet = "https://meet.google.com/abc-defg-hij"
    assert _apps.normalize_join_url("meet", meet) == meet
    assert _apps.normalize_join_url("zoom", "") == ""


def test_url_for_normalises_an_override():
    assert (
        _apps.url_for("zoom", {"zoom": "https://us04web.zoom.us/j/99?pwd=Z.1"})
        == "https://us04web.zoom.us/wc/99/join?pwd=Z.1"
    )
