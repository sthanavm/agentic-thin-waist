"""WebRTC (Google Meet): progress, startup and bitrate from getStats().

A call has no media clock -- there is no seekable timeline, so currentTime is
meaningless. Progress has to come from the decoded-frame counter. Leaving those
fields None made a healthy call fail the panel verdict's `watched_seconds > 0`
gate, so a run with 1373 decoded frames at 360p was labelled "the app did not
play". These tests pin the derived fields and the startup-spike exclusion.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from shared import qoe  # noqa: E402

T0 = 1_000_000.0


def _call(frames, *, bitrates=None, step=1.0, w=640, h=360, dropped=0):
    """Meet-shaped samples: a frame counter and inbound-rtp bitrate, no clock."""
    n = len(frames)
    bitrates = bitrates if bitrates is not None else [1.1] * n
    return [
        {
            "timestamp": T0 + i * step,
            "stats": {
                "frames_decoded": f,
                "frames_dropped": dropped,
                "bytes_received": 50_000 * (i + 1),
                "inbound_bitrate_mbps": bitrates[i],
                "frame_width": w,
                "frame_height": h,
                "resolution": f"{w}x{h}",
                "freeze_count": 0,
                "total_freezes_duration_seconds": 0,
            },
        }
        for i, f in enumerate(frames)
    ]


def test_decoded_frames_count_as_playback():
    out = qoe.summarize(_call(list(range(0, 600, 25))), "meet")
    assert out["status"] == "ok"
    assert out["video_resolution_p"] == 360
    assert out["watched_seconds"] > 0  # the gate that mislabelled Meet
    assert out["session_seconds"] == 23.0
    assert out["total_video_frames"] == 575
    assert out["derivation"]["progress_basis"].startswith("frames_decoded")


def test_frame_rate_is_derived():
    # 25 new frames per 1s sample -> 25 fps.
    out = qoe.summarize(_call(list(range(0, 500, 25))), "meet")
    assert out["frame_rate_fps"] == 25.0
    assert out["derivation"]["fps_basis"].startswith("frames_decoded_delta")


def test_startup_is_time_to_first_decoded_frame():
    # Frames stay put for the first three intervals, then start advancing.
    out = qoe.summarize(_call([10, 10, 10, 10, 40, 70, 100]), "meet")
    assert out["video_startup_time_ms"] == 4000.0
    assert "first decoded frame" in out["derivation"]["startup_basis"]


def test_the_post_join_bitrate_spike_is_excluded():
    """First interval divides accumulated bytes by a tiny elapsed time."""
    out = qoe.summarize(
        _call(list(range(0, 300, 25)), bitrates=[38.663] + [1.1] * 11), "meet"
    )
    assert out["max_bitrate_mbps"] == 1.1  # not the 38.663 spike
    assert out["mean_bitrate_mbps"] == 1.1  # the mean must drop it too
    # max below mean is not a possible pair of numbers.
    assert out["max_bitrate_mbps"] >= out["mean_bitrate_mbps"]
    assert out["max_bitrate_is_startup_artifact"] is False
    assert "exclude the first post-join" in out["derivation"]["bitrate_caveat"]


def test_a_call_with_no_advancing_frames_is_not_playback():
    out = qoe.summarize(_call([7] * 8), "meet")
    assert out["watched_seconds"] == 0


def test_frozen_seconds_are_not_credited_as_watched():
    # Frames advance, stall for three intervals, then resume.
    out = qoe.summarize(_call([0, 25, 50, 50, 50, 50, 75, 100]), "meet")
    assert out["watched_seconds"] == 4.0
    assert out["session_seconds"] == 7.0


def test_session_start_epoch_is_exposed_for_plot_alignment():
    out = qoe.summarize(_call(list(range(0, 200, 25))), "meet")
    assert out["session_start_epoch"] == T0


def test_a_local_preview_with_no_remote_media_is_still_rejected():
    rows = _call([0, 25, 50])
    for r in rows:
        r["stats"]["bytes_received"] = 0
        r["stats"]["inbound_bitrate_mbps"] = 0
    out = qoe.summarize(rows, "meet")
    assert out["status"] != "ok"
