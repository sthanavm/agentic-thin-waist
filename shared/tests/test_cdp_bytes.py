"""Tests for the CDP byte ledger.

These validate the accounting logic against CDP messages shaped exactly as the
Network domain emits them. They do NOT validate that Chrome's performance log
actually delivers these events in our driver configuration -- that needs a
browser and is unverified until a real run.
"""

import pathlib
import sys

sys.path.insert(
    0,
    str(
        pathlib.Path(__file__).resolve().parents[2]
        / "services/orchestration/scripts/selenium_video_qoe"
    ),
)

from cdp_bytes import CdpByteLedger, host_of, matches  # noqa: E402

GV = ("googlevideo.com",)


def _sent(rid, url):
    return {
        "method": "Network.requestWillBeSent",
        "params": {"requestId": rid, "request": {"url": url}},
    }


def _data(rid, n):
    return {
        "method": "Network.dataReceived",
        "params": {"requestId": rid, "encodedDataLength": n},
    }


def _done(rid, n):
    return {
        "method": "Network.loadingFinished",
        "params": {"requestId": rid, "encodedDataLength": n},
    }


def test_bytes_are_grouped_by_host_suffix():
    led = CdpByteLedger()
    for m in [
        _sent("1", "https://rr2---sn-x.googlevideo.com/videoplayback?x=1"),
        _data("1", 500_000),
        _done("1", 500_000),
        _sent("2", "https://www.youtube.com/watch?v=x"),
        _data("2", 4_000_000),
        _done("2", 4_000_000),
    ]:
        led.feed(m)
    t = led.totals(GV)
    assert t["cdp_finished_bytes"] == 500_000
    assert t["cdp_received_bytes"] == 500_000
    assert t["cdp_finished_requests"] == 1


def test_a_lookalike_host_is_not_counted():
    assert matches("rr2.googlevideo.com", GV)
    assert matches("googlevideo.com", GV)
    assert not matches("evilgooglevideo.com", GV)
    assert not matches("googlevideo.com.attacker.net", GV)


def test_multiple_data_events_accumulate_for_one_request():
    led = CdpByteLedger()
    led.feed(_sent("1", "https://x.googlevideo.com/videoplayback"))
    for _ in range(4):
        led.feed(_data("1", 250_000))
    led.feed(_done("1", 1_000_000))
    t = led.totals(GV)
    assert t["cdp_received_bytes"] == 1_000_000
    assert t["cdp_finished_bytes"] == 1_000_000


def test_a_response_that_never_finishes_is_reported_as_open_not_lost():
    """The direct test of the never-finished-response explanation."""
    led = CdpByteLedger()
    led.feed(_sent("1", "https://x.googlevideo.com/a"))
    led.feed(_data("1", 900_000))
    led.feed(_done("1", 900_000))
    led.feed(_sent("2", "https://x.googlevideo.com/b"))
    led.feed(_data("2", 855_173))  # arrives, never completes
    t = led.totals(GV)
    assert t["cdp_received_bytes"] == 1_755_173
    assert t["cdp_finished_bytes"] == 900_000
    assert t["cdp_open_at_end_bytes"] == 855_173
    assert t["cdp_open_at_end_requests"] == 1


def test_bytes_arriving_before_the_url_is_known_are_not_dropped():
    led = CdpByteLedger()
    led.feed(_data("1", 700_000))  # reordered ahead of its request
    t_before = led.totals(GV)
    assert t_before["cdp_unresolved_bytes"] == 700_000
    assert t_before["cdp_received_bytes"] == 0
    led.feed(_sent("1", "https://x.googlevideo.com/a"))
    t_after = led.totals(GV)
    assert t_after["cdp_received_bytes"] == 700_000
    assert t_after["cdp_unresolved_bytes"] == 0


def test_response_received_can_supply_the_url():
    led = CdpByteLedger()
    led.feed(
        {
            "method": "Network.responseReceived",
            "params": {
                "requestId": "9",
                "response": {"url": "https://y.googlevideo.com/v"},
            },
        }
    )
    led.feed(_data("9", 123_456))
    assert led.totals(GV)["cdp_received_bytes"] == 123_456


def test_non_network_and_malformed_events_are_ignored_safely():
    led = CdpByteLedger()
    led.feed({"method": "Page.loadEventFired", "params": {}})
    led.feed({"method": "Network.dataReceived", "params": {}})
    led.feed({})
    t = led.totals(GV)
    assert t["cdp_received_bytes"] == 0
    assert t["cdp_events_ignored"] == 1


def test_host_of_handles_junk():
    assert host_of("not a url") is None
    assert host_of("https://A.GoogleVideo.COM/x") == "a.googlevideo.com"
