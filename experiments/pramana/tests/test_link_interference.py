"""Traffic that was not the app's, judged by WHEN it moved and not how much.

The run behind this file: a webrtc_peer capture on 2026-09-22 in which
5,481,654 bytes -- 74.9% of every byte in the pcap -- were Chrome fetching its
own chromedriver at 10.15 Mbps against a 10 Mbps cap. It was inside the
capture, so it is in the dataset. It finished 3.71 s *before* the first media
packet, so it never contended with the measurement. The run was valid by
timing rather than by design, and nothing in the record said so.

So the guard has to do two different things, and the first two tests are the
pair that matters: the historical run must still PASS (it was valid) while
being flagged (it was lucky), and the same bytes moved into the app's window
must FAIL. A guard that cannot tell those apart is either useless or it
invalidates good runs.
"""

import pathlib
import struct
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pramana_helpers import (  # noqa: E402
    attribute_capture,
    link_interference,
)

_LT_ETHERNET = 1

CLIENT = "172.16.1.1"  # ns1: the browser
CDN = "142.251.151.4"  # the app's endpoint
SETUP = "185.199.109.153"  # githubusercontent, where the driver comes from
PROBE = "172.16.2.1"  # ns2: a declared probe's far end
CAP = 10.0

# The non-app hosts are given DNS answers, exactly as they have in a real
# capture. It matters: an UNNAMED flow overlapping the app is what
# `adopt_unnamed_ips` claims by timing affinity, so an unnamed setup download
# would be folded into the app and never reach a non-app bucket to be judged.
OTHER_NAME = "cdn.example.net"  # classifies as `other`
INFRA_NAME = "googlechromelabs.github.io"  # classifies as `browser_infra`

# Bytes a `_pkt` puts on the wire: 14 ethernet + 20 IPv4 + 20 TCP + payload.
_ON_WIRE = 14 + 20 + 20


def _pkt(
    src: str,
    dst: str,
    payload: int,
    proto: int = 6,
    sport: int = 40000,
    dport: int = 443,
) -> bytes:
    body = b"\x00" * payload
    if proto == 17:
        l4 = struct.pack(">HHHH", sport, dport, 8 + payload, 0) + body
    else:
        l4 = struct.pack(">HHIIBBHHH", sport, dport, 0, 0, 0x50, 0x10, 0, 0, 0) + body
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


def _dns_response(name: str, ip: str) -> bytes:
    """A minimal A-record answer, so a host lands in a NAMED bucket."""
    qname = (
        b"".join(bytes([len(lbl)]) + lbl.encode() for lbl in name.split(".")) + b"\x00"
    )
    dns = (
        struct.pack(">HHHHHH", 0x1234, 0x8180, 1, 1, 0, 0)  # 1 question, 1 answer
        + qname
        + struct.pack(">HH", 1, 1)  # QTYPE=A, QCLASS=IN
        + qname
        + struct.pack(">HHIH", 1, 1, 60, 4)  # A, IN, ttl, rdlength
        + bytes(int(o) for o in ip.split("."))
    )
    udp = struct.pack(">HHHH", 53, 40000, 8 + len(dns), 0) + dns
    ip_hdr = struct.pack(
        ">BBHHHBBH4s4s",
        0x45,
        0,
        20 + len(udp),
        0,
        0,
        64,
        17,
        0,
        bytes(int(o) for o in "10.0.0.53".split(".")),
        bytes(int(o) for o in CLIENT.split(".")),
    )
    return b"\x02" * 6 + b"\x03" * 6 + struct.pack(">H", 0x0800) + ip_hdr + udp


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


# Packets/s of 1400 B that make up one Mbps-second: 1 Mbps = 125 kB/s = ~89.
def _pkts_for_mbps(mbps: float) -> int:
    return max(int(round(mbps * 1e6 / 8 / 1400)), 1)


def _app_second(t: float, mbps: float = 2.0) -> list:
    out = [(t, _pkt(CDN, CLIENT, 1400)) for _ in range(_pkts_for_mbps(mbps))]
    out.append((t, _pkt(CLIENT, CDN, 60)))
    return out


def _setup_second(t: float, mbps: float, peer: str = SETUP) -> list:
    return [(t, _pkt(peer, CLIENT, 1400)) for _ in range(_pkts_for_mbps(mbps))]


T0 = 100.0


def _capture(tmp_path, packets, name="capture.pcap"):
    pcap = tmp_path / name
    _write_pcap(pcap, packets)
    return pcap


def _judge(pcap, **kw):
    kw.setdefault("app_endpoint_ips", {"youtube": {CDN}})
    at = attribute_capture(pcap, ["youtube"], cap_mbps=CAP, client_ips=[CLIENT], **kw)
    return at, link_interference(at, ["youtube"], CAP)


def _named(host: str, ip: str = SETUP) -> list:
    """The DNS answer that gives `ip` a name, first thing in the capture."""
    return [(T0, _dns_response(host, ip))]


# ── the pair that matters ────────────────────────────────────────────────────
def test_a_setup_download_before_the_app_starts_is_flagged_but_does_not_fail(
    tmp_path,
):
    """The 2026-09-22 shape: driver fetch in the first 5 s, media from t=10 s.

    It must not fail the run -- the app's numbers really were taken off an
    uncontended link -- and it must not be silent either.
    """
    packets = _named(OTHER_NAME)
    for s in range(5):  # the fetch: saturating, before the app
        packets += _setup_second(T0 + s, CAP)
    for s in range(10, 40):  # the app, afterwards
        packets += _app_second(T0 + s)
    at, li = _judge(_capture(tmp_path, packets))

    assert li.checked is True
    assert li.ok is True, li.offenders
    assert li.offenders == []
    assert li.warnings, "a saturating non-app flow must not pass unremarked"
    assert "other" in li.warnings[0]
    # The window starts at the app, not at the capture.
    assert li.app_window_s[0] >= 10.0
    assert at.bucket_mb["other"] > 5.0


def test_the_same_bytes_inside_the_app_window_fail_the_run(tmp_path):
    """Move the fetch into the measured window and the run is not a measurement."""
    packets = _named(OTHER_NAME)
    for s in range(40):
        packets += _app_second(T0 + s)
        if 15 <= s < 20:  # the fetch, now overlapping
            packets += _setup_second(T0 + s, CAP)
    _at, li = _judge(_capture(tmp_path, packets))

    assert li.checked is True
    assert li.ok is False
    assert len(li.offenders) == 1
    assert "other/download" in li.offenders[0]
    assert "5 bins" in li.offenders[0]
    per = li.buckets["other"]["download"]
    assert per["in_window_contended_bins"] == 5
    assert per["in_window_peak_mbps"] > CAP * 0.5


def test_a_short_saturating_burst_in_a_long_window_still_fails(tmp_path):
    """The reason the test is per-bin and not a mean over the window.

    5 s of saturation inside a 120 s window is 4.2% of the window's capacity --
    comfortably under a 10% budget -- and 100% of the link while it lasts. The
    app rebuffers through it either way, so a mean would call this run clean.
    """
    packets = _named(OTHER_NAME)
    for s in range(120):
        packets += _app_second(T0 + s)
        if 60 <= s < 65:
            packets += _setup_second(T0 + s, CAP)
    _at, li = _judge(_capture(tmp_path, packets))

    per = li.buckets["other"]["download"]
    # The mean a window-average guard would have looked at, and passed.
    assert per["in_window_share_of_cap"] < li.budget_fraction
    # What this guard looks at instead.
    assert per["in_window_contended_bins"] == 5
    assert li.ok is False
    assert "5 bins" in li.offenders[0]


# ── the guard must not fire on ordinary runs ─────────────────────────────────
def test_quiet_background_traffic_is_under_budget(tmp_path):
    """A run with normal background chatter must pass clean and silent.

    This is the test that keeps the guard usable: one that cried wolf on every
    run would be turned off, and then it would catch nothing.
    """
    packets = _named(OTHER_NAME)
    for s in range(60):
        packets += _app_second(T0 + s)
        packets += _setup_second(T0 + s, 0.05)  # ~6 kB/s of chatter
    _at, li = _judge(_capture(tmp_path, packets))

    assert li.checked is True
    assert li.ok is True
    assert li.offenders == []
    assert li.warnings == []


def test_a_single_contended_bin_is_not_enough_to_fail(tmp_path):
    """Below `min_bins` is a blip, not contention. Pinned so the floor is real."""
    packets = _named(OTHER_NAME)
    for s in range(40):
        packets += _app_second(T0 + s)
        if s == 20:
            packets += _setup_second(T0 + s, CAP)
    _at, li = _judge(_capture(tmp_path, packets))

    assert li.buckets["other"]["download"]["in_window_contended_bins"] == 1
    assert li.ok is True
    assert li.offenders == []
    assert li.warnings, "it still peaked over the cap, so it is still reported"


# ── probes share the link on purpose ────────────────────────────────────────
def test_a_declared_probe_is_never_an_offender(tmp_path):
    """IRTT and NetVibe are *meant* to be on the link; that is the whole point.

    Without this, declaring a probe would make every probed run abort.
    """
    packets = []
    for s in range(40):
        packets += _app_second(T0 + s)
        packets += [
            (T0 + s, _pkt(PROBE, CLIENT, 1400, proto=17, dport=2112))
            for _ in range(_pkts_for_mbps(CAP))
        ]
    at = attribute_capture(
        _capture(tmp_path, packets),
        ["youtube"],
        cap_mbps=CAP,
        client_ips=[CLIENT],
        app_endpoint_ips={"youtube": {CDN}},
        probe_endpoint_ips={"irtt": {PROBE}},
    )
    li = link_interference(at, ["youtube"], CAP)

    assert at.bucket_mb.get("irtt", 0) > 0, "probe should have its own bucket"
    assert li.ok is True
    assert li.offenders == []
    assert "irtt" not in li.buckets


# ── the named buckets are judged too, not only `other` ──────────────────────
def test_browser_infra_is_judged_not_only_the_unknown_bucket(tmp_path):
    """After the classifier fix the driver fetch lands in `browser_infra`.

    If the guard only looked at `other`, naming the host would have quietly
    switched the guard off -- the two changes would have cancelled out.
    """
    packets = _named(INFRA_NAME)
    for s in range(40):
        packets += _app_second(T0 + s)
        if 15 <= s < 20:
            packets += _setup_second(T0 + s, CAP)
    at, li = _judge(_capture(tmp_path, packets))

    assert at.bucket_mb.get("browser_infra", 0) > 5.0, at.bucket_mb
    assert li.ok is False
    assert "browser_infra/download" in li.offenders[0]


# ── "nothing to judge" must not read as "passed" ────────────────────────────
def test_an_app_that_never_moved_a_byte_is_not_checked(tmp_path):
    """No measured window means no verdict. `checked` is what the abort reads."""
    packets = _named(OTHER_NAME)
    for s in range(10):
        packets += _setup_second(T0 + s, CAP)
    _at, li = _judge(_capture(tmp_path, packets))

    assert li.checked is False
    assert li.app_window_s == []
    assert li.note
    # Still reported, because this is the run where it matters most.
    assert li.buckets["other"]["download"]["peak_mbps"] > 0


def test_an_empty_capture_is_not_checked(tmp_path):
    pcap = tmp_path / "empty.pcap"
    _write_pcap(pcap, [])
    at = attribute_capture(pcap, ["youtube"], cap_mbps=CAP, client_ips=[CLIENT])
    li = link_interference(at, ["youtube"], CAP)
    assert li.checked is False
    assert li.buckets == {}


def test_no_shaped_capacity_is_not_checked(tmp_path):
    packets = [p for s in range(10) for p in _app_second(T0 + s)]
    at = attribute_capture(
        _capture(tmp_path, packets), ["youtube"], cap_mbps=0.0, client_ips=[CLIENT]
    )
    li = link_interference(at, ["youtube"], 0.0)
    assert li.checked is False
    assert li.note


# ── the series the guard reads ──────────────────────────────────────────────
def test_the_nonapp_series_is_on_the_same_grid_as_the_app_series(tmp_path):
    """Same bin count and same `t0`, which is what makes "at the same time" a
    bin index rather than a comparison across two time bases."""
    packets = _named(OTHER_NAME)
    for s in range(30):
        packets += _app_second(T0 + s)
        packets += _setup_second(T0 + s, 1.0)
    at, _li = _judge(_capture(tmp_path, packets))

    assert at.nonapp_mbps, "non-app buckets must get a series"
    app_series = at.traffic("youtube", "download").mbps
    for bucket, dirs in at.nonapp_mbps.items():
        for direction, series in dirs.items():
            assert (
                len(series) == at.nbins == len(app_series)
            ), f"{bucket}/{direction} is off-grid"


def test_app_buckets_do_not_appear_in_the_nonapp_series(tmp_path):
    """The split has to be exclusive or the app would contend with itself."""
    packets = []
    for s in range(20):
        packets += _app_second(T0 + s)
    at = attribute_capture(
        _capture(tmp_path, packets),
        ["youtube"],
        cap_mbps=CAP,
        client_ips=[CLIENT],
        app_endpoint_ips={"youtube": {CDN}},
    )
    assert "youtube" not in at.nonapp_mbps
    li = link_interference(at, ["youtube"], CAP)
    assert li.ok is True
    assert li.offenders == []


def test_adding_the_series_did_not_move_the_app_numbers(tmp_path):
    """The per-app series is the measurement; binning extra buckets must not
    touch it. Compared against the figures the same capture gave before."""
    packets = _named(OTHER_NAME)
    for s in range(20):
        packets += _app_second(T0 + s, mbps=2.0)
        packets += _setup_second(T0 + s, 1.0)
    at = attribute_capture(
        _capture(tmp_path, packets),
        ["youtube"],
        cap_mbps=CAP,
        client_ips=[CLIENT],
        app_endpoint_ips={"youtube": {CDN}},
    )
    dl = at.traffic("youtube", "download")
    # Every app figure is checked against the packets that were written, not
    # against a round number, so the assertion cannot drift with the fixture.
    assert dl.packets == 20 * _pkts_for_mbps(2.0)
    assert dl.total_mb == pytest.approx(
        20 * _pkts_for_mbps(2.0) * (_ON_WIRE + 1400) / 1e6, rel=0.001
    )
    assert at.bucket_mb["youtube"] == pytest.approx(
        (20 * _pkts_for_mbps(2.0) * (_ON_WIRE + 1400) + 20 * (_ON_WIRE + 60)) / 1e6,
        rel=0.001,
    )
    # And the series the guard reads is the same bytes over the same bins.
    assert dl.avg_throughput_mbps == pytest.approx(
        dl.total_mb * 8 / (at.nbins * at.bin_s), rel=0.001
    )
    assert sum(dl.mbps) * at.bin_s / 8 == pytest.approx(dl.total_mb, rel=0.001)


# ── the abort is opt-in, and that is load-bearing ───────────────────────────
def test_the_abort_is_off_by_default(monkeypatch):
    """22 of 31 archived captures fail this test, so the abort cannot default on.

    The figures are still computed and still warned about -- what is opt-in is
    only whether the run raises. If this ever flips to on by default it will
    start invalidating the existing dataset, so it is pinned here.
    """
    import importlib

    import pramana_helpers

    monkeypatch.delenv("PRAMANA_STRICT_INTERFERENCE", raising=False)
    reloaded = importlib.reload(pramana_helpers)
    assert reloaded.STRICT_LINK_INTERFERENCE is False

    monkeypatch.setenv("PRAMANA_STRICT_INTERFERENCE", "1")
    reloaded = importlib.reload(pramana_helpers)
    assert reloaded.STRICT_LINK_INTERFERENCE is True

    monkeypatch.delenv("PRAMANA_STRICT_INTERFERENCE", raising=False)
    importlib.reload(pramana_helpers)


def test_the_verdict_is_computed_even_when_the_abort_is_off(tmp_path):
    """Opting out of the raise must not opt out of the measurement."""
    packets = _named(OTHER_NAME)
    for s in range(40):
        packets += _app_second(T0 + s)
        if 15 <= s < 20:
            packets += _setup_second(T0 + s, CAP)
    _at, li = _judge(_capture(tmp_path, packets))

    assert li.checked is True
    assert li.ok is False, "the verdict stands whether or not anything raises"
    assert li.buckets["other"]["download"]["in_window_contended_bins"] == 5
