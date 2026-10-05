"""The gate that stops a conferencing cell measuring the wrong <video>.

Three sweeps were lost to element selection before this existed. The last one
measured the collector's OWN camera - a 1280x720 fake device rendered into a
207x117 self-view thumbnail - in all 168 samples of a cell, and reported 0.0%
dropped frames because nothing was crossing a network at all.

The gate is deliberately narrow: the measured element must be a remote stream,
and it must be laid out at main-view size. The threshold comes from the runs that
demonstrably did measure the peer (1756x988).
"""

import pathlib
import time

import pytest

SRC = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "selenium_video_qoe"
    / "collect.py"
)

# Lift the gate out of the source: collect.py imports selenium at module scope.
_NS: dict = {"time": time, "Any": object}
_text = SRC.read_text()
_start = _text.index("REMOTE_TILE_REFERENCE_AREA")
_end = _text.index("def run_job(")
exec(
    "import os, time\nfrom typing import Any\n" + _text[_start:_end],
    _NS,
)
assert_remote_tile = _NS["_assert_remote_tile"]
RemoteTileMissing = _NS["RemoteTileMissing"]
MIN_AREA = _NS["REMOTE_TILE_MIN_AREA"]

JOB = {"require_remote_tile": True}
LONG_AGO = 0.0  # grace period already elapsed


def sample(stream, box, area, local, origin="remote-stream"):
    return {
        "stats": {
            "measured_element": {
                "stream": stream,
                "box": box,
                "box_area": area,
                "origin": origin,
                "label": "",
            },
            "measured_is_local": local,
            "measured_box_area": area,
            "video_remote_decoded_count": 0 if local else 1,
        }
    }


def test_the_self_view_that_fooled_the_last_sweep_is_refused():
    """1280x720 fake camera in a 207x117 box: the exact failing case."""
    s = [sample("1280x720", "207x117", 207 * 117, True, "local-capture")]
    with pytest.raises(RemoteTileMissing) as e:
        assert_remote_tile("zoom", JOB, s, LONG_AGO)
    assert "LOCAL self-view" in str(e.value)


def test_a_remote_tile_too_small_to_be_the_main_view_is_refused():
    s = [sample("320x180", "207x117", 207 * 117, False)]
    with pytest.raises(RemoteTileMissing) as e:
        assert_remote_tile("zoom", JOB, s, LONG_AGO)
    assert "too small" in str(e.value)


def test_the_earlier_good_layout_passes():
    """1756x988 is the reference: the runs that did measure the peer."""
    s = [sample("1280x720", "1756x988", 1756 * 988, False)]
    assert_remote_tile("zoom", JOB, s, LONG_AGO)


def test_the_same_view_in_a_different_layout_passes():
    """A 1458x820 main view is the same thing, downscaled by Zoom's layout.

    This is why the floor is a fraction of the reference rather than equality:
    a strict match would reject a perfectly good measurement.
    """
    s = [sample("320x180", "1458x820", 1458 * 820, False)]
    assert_remote_tile("zoom", JOB, s, LONG_AGO)


def test_the_threshold_sits_between_the_two_observed_cases():
    assert 207 * 117 < MIN_AREA < 1458 * 820


def test_nothing_is_checked_during_the_grace_period():
    """The page needs time to lay the tile out; failing instantly would refuse
    every cell before it had a chance to join."""
    s = [sample("1280x720", "207x117", 207 * 117, True, "local-capture")]
    assert_remote_tile("zoom", JOB, s, time.time())  # grace not yet elapsed


def test_apps_that_do_not_need_a_peer_are_untouched():
    """Vimeo and YouTube have no remote tile; the gate must not apply to them."""
    s = [sample("1440x1080", "1317x988", 1317 * 988, None, "no-stream")]
    assert_remote_tile("vimeo", {}, s, LONG_AGO)


def test_a_stream_with_no_mediastream_is_not_treated_as_local():
    """local is None for a file/MSE player - None must not read as True."""
    s = [sample("1440x1080", "1317x988", 1317 * 988, None, "no-stream")]
    assert_remote_tile("vimeo", JOB, s, LONG_AGO)


def test_no_samples_at_all_is_refused():
    with pytest.raises(RemoteTileMissing) as e:
        assert_remote_tile("zoom", JOB, [], LONG_AGO)
    assert "no player samples" in str(e.value)


def test_the_latest_sample_decides_not_the_first():
    """A cell that starts on a thumbnail and moves to the main view is fine."""
    s = [
        sample("1280x720", "207x117", 207 * 117, True, "local-capture"),
        sample("320x180", "1458x820", 1458 * 820, False),
    ]
    assert_remote_tile("zoom", JOB, s, LONG_AGO)
