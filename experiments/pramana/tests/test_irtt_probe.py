"""IRTT as a probe: measured, named, and kept out of the app's numbers.

IRTT shares the shaped link with the app under test, so its packets are in the
same capture. It is not an app: it has no player, no QoE and no per-app pcap,
and it must never be summed into the shared-cap total that J1/J2 test.

The flow is the awkward shape for attribution: an RFC1918 endpoint, no DNS
answer, no TLS SNI, and a time span that overlaps the app's run exactly. That
is precisely what `adopt_unnamed_ips` claims by timing when a run has one app
-- so without a declaration the probe's traffic is handed to the browser. The
first test below is that failure; the rest pin the fix.
"""

import json
import pathlib
import struct
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pramana_helpers import (  # noqa: E402
    attribute_capture,
    irtt_probe_endpoints,
    summarize_irtt,
)

_LT_ETHERNET = 1

CLIENT = "172.16.1.1"  # ns1, the browser and the irtt client
IRTT_SERVER = "172.16.2.1"  # ns2, the irtt server
CDN = "142.251.151.4"  # the app's endpoint, public


def _pkt(src: str, dst: str, payload: int, proto: int = 17) -> bytes:
    body = b"\x00" * payload
    if proto == 17:
        l4 = struct.pack(">HHHH", 40000, 2112, 8 + payload, 0) + body
    else:
        l4 = struct.pack(">HHIIBBHHH", 40000, 443, 0, 0, 0x50, 0x10, 0, 0, 0) + body
    ip = struct.pack(
        ">BBHHHBBH4s4s",
        0x45,
        0,
        20 + len(l4),
        0,
        0,
        64,
        proto,
        0,
        bytes(int(o) for o in src.split(".")),
        bytes(int(o) for o in dst.split(".")),
    )
    return b"\x02" * 6 + b"\x03" * 6 + struct.pack(">H", 0x0800) + ip + l4


def _write_pcap(path, packets) -> None:
    with open(path, "wb") as fh:
        fh.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, _LT_ETHERNET))
        for ts, raw in packets:
            sec = int(ts)
            fh.write(
                struct.pack(
                    "<IIII", sec, int(round((ts - sec) * 1e6)), len(raw), len(raw)
                )
            )
            fh.write(raw)


@pytest.fixture
def youtube_plus_irtt(tmp_path):
    """A browser app on a public CDN plus an IRTT probe to ns2, overlapping.

    The probe is small and steady (10 packets/s each way, ~1.2 kB/s) and the
    app is large and bursty, matching the real ratio.
    """
    packets = []
    for second in range(60):
        t = 100.0 + second
        for _ in range(20):  # app download
            packets.append((t, _pkt(CDN, CLIENT, 1400, proto=6)))
        packets.append((t, _pkt(CLIENT, CDN, 60, proto=6)))  # app upload/ACK
        for _ in range(10):  # probe, both directions
            packets.append((t, _pkt(CLIENT, IRTT_SERVER, 60)))
            packets.append((t, _pkt(IRTT_SERVER, CLIENT, 60)))
    pcap = tmp_path / "capture.pcap"
    _write_pcap(pcap, packets)
    return pcap


def test_undeclared_slow_probe_is_unattributed_not_adopted(youtube_plus_irtt):
    """At the default rate `adopt_unnamed_ips`'s byte floor already saves us.

    `_ADOPT_MIN_BYTES` is 256 KB and its comment says "ignore stray probes", so
    a 100 ms probe is below it. The flow is not handed to the app -- but it is
    not named either: it sits in `other`, indistinguishable from genuinely
    unknown traffic, and drags `attributed_fraction` down.
    """
    at = attribute_capture(
        youtube_plus_irtt,
        ["youtube"],
        cap_mbps=6.0,
        client_ips=[CLIENT],
        app_endpoint_ips={"youtube": {CDN}},
    )
    assert at.adopted_ips == {}
    assert IRTT_SERVER not in at.app_ips["youtube"]
    assert at.bucket_mb.get("other", 0.0) > 0.0
    assert at.attributed_fraction < 1.0


def test_undeclared_fast_probe_is_adopted_into_the_app(tmp_path):
    """The hazard, and why the floor is not the safeguard it looks like.

    The margin is 4%: a 100 ms probe over a 120 s run is ~245 KB against a
    256 KB floor. Raise the rate, lengthen the run, or point a heavier probe at
    the link and the floor stops protecting anything -- the flow is unnamed,
    RFC1918 and exactly time-overlapped, so adoption hands it to the only app
    in the run. Declaring the probe is what makes the split deterministic
    instead of a function of probe rate and run length.
    """
    packets = []
    for second in range(60):
        t = 100.0 + second
        for _ in range(20):
            packets.append((t, _pkt(CDN, CLIENT, 1400, proto=6)))
        for _ in range(100):  # 10 ms probe: comfortably over the floor
            packets.append((t, _pkt(CLIENT, IRTT_SERVER, 60)))
            packets.append((t, _pkt(IRTT_SERVER, CLIENT, 60)))
    pcap = tmp_path / "capture.pcap"
    _write_pcap(pcap, packets)

    undeclared = attribute_capture(
        pcap,
        ["youtube"],
        cap_mbps=6.0,
        client_ips=[CLIENT],
        app_endpoint_ips={"youtube": {CDN}},
    )
    assert IRTT_SERVER in undeclared.adopted_ips
    assert undeclared.adopted_ips[IRTT_SERVER].startswith("youtube:")
    # The probe's bytes are now inside the app's own figures, which is what
    # would reach the shared-cap sum that J1/J2 test.
    assert IRTT_SERVER in undeclared.app_ips["youtube"]

    declared = attribute_capture(
        pcap,
        ["youtube"],
        cap_mbps=6.0,
        client_ips=[CLIENT],
        app_endpoint_ips={"youtube": {CDN}},
        probe_endpoint_ips={"irtt": {IRTT_SERVER}},
    )
    assert declared.adopted_ips == {}
    assert IRTT_SERVER not in declared.app_ips["youtube"]
    assert declared.bucket_mb.get("irtt", 0.0) > 0.0


def test_declared_probe_gets_its_own_bucket(youtube_plus_irtt):
    at = attribute_capture(
        youtube_plus_irtt,
        ["youtube"],
        cap_mbps=6.0,
        client_ips=[CLIENT],
        app_endpoint_ips={"youtube": {CDN}},
        probe_endpoint_ips={"irtt": {IRTT_SERVER}},
    )
    assert at.bucket_mb.get("irtt", 0.0) > 0.0
    assert at.probe_ips == {"irtt": [IRTT_SERVER]}
    # Named, so it is attributed rather than sitting in `other`. This is the
    # difference the declaration makes at the default probe rate, where the
    # adoption floor already kept it out of the app: `other` -> `irtt`.
    assert at.bucket_mb.get("other", 0.0) == 0.0
    assert at.attributed_fraction == pytest.approx(1.0)


def test_declared_probe_is_never_adopted(youtube_plus_irtt):
    """The guard: a declared probe is not offered to the adoption pass."""
    at = attribute_capture(
        youtube_plus_irtt,
        ["youtube"],
        cap_mbps=6.0,
        client_ips=[CLIENT],
        app_endpoint_ips={"youtube": {CDN}},
        probe_endpoint_ips={"irtt": {IRTT_SERVER}},
    )
    assert at.adopted_ips == {}
    assert IRTT_SERVER not in at.app_ips["youtube"]


def test_probe_is_excluded_from_the_apps_own_numbers(youtube_plus_irtt):
    """The app's bytes must be the app's, with the probe neither added nor lost."""
    declared = attribute_capture(
        youtube_plus_irtt,
        ["youtube"],
        cap_mbps=6.0,
        client_ips=[CLIENT],
        app_endpoint_ips={"youtube": {CDN}},
        probe_endpoint_ips={"irtt": {IRTT_SERVER}},
    )
    # 20 x 1454 B on the wire per second for 60 s, download only.
    down = declared.per_app["youtube"]["download"]
    assert down.packets == 1200
    assert down.total_mb == pytest.approx(1.745, abs=0.01)
    # The probe is not an app, so it has no per-app series at all.
    assert "irtt" not in declared.per_app
    # Every byte is still accounted for somewhere.
    assert sum(declared.bucket_mb.values()) == pytest.approx(
        declared.total_mb, abs=0.01
    )


def test_probe_endpoint_is_not_mistaken_for_the_vantage(youtube_plus_irtt):
    """A probe's far end is a peer, so inference must not claim it as local."""
    at = attribute_capture(
        youtube_plus_irtt,
        ["youtube"],
        cap_mbps=6.0,
        app_endpoint_ips={"youtube": {CDN}},
        probe_endpoint_ips={"irtt": {IRTT_SERVER}},
    )
    assert IRTT_SERVER not in at.local_ips
    assert at.local_ips == [CLIENT]


def test_no_probe_declared_is_identical_to_before(tmp_path):
    """A run without the probe must attribute exactly as it always did."""
    packets = []
    for second in range(30):
        for _ in range(10):
            packets.append((50.0 + second, _pkt(CDN, CLIENT, 1200, proto=6)))
    pcap = tmp_path / "capture.pcap"
    _write_pcap(pcap, packets)
    kw = dict(cap_mbps=6.0, client_ips=[CLIENT], app_endpoint_ips={"youtube": {CDN}})
    a = attribute_capture(pcap, ["youtube"], **kw)
    b = attribute_capture(pcap, ["youtube"], probe_endpoint_ips={}, **kw)
    assert a.bucket_mb == b.bucket_mb
    assert a.app_ips == b.app_ips
    assert a.attributed_fraction == b.attributed_fraction


# ── the record ──────────────────────────────────────────────────────────────
# Values taken from a real 0.9.0 run across ns1→ns2 on this VM, so the schema
# and the units are the ones the binary actually emits, not invented ones.
REAL_IRTT_JSON = {
    "version": {"irtt": "0.9.0"},
    "stats": {
        "packets_sent": 39,
        "packets_received": 39,
        "packet_loss_percent": 0.0,
        "upstream_loss_percent": 0.0,
        "downstream_loss_percent": 0.0,
        "duplicates": 0,
        "late_packets": 0,
        "bytes_sent": 2340,
        "bytes_received": 2340,
        "duration": 3952000000,
        "rtt": {
            "min": 50428280,
            "mean": 50667410,
            "median": 50661000,
            "max": 50793498,
            "stddev": 91234,
        },
        "send_delay": {"mean": 25300000},
        "receive_delay": {"mean": 25360000},
        "ipdv_round_trip": {"mean": 70000},
        "ipdv_send": {"mean": 31000},
        "ipdv_receive": {"mean": 42000},
        "timer_err_percent": 0.21,
        "timer_miss_percent": 0.0,
        "timer_misses": 0,
    },
}


def test_summary_converts_nanoseconds_to_milliseconds():
    """Every other latency here is in ms; a bare ns number would be misread."""
    out = summarize_irtt(
        {
            "transport": "worker_endpoint",
            "server_address": IRTT_SERVER,
            "port": 2112,
            "endpoint_ips": [IRTT_SERVER],
            "client_returncode": 0,
            "result": REAL_IRTT_JSON,
        }
    )
    assert out["status"] == "ok"
    assert out["round_trip_ms"]["mean"] == pytest.approx(50.6674, abs=1e-4)
    assert out["round_trip_ms"]["min"] == pytest.approx(50.4283, abs=1e-4)
    assert out["one_way_ms"]["send_mean"] == pytest.approx(25.3, abs=1e-3)
    assert out["jitter_ms"]["round_trip_mean"] == pytest.approx(0.07, abs=1e-3)
    assert out["duration_ms"] == pytest.approx(3952.0, abs=0.1)
    assert out["packets"] == {
        "sent": 39,
        "received": 39,
        "loss_percent": 0.0,
        "upstream_loss_percent": 0.0,
        "downstream_loss_percent": 0.0,
        "duplicates": 0,
        "late": 0,
    }
    assert out["irtt_version"] == "0.9.0"


def test_summary_reports_sender_timing():
    """If the sender missed its schedule the numbers describe it, not the link."""
    out = summarize_irtt({"result": REAL_IRTT_JSON, "endpoint_ips": [IRTT_SERVER]})
    assert out["timer"] == {"error_percent": 0.21, "miss_percent": 0.0, "misses": 0}


def test_summary_of_a_failed_probe_says_so():
    """No result must read as failed, never as a zero-latency success."""
    out = summarize_irtt(
        {
            "transport": "host_nsenter",
            "endpoint_ips": [IRTT_SERVER],
            "client_returncode": 1,
            "result": None,
            "error": "irtt wrote no output (rc=1): connection refused",
        }
    )
    assert out["status"] == "failed"
    assert "connection refused" in out["error"]
    assert "round_trip_ms" not in out


def test_no_probe_summarises_to_nothing():
    assert summarize_irtt(None) is None


def test_probe_endpoints_come_from_what_the_probe_reported():
    rec = summarize_irtt({"endpoint_ips": [IRTT_SERVER], "result": REAL_IRTT_JSON})
    assert irtt_probe_endpoints(rec) == {"irtt": {IRTT_SERVER}}


def test_probe_endpoints_empty_when_the_probe_did_not_run():
    """An absent probe must leave attribution byte-identical to a run without it."""
    assert irtt_probe_endpoints(None) == {}
    assert irtt_probe_endpoints({"probe": "irtt", "endpoint_ips": []}) == {}


def test_record_is_json_serialisable():
    """It goes into record.json, so it must survive a round trip."""
    out = summarize_irtt({"endpoint_ips": [IRTT_SERVER], "result": REAL_IRTT_JSON})
    assert (
        json.loads(json.dumps(out))["round_trip_ms"]["mean"]
        == out["round_trip_ms"]["mean"]
    )
