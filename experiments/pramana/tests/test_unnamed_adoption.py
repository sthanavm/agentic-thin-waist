"""Adoption of unnamed media flows into an app bucket.

Media endpoints are handed to the client inside encrypted signalling, so they
appear in the capture with no DNS answer and no TLS SNI. Left in `other` they
made an app look idle for most of its run. These tests pin both halves of the
rule: the two signals that legitimately claim a flow, and -- the part that keeps
the heuristic honest -- every case that must stay unattributed.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pramana_helpers import adopt_unnamed_ips  # noqa: E402

BIG = 5_000_000  # comfortably over the byte floor


def test_prefix_24_claims_a_sibling_cdn_address():
    """The real YouTube case: unnamed .13 beside named .17 in one /24."""
    out = adopt_unnamed_ips(
        {"198.189.66.13": (BIG, 7.1, 88.8)},
        {"youtube": {"198.189.66.17", "142.251.151.4"}},
        {"youtube": (1.0, 88.8)},
    )
    assert out == {"198.189.66.13": "youtube:prefix/24"}


def test_timing_claims_a_relay_that_shares_no_prefix():
    """The real Meet case: 74.125.x relay, meet.google.com is 142.251.x."""
    out = adopt_unnamed_ips(
        {"74.125.250.130": (BIG, 12.4, 96.2)},
        {"meet": {"142.251.156.5"}},
        {"meet": (3.5, 95.5)},
    )
    assert out == {"74.125.250.130": "meet:timing"}


def test_two_concurrent_apps_leave_the_flow_unattributed():
    """A concurrent run must not guess; an unclaimed flow stays in `other`."""
    out = adopt_unnamed_ips(
        {"203.0.113.9": (BIG, 5.0, 90.0)},
        {"youtube": {"142.251.151.4"}, "vimeo": {"162.159.130.234"}},
        {"youtube": (1.0, 95.0), "vimeo": (2.0, 94.0)},
    )
    assert out == {}


def test_prefix_still_disambiguates_a_concurrent_run():
    """Timing is ambiguous, but only one app owns the /24 — so it is claimable."""
    out = adopt_unnamed_ips(
        {"198.189.66.13": (BIG, 5.0, 90.0)},
        {"youtube": {"198.189.66.17"}, "vimeo": {"162.159.130.234"}},
        {"youtube": (1.0, 95.0), "vimeo": (2.0, 94.0)},
    )
    assert out == {"198.189.66.13": "youtube:prefix/24"}


def test_small_flows_are_ignored():
    """A stray exchange is not evidence of anything."""
    out = adopt_unnamed_ips(
        {"203.0.113.9": (4_096, 10.0, 90.0)},
        {"meet": {"142.251.156.5"}},
        {"meet": (3.5, 95.5)},
    )
    assert out == {}


def test_a_short_burst_is_not_adopted_by_timing():
    """Chrome's own background downloads are bursts, not sustained sessions."""
    out = adopt_unnamed_ips(
        {"203.0.113.9": (BIG, 6.0, 9.0)},  # 3s inside a 92s window
        {"meet": {"142.251.156.5"}},
        {"meet": (3.5, 95.5)},
    )
    assert out == {}


def test_a_flow_outside_the_app_window_is_not_adopted():
    """Sustained, but it ran before the app did — it is somebody else's."""
    out = adopt_unnamed_ips(
        {"203.0.113.9": (BIG, 0.0, 30.0)},
        {"meet": {"142.251.156.5"}},
        {"meet": (60.0, 150.0)},
    )
    assert out == {}


def test_no_named_traffic_means_nothing_to_anchor_to():
    assert adopt_unnamed_ips({"203.0.113.9": (BIG, 1.0, 90.0)}, {}, {}) == {}
