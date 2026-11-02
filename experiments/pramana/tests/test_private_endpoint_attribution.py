"""Attribution when the far end is RFC1918, i.e. something we run ourselves.

The conferencing smoke run measured 1.5% of bytes attributed. Both endpoints
were inside our own namespaces -- the browser on 172.16.1.1 in ns1, the WebRTC
peer server on 172.16.2.1 in ns2 -- and the locality rule ("a private address
carrying >=10% of packets is us") claimed both. With both ends local, `remote`
pointed at a local address that no DNS answer or SNI ever names, so every
packet fell into `other`.

IRTT and NetVibe both run server-side inside those same namespaces, so the
same rule would mis-attribute them. These tests pin the three parts of the
fix: declared locality wins over inference, declared peers are never treated
as the vantage, and a capture that still has both ends local says so loudly
instead of reporting a plausible-looking split.
"""

import json
import pathlib
import struct
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pramana_helpers import (  # noqa: E402
    analyze_pcap_netstats,
    app_endpoint_ips_from_url_map,
    app_endpoint_ips_from_webrtc_stats,
    attribute_capture,
    client_ips_for_capture,
    merge_endpoint_ips,
)

_LT_ETHERNET = 1


def _ipv4(src: str, dst: str, payload_len: int, proto: int = 17) -> bytes:
    """One Ethernet-framed IPv4 packet with `payload_len` bytes of UDP body."""
    sport, dport = 40000, 2112
    udp = struct.pack(">HHHH", sport, dport, 8 + payload_len, 0) + b"\x00" * payload_len
    total = 20 + len(udp)
    ip = struct.pack(
        ">BBHHHBBH4s4s",
        0x45,
        0,
        total,
        0,
        0,
        64,
        proto,
        0,
        bytes(int(o) for o in src.split(".")),
        bytes(int(o) for o in dst.split(".")),
    )
    eth = b"\x02" * 6 + b"\x03" * 6 + struct.pack(">H", 0x0800)
    return eth + ip + udp


def _write_pcap(path: pathlib.Path, packets: list[tuple[float, bytes]]) -> None:
    with open(path, "wb") as fh:
        fh.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, _LT_ETHERNET))
        for ts, raw in packets:
            sec = int(ts)
            usec = int(round((ts - sec) * 1e6))
            fh.write(struct.pack("<IIII", sec, usec, len(raw), len(raw)))
            fh.write(raw)


CLIENT = "172.16.1.1"  # browser, ns1
PEER = "172.16.2.1"  # a server we run, ns2
PUBLIC = "142.251.151.4"  # a CDN, for the mixed case


@pytest.fixture
def in_topology_pcap(tmp_path):
    """60 s of two-way traffic between ns1 and ns2, both RFC1918.

    Download is deliberately larger than upload so a direction mix-up is
    visible in the numbers rather than only in a label.
    """
    packets = []
    for second in range(60):
        for _ in range(10):
            packets.append((100.0 + second, _ipv4(PEER, CLIENT, 1000)))
        packets.append((100.0 + second, _ipv4(CLIENT, PEER, 100)))
    pcap = tmp_path / "capture.pcap"
    _write_pcap(pcap, packets)
    return pcap


def test_declared_client_makes_an_rfc1918_peer_attributable(in_topology_pcap):
    """The smoke-run case: ns1 browser, ns2 peer, nothing public involved."""
    at = attribute_capture(
        in_topology_pcap,
        ["webrtc_peer"],
        cap_mbps=6.0,
        app_endpoint_ips={"webrtc_peer": {PEER}},
        client_ips=[CLIENT],
    )
    assert at.local_ip_source == "declared_client_ips"
    assert at.local_ips == [CLIENT]
    # Everything belongs to the app: there is no other traffic in this capture.
    assert at.attributed_fraction == pytest.approx(1.0)
    assert at.bucket_mb.get("other", 0.0) == 0.0
    assert at.bucket_mb.get("intra_local", 0.0) == 0.0
    assert at.app_ips["webrtc_peer"] == [PEER]


def test_direction_is_not_reversed_for_an_rfc1918_peer(in_topology_pcap):
    """Download must be the peer->client leg, which is the larger one here."""
    at = attribute_capture(
        in_topology_pcap,
        ["webrtc_peer"],
        cap_mbps=6.0,
        app_endpoint_ips={"webrtc_peer": {PEER}},
        client_ips=[CLIENT],
    )
    down = at.per_app["webrtc_peer"]["download"]
    up = at.per_app["webrtc_peer"]["upload"]
    assert down.packets == 600
    assert up.packets == 60
    assert down.total_mb > up.total_mb


def test_a_declared_peer_is_never_claimed_as_the_vantage(in_topology_pcap):
    """Ground truth about the far end survives inference, without client_ips.

    This is the half that broke silently: the peer's address was dropped from
    the ground-truth seed precisely *because* inference had already claimed it
    as local.
    """
    at = attribute_capture(
        in_topology_pcap,
        ["webrtc_peer"],
        cap_mbps=6.0,
        app_endpoint_ips={"webrtc_peer": {PEER}},
    )
    assert at.local_ip_source == "inferred_private_majority"
    assert PEER not in at.local_ips
    assert at.local_ips == [CLIENT]
    assert at.attributed_fraction == pytest.approx(1.0)


def test_both_ends_local_is_reported_not_quietly_split(in_topology_pcap):
    """With no ground truth at all the old collapse still happens -- loudly."""
    at = attribute_capture(in_topology_pcap, ["webrtc_peer"], cap_mbps=6.0)
    assert sorted(at.local_ips) == [CLIENT, PEER]
    # Nothing is attributed, and the reason is stated rather than inferable
    # only from a suspiciously low percentage.
    assert at.attributed_fraction == 0.0
    assert at.bucket_mb.get("intra_local", 0.0) > 0.0
    assert "both held to be local" in at.note
    assert "client_ips" in at.note


def test_contradictory_declarations_raise_rather_than_pick_one(in_topology_pcap):
    """An address cannot be both the vantage and the far end."""
    with pytest.raises(ValueError, match="declared BOTH"):
        attribute_capture(
            in_topology_pcap,
            ["webrtc_peer"],
            cap_mbps=6.0,
            app_endpoint_ips={"webrtc_peer": {CLIENT}},
            client_ips=[CLIENT],
        )


def test_public_endpoint_attribution_is_unchanged(tmp_path):
    """The ordinary streaming case must behave exactly as before the fix.

    Declaring the vantage is a no-op when only one endpoint is private, which
    is what makes the change safe for the existing streaming runs.
    """
    packets = []
    for second in range(30):
        for _ in range(10):
            packets.append((50.0 + second, _ipv4(PUBLIC, CLIENT, 1200)))
        packets.append((50.0 + second, _ipv4(CLIENT, PUBLIC, 80)))
    pcap = tmp_path / "capture.pcap"
    _write_pcap(pcap, packets)

    inferred = attribute_capture(pcap, ["youtube"], cap_mbps=6.0)
    declared = attribute_capture(pcap, ["youtube"], cap_mbps=6.0, client_ips=[CLIENT])

    assert inferred.local_ips == declared.local_ips == [CLIENT]
    assert inferred.bucket_mb == declared.bucket_mb
    assert inferred.total_mb == declared.total_mb
    assert inferred.attributed_fraction == declared.attributed_fraction


def test_netstats_does_not_count_both_directions_as_download(in_topology_pcap):
    """Shaping is checked against a download cap, so download must be download.

    Inferring locality claims both ns1 and ns2, which made every packet a
    download and roughly doubled the measured rate.
    """
    declared = analyze_pcap_netstats(in_topology_pcap, 6.0, client_ips=[CLIENT])
    inferred = analyze_pcap_netstats(in_topology_pcap, 6.0)

    # 600 download packets x 1042 B on the wire = 5_001_600 bits, spread over
    # the 59 s between the first and last timestamp => 0.0848 Mbps.
    assert declared.avg_throughput_mbps == pytest.approx(0.085, abs=0.002)
    assert declared.note == ""
    # Without the declaration no download direction can be isolated at all,
    # which is now stated instead of being reported as twice the real rate.
    assert inferred.avg_throughput_mbps is None
    assert "both held to be local" in inferred.note
    # The safety property: an unmeasurable capture must not pass the shaping
    # check. A reported 0.0 Mbps would have compared as "under the cap".
    assert inferred.shaping_verified is False
    assert inferred.delivered_fraction is None
    assert inferred.peak_throughput_mbps is None


def test_negotiated_address_is_preferred_over_the_url(tmp_path):
    """ICE picks its own transport address; the URL's host is not the media path."""
    qoe = tmp_path / "qoe"
    qoe.mkdir()
    samples = [
        {"timestamp": 1.0, "stats": {"selected_remote_address": PEER}},
        {"timestamp": 2.0, "stats": {"selected_remote_address": PEER}},
    ]
    (qoe / "webrtc_peer_stats.jsonl").write_text(
        "\n".join(json.dumps(s) for s in samples)
    )

    negotiated = app_endpoint_ips_from_webrtc_stats(tmp_path, ["webrtc_peer"])
    assert negotiated == {"webrtc_peer": {PEER}}

    # The signalling URL named a *different* address, as it does in a real run.
    from_url = app_endpoint_ips_from_url_map({"webrtc_peer": "http://172.16.3.1:8080/"})
    merged = merge_endpoint_ips(from_url, negotiated)
    assert merged == {"webrtc_peer": {PEER, "172.16.3.1"}}


def test_mdns_candidate_name_is_skipped_not_treated_as_an_address(tmp_path, capsys):
    """Chrome may publish `<uuid>.local` instead of an address; it cannot match."""
    qoe = tmp_path / "qoe"
    qoe.mkdir()
    (qoe / "meet_stats.jsonl").write_text(
        json.dumps(
            {"stats": {"selected_remote_address": "abcd-1234.local"}},
        )
    )
    out = app_endpoint_ips_from_webrtc_stats(tmp_path, ["meet"])
    assert out == {}
    assert "mDNS" in capsys.readouterr().out


def test_missing_stats_file_is_not_an_error(tmp_path):
    """A streaming app has no ICE stats at all; that is normal, not a failure."""
    assert app_endpoint_ips_from_webrtc_stats(tmp_path, ["youtube"]) == {}


def test_client_ips_override_rejects_a_non_address(monkeypatch):
    """A typo must fail loudly: direction attribution depends on this exactly."""
    monkeypatch.setenv("PRAMANA_CLIENT_IPS", "172.16.1.1,not-an-ip")
    with pytest.raises(ValueError, match="not an IP address"):
        client_ips_for_capture()


def test_client_ips_override_is_honoured(monkeypatch):
    monkeypatch.setenv("PRAMANA_CLIENT_IPS", "10.1.2.3 , 10.1.2.4")
    assert client_ips_for_capture() == ["10.1.2.3", "10.1.2.4"]
