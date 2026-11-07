"""The double-r GGC edge: component update, or the video itself?

`rr<N>---sn-<id>.gvt1.com` was classified as Chrome's component updater by
NAME, on the strength of a single 2026-09-22 measurement. On 2026-11-05 YouTube
served a whole session from one of those edges and the name rule filed 15.6 MB
of video as `browser_infra`, which made the app look starved of 70-80% of its
own link on 9 CPUC runs and inverted their interference verdicts.

Both directions are pinned here, because a rule that only fixes the new case
would re-break the old one, and a rule that only keeps the old case is the bug.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pramana_helpers import (  # noqa: E402
    _GVT_GGC_RE,
    classify_host,
    ggc_role,
)

# The 2026-09-22 run: 973 kB in a 4-5 s burst at t=1..5 s, before page load.
UPDATE = dict(total_bytes=973_000, span_s=4.0, early_fraction=1.0)
# The 2026-11-05 run: 15.6 MB from t=0..67 s, 17.8% inside the first 10 s.
MEDIA = dict(total_bytes=15_610_000, span_s=67.0, early_fraction=0.178)

HOST = "rr5---sn-jxopj-nh4e.gvt1.com"
OLD_HOST = "rr5---sn-jxopj-n5oe.gvt1.com"


def test_the_documented_component_update_is_still_an_update():
    """The case the name rule was built from must not regress."""
    assert ggc_role(**UPDATE) == "update"


def test_the_session_long_abr_fetch_is_media():
    """The case the name rule got wrong."""
    assert ggc_role(**MEDIA) == "media"


def test_both_hosts_still_match_the_candidate_pattern():
    """Shape decides, but the NAME is still what selects candidates.

    If the regex stopped matching, the shape test would never run and every
    such flow would fall through to whatever the generic rules say.
    """
    assert _GVT_GGC_RE.search(HOST)
    assert _GVT_GGC_RE.search(OLD_HOST)


def test_classify_host_follows_the_shape_verdict():
    apps = ["youtube"]
    assert classify_host(HOST, apps) == "browser_infra"
    assert classify_host(HOST, apps, ggc_media_hosts={HOST}) == "youtube"
    # A host the shape test did NOT clear stays infra even when another one did.
    assert classify_host(OLD_HOST, apps, ggc_media_hosts={HOST}) == "browser_infra"


def test_single_r_media_edge_is_untouched():
    """The single-r form was never in question and must not start depending on
    the shape test, which only ever sees double-r candidates."""
    assert classify_host("r5---sn-jxopj-nh4e.gvt1.com", ["youtube"]) == "youtube"


def test_a_large_early_burst_is_not_media():
    """Volume alone must not promote an update.

    A 20 MB component download arriving entirely in the first seconds is still
    not the video, so size cannot be the only test.
    """
    assert ggc_role(20_000_000, 6.0, 0.98) == "update"


def test_a_long_thin_trickle_is_not_media():
    """Duration alone must not promote an update.

    Background polling can touch the same edge for the whole run without ever
    being a video, so span cannot be the only test.
    """
    assert ggc_role(400_000, 90.0, 0.1) == "update"


def test_a_late_tiny_flow_is_not_media():
    """Early-fraction alone must not promote an update."""
    assert ggc_role(100_000, 80.0, 0.0) == "update"


def test_the_rule_is_deterministic():
    """Same inputs, same answer -- the verdict must not depend on anything
    outside its arguments, or re-deriving a capture could reclassify it."""
    for _ in range(50):
        assert ggc_role(**MEDIA) == "media"
        assert ggc_role(**UPDATE) == "update"


def test_classification_does_not_depend_on_set_iteration_order():
    """`ggc_media_hosts` is a set; the answer must not depend on its order."""
    hosts = {HOST, OLD_HOST, "rr9---sn-aaaa-bbbb.gvt1.com"}
    first = classify_host(HOST, ["youtube"], ggc_media_hosts=hosts)
    for _ in range(20):
        assert classify_host(HOST, ["youtube"], ggc_media_hosts=set(hosts)) == first
