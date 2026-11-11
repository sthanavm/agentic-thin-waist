"""`media_bytes` must be absent where it is undefined, not zero.

`media_bytes` was reported as `{'meet': 0}` for every Meet run ever captured,
including the known-720p control whose pcap carries 29 MB of inbound video.
The tally matches a remote host NAME against MEDIA_HOST_SUFFIXES, and a
WebRTC call reaches endpoints chosen by ICE that are never named in DNS, so
there was never anything for it to match. A zero there is an undefined
measurement wearing the clothes of a measured absence -- the same shape as
the collector failure that produced a false claim about Meet at 0.5 Mb/s.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pramana_helpers as H  # noqa: E402


def test_an_app_with_named_media_hosts_is_in_scope():
    assert H.media_host_suffixes("youtube")
    assert H.media_attribution_apps(["youtube"]) == ["youtube"]


def test_a_webrtc_app_is_out_of_scope_rather_than_zero():
    # No suffixes are listed for meet, and none could be: there is no name.
    assert H.media_host_suffixes("meet") == ()
    assert H.media_attribution_apps(["meet"]) == []


def test_scope_is_per_app_not_per_capture():
    # A capture with both must keep the figure for the one it is defined for
    # and omit it for the other, rather than dropping or zeroing both.
    assert H.media_attribution_apps(["meet", "youtube"]) == ["youtube"]


def test_local_alias_attribution_has_no_remote_name_to_match():
    # Keyed on the local alias, there is no remote hostname for ANY app, so
    # the figure is undefined even for YouTube.
    assert H.media_attribution_apps(["youtube"], "local_alias_ip") == []


def test_zero_is_still_reachable_for_a_real_shortfall():
    # The point of the field: an in-scope app whose media host was never
    # named must still be able to report 0, because that IS the bug it was
    # added to catch. Scope must not swallow that case.
    assert "youtube" in H.media_attribution_apps(["youtube"], "remote_hostname")
