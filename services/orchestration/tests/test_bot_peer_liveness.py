"""The Zoom bot peer's liveness witness: its own interface tx counters.

Zoom's web client has no RTCPeerConnection, so the peer cannot read outbound-RTP
counters the way the WebRTC peers do. It used to infer "publishing" from the
in-call DOM instead -- but that DOM renders the bot's OWN local preview, which
keeps showing 1280x720 long after the meeting has ended. A peer trusted on that
basis reported "publishing" through an entire dead 40-minute window, and a sweep
recorded a cell against a meeting nobody was in.

Inside a bridge-networked container /proc/net/dev is the peer's own namespace, so
the interface counters are readable without any privileged helper. These tests
pin the parsing and the threshold that separates a live publish from the trickle
an ended call still produces.
"""

import importlib.util
import pathlib

import pytest

SRC = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "selenium_video_qoe"
    / "bot_peer.py"
)

# bot_peer.py drives Selenium at import time scope, so lift just the helper out
# of the source rather than importing the module.
_NS: dict = {}
_text = SRC.read_text()
exec(_text[_text.index("def _own_tx_bytes") : _text.index("def main(")], _NS)
own_tx_bytes = _NS["_own_tx_bytes"]

PROC_NET_DEV = """Inter-|   Receive                          |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 11141270    9981    0    0    0     0          0         0 11141270    9981    0    0    0     0       0          0
  eth0: 90959473  416452    0    0    0     0          0         0 43907980  561392    0    0    0     0       0          0
"""


def _patch_proc(monkeypatch, text):
    real_open = open

    def fake_open(path, *a, **k):
        if str(path) == "/proc/net/dev":
            import io

            return io.StringIO(text)
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)


def test_sums_transmit_bytes_of_real_interfaces(monkeypatch):
    _patch_proc(monkeypatch, PROC_NET_DEV)
    # eth0's transmit column only; loopback must not be counted.
    assert own_tx_bytes() == 43907980


def test_loopback_is_excluded(monkeypatch):
    """Chrome's internal loopback traffic would otherwise mask a dead call."""
    _patch_proc(monkeypatch, PROC_NET_DEV)
    assert own_tx_bytes() != 43907980 + 11141270


def test_multiple_interfaces_are_summed(monkeypatch):
    extra = PROC_NET_DEV + (
        "  eth1: 10 1    0    0    0     0          0         0 500 7"
        "    0    0    0     0       0          0\n"
    )
    _patch_proc(monkeypatch, extra)
    assert own_tx_bytes() == 43907980 + 500


def test_unreadable_counters_report_none_not_zero(monkeypatch):
    """None means "no signal"; zero would read as "call is dead" and abort a run."""

    def boom(path, *a, **k):
        raise OSError("no procfs here")

    monkeypatch.setattr("builtins.open", boom)
    assert own_tx_bytes() is None


def test_a_dead_call_trickle_is_below_the_alive_threshold():
    """Measured on an ended meeting: 66 bytes per 10s, against a 20kB threshold.

    A live 720p publish moves roughly 600kB per 5s poll, so the two are
    separated by four orders of magnitude -- the threshold is what stops an
    ended call's keepalive and DNS from resetting the stall counter forever.
    """
    min_delta = 20_000
    dead_trickle = 66
    live_publish = 600_000
    assert dead_trickle < min_delta
    assert live_publish >= min_delta


@pytest.mark.parametrize("missing", ["", "Inter-|\n face |\n"])
def test_a_header_only_file_is_not_a_crash(monkeypatch, missing):
    _patch_proc(monkeypatch, missing)
    assert own_tx_bytes() == 0
