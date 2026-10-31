"""NetVibe as a probe: per-direction queueing, and the shaped-path guard.

NetVibe is the second probe to share the link with the app under test, so it
reuses the `probe_endpoint_ips` machinery IRTT introduced and nothing about app
attribution changes. Two things are specific to it and are what these tests
pin.

**Direction.** NetVibe's own convention is the opposite of the intuitive
reading: the probe SERVER sends and the sensor echoes, so in
`pkg/analyzer/clock.go` the raw one-way values are `[down] t2-t1, [up] t4-t3`.
Getting this backwards would swap every upstream and downstream number in the
dataset, silently.

**Basis.** bench stores t2/t3 on the sensor's own time base, not the epoch, so
absolute one-way delay is not recoverable from the file -- only each leg's
variation above its own floor, which is the queueing. The round trip is
absolute because the two unknowns cancel. A test builds a file whose absolute
legs are deliberately impossible and checks that the queueing and the round
trip still come out right.
"""

import pathlib
import sqlite3
import struct
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pramana_helpers import (  # noqa: E402
    check_probe_path,
    netvibe_bench_config,
    netvibe_probe_endpoints,
    probe_path_evidence,
    summarize_netvibe,
)

PROBE = "172.16.2.1"  # ns2's veth3: bench and its probe server
CLIENT = "172.16.1.1"  # ns1: the sensor
CDN = "142.251.151.4"

_LT_ETHERNET = 1


# ── a bench SQLite file, built to the schema in docs/runbooks/bench-mode.md ──
def _bench_db(path, rows, rate_changes=(), commit="abc123"):
    """rows: (t1_ns, t2_us, t3_us, t4_ns, status, rate_per_second)."""
    con = sqlite3.connect(str(path))
    con.executescript("""
        create table probe_records (t1_ns integer, device_id text,
            network_id text, session_id text, probe_server text, config text,
            family text, seq integer, echo_seq integer, t2_us integer,
            t3_us integer, t4_ns integer, status text, flags integer,
            rate_per_second real);
        create table probe_sessions (session_id text, probes_sent integer,
            ok integer, lost_down integer, lost_up integer,
            lost_unknown integer, invalid integer, duplicates integer,
            late_echoes integer, decode_errors integer,
            records_dropped integer, end_reason text);
        create table probe_rate_changes (t_ns integer, session_id text,
            probe_server text, rate_per_second real, rung integer,
            reason text);
        create table sensors (label text, device_id text);
        create table bench_sessions (session_id text, label text,
            client_ip text, network_id text, probe_server text,
            created_at_ns integer);
        create table meta (key text, value text);
        """)
    for i, (t1, t2, t3, t4, status, rate) in enumerate(rows):
        con.execute(
            "insert into probe_records (t1_ns, t2_us, t3_us, t4_ns, seq, "
            "status, rate_per_second) values (?,?,?,?,?,?,?)",
            (t1, t2, t3, t4, i, status, rate),
        )
    ok = sum(1 for r in rows if r[4] == "ok")
    con.execute(
        "insert into probe_sessions (session_id, probes_sent, ok, lost_down, "
        "lost_up, lost_unknown, invalid, duplicates, late_echoes, "
        "decode_errors, records_dropped, end_reason) "
        "values ('s1',?,?,0,0,?,0,0,0,0,0,'admin')",
        (len(rows), ok, len(rows) - ok),
    )
    for t_ns, rate, rung, reason in rate_changes:
        con.execute(
            "insert into probe_rate_changes (t_ns, rate_per_second, rung, "
            "reason) values (?,?,?,?)",
            (t_ns, rate, rung, reason),
        )
    con.execute("insert into meta (key, value) values ('netvibe_commit', ?)", (commit,))
    con.commit()
    con.close()


def _stopped(path, **kw):
    out = {
        "transport": "host_nsenter",
        "server_address": PROBE,
        "media_port": 10443,
        "endpoint_ips": [PROBE],
        "out_sqlite": str(path),
    }
    out.update(kw)
    return out


# The sensor's clock is offset from the probe server's by an impossible amount,
# which is the point: t2/t3 are on the sensor's own base. Absolute legs are
# meaningless, queueing and round trip are not.
OFFSET_US = 1_793_410_740_000_000


def _row(t1_ns, down_us, up_us, server_us, status="ok", rate=10.0):
    """One probe: `down_us` on the way out, `up_us` back, `server_us` held."""
    t2_us = t1_ns / 1000.0 + down_us + OFFSET_US
    t3_us = t2_us + server_us
    t4_ns = (t3_us - OFFSET_US + up_us) * 1000.0
    return (int(t1_ns), int(t2_us), int(t3_us), int(t4_ns), status, rate)


@pytest.fixture
def db_idle(tmp_path):
    """A quiet link: both legs flat at their floor."""
    p = tmp_path / "bench.sqlite"
    rows = [_row(i * 100_000_000, 20_000.0, 5_000.0, 50.0) for i in range(40)]
    _bench_db(p, rows)
    return p


@pytest.fixture
def db_loaded(tmp_path):
    """Downstream queues up to +40 ms; upstream stays flat."""
    p = tmp_path / "bench.sqlite"
    rows = []
    for i in range(40):
        extra = 0.0 if i < 20 else 40_000.0
        rows.append(_row(i * 100_000_000, 20_000.0 + extra, 5_000.0, 50.0))
    _bench_db(p, rows)
    return p


def test_round_trip_is_absolute_despite_the_clock_offset(db_idle):
    """down 20 ms + up 5 ms, minus the 50 us the server held it = 25 ms."""
    out = summarize_netvibe(_stopped(db_idle))
    assert out["status"] == "ok"
    rt = out["round_trip_ms"]
    assert rt["min"] == pytest.approx(25.0, abs=0.01)
    assert rt["max"] == pytest.approx(25.0, abs=0.01)


def test_queueing_is_measured_per_direction_and_not_swapped(db_loaded):
    """The load is downstream, so downstream queues and upstream must not.

    If the t2-t1 / t4-t3 convention were swapped, this test would show the
    queue on the upstream leg -- which is exactly the silent error it guards.
    """
    out = summarize_netvibe(_stopped(db_loaded))
    down = out["queueing_ms"]["downstream_ns2_to_ns1"]
    up = out["queueing_ms"]["upstream_ns1_to_ns2"]
    assert down["min"] == pytest.approx(0.0, abs=0.01)
    assert down["max"] == pytest.approx(40.0, abs=0.01)
    assert up["max"] == pytest.approx(0.0, abs=0.01)


def test_an_idle_link_shows_no_queueing_in_either_direction(db_idle):
    out = summarize_netvibe(_stopped(db_idle))
    for leg in out["queueing_ms"].values():
        assert leg["max"] == pytest.approx(0.0, abs=0.01)


def test_absolute_one_way_is_not_reported(db_idle):
    """It cannot be computed, so it must not appear as though it had been."""
    out = summarize_netvibe(_stopped(db_idle))
    assert "one_way_ms" not in out
    assert "queueing" in out["delay_basis"]
    assert "sensor's time base" in out["delay_basis"]


def test_losses_and_counts_are_reported(tmp_path):
    p = tmp_path / "bench.sqlite"
    rows = [_row(i * 100_000_000, 20_000.0, 5_000.0, 50.0) for i in range(10)]
    rows.append(_row(10 * 100_000_000, 0, 0, 0, status="lost_down"))
    rows.append(_row(11 * 100_000_000, 0, 0, 0, status="lost_up"))
    _bench_db(p, rows)
    out = summarize_netvibe(_stopped(p))
    assert out["records"]["total"] == 12
    assert out["records"]["ok"] == 10
    assert out["records"]["by_status"]["lost_down"] == 1
    assert out["records"]["by_status"]["lost_up"] == 1
    assert out["sessions"][0]["end_reason"] == "admin"
    assert out["netvibe_commit"] == "abc123"


def test_escalation_is_visible_in_the_record(tmp_path):
    """Rate changes and the rates actually used, so a doubled rate is evident."""
    p = tmp_path / "bench.sqlite"
    rows = [
        _row(i * 100_000_000, 20_000.0, 5_000.0, 50.0, rate=10.0) for i in range(10)
    ]
    rows += [
        _row(i * 100_000_000, 20_000.0, 5_000.0, 50.0, rate=20.0) for i in range(10, 20)
    ]
    _bench_db(p, rows, rate_changes=[(1_000_000_000, 20.0, 1, "delay")])
    out = summarize_netvibe(_stopped(p))
    assert out["rates_observed"] == [10.0, 20.0]
    assert out["escalated"] is True
    assert out["rate_changes"][0]["rung"] == 1
    assert out["rate_changes"][0]["reason"] == "delay"


def test_a_fixed_rate_run_is_not_reported_as_escalated(db_idle):
    out = summarize_netvibe(_stopped(db_idle))
    assert out["rates_observed"] == [10.0]
    assert out["escalated"] is False
    assert out["rate_changes"] == []


def test_an_unstamped_build_falls_back_to_the_declared_commit(db_idle, monkeypatch):
    """A binary built from a tree with no `.git` stamps the literal "dev".

    Measured on the verification run. The probe's send timing and record schema
    come from that source, so a run that cannot say which source it used is
    unattributable -- and `netvibe_commit_source` says which answer this is.
    """
    import pramana_helpers as H

    monkeypatch.setattr(H, "NETVIBE_COMMIT", "e9e93bec")
    p = db_idle.parent / "dev.sqlite"
    _bench_db(p, [_row(0, 20_000.0, 5_000.0, 50.0)], commit="dev")
    out = summarize_netvibe(_stopped(p))
    assert out["netvibe_commit"] == "e9e93bec"
    assert out["netvibe_commit_source"] == "PRAMANA_NETVIBE_COMMIT"

    # A real stamp always wins over the declaration.
    out2 = summarize_netvibe(_stopped(db_idle))
    assert out2["netvibe_commit"] == "abc123"
    assert out2["netvibe_commit_source"] == "stamped by bench"


def test_a_missing_file_is_a_failure_not_an_empty_success(tmp_path):
    out = summarize_netvibe(_stopped(tmp_path / "nope.sqlite"))
    assert out["status"] == "failed"
    assert "no bench SQLite file" in out["error"]
    assert "queueing_ms" not in out


def test_a_file_with_no_answered_probes_is_a_failure(tmp_path):
    p = tmp_path / "bench.sqlite"
    _bench_db(p, [_row(0, 0, 0, 0, status="lost_unknown")])
    out = summarize_netvibe(_stopped(p))
    assert out["status"] == "failed"
    assert "no answered probes" in out["error"]


def test_no_probe_summarises_to_nothing():
    assert summarize_netvibe(None) is None


def test_probe_endpoints_come_from_the_probe(db_idle):
    rec = summarize_netvibe(_stopped(db_idle))
    assert netvibe_probe_endpoints(rec) == {"netvibe": {PROBE}}
    assert netvibe_probe_endpoints(None) == {}


# ── bench config ────────────────────────────────────────────────────────────
def test_the_worker_path_places_the_probe_server_with_the_netns_key(tmp_path):
    """Upstream's own mechanism, rather than wrapping bench in nsenter."""
    cfg = netvibe_bench_config(
        tmp_path / "run.sqlite", tmp_path / "state", in_namespace=False
    )
    assert cfg["probe_servers"][0]["netns"] == "ns2"
    assert cfg["probe_servers"][0]["public_ip"] == PROBE
    assert cfg["probe_servers"][0]["media_port"] == 10443


def test_the_host_path_omits_netns_because_bench_is_already_in_ns2(tmp_path):
    """`netns` resolves /run/netns/NAME in the CALLER's mount namespace.

    ns1 and ns2 live inside the worker container's, so a host-side bench cannot
    use the key -- measured: `Cannot open network namespace "ns2"`.
    """
    cfg = netvibe_bench_config(
        tmp_path / "run.sqlite", tmp_path / "state", in_namespace=True
    )
    assert "netns" not in cfg["probe_servers"][0]


def test_the_state_dir_stays_clear_of_the_unix_socket_limit(monkeypatch, tmp_path):
    """Found by a real run, not by reading the docs.

    Each probe server binds a Unix socket at `<state_dir>/<label>.sock`, and
    sun_path is 108 bytes. With state_dir under the run directory the path came
    to 138 bytes and the probe server died with `bind: invalid argument`, which
    reached the operator only as "did not report ready within 45s". The state
    dir is now a short temp path, and a path that would still overflow is
    refused with the real reason.
    """
    import pramana_helpers as H

    assert H._UNIX_SOCKET_PATH_MAX == 108
    # A TMPDIR long enough to overflow on its own must be refused up front.
    deep = tmp_path / ("d" * 90) / ("e" * 40)
    deep.mkdir(parents=True)
    monkeypatch.setattr(H.tempfile, "gettempdir", lambda: str(deep))
    with pytest.raises(RuntimeError, match="sun_path limit"):
        H._netvibe_host_start(tmp_path / "run", 10)


# ── the shaped-path guard ───────────────────────────────────────────────────
def _pkt(src, dst, payload=60):
    l4 = struct.pack(">HHHH", 40000, 10443, 8 + payload, 0) + b"\x00" * payload
    ip = struct.pack(
        ">BBHHHBBH4s4s",
        0x45,
        0,
        20 + len(l4),
        0,
        0,
        64,
        17,
        0,
        bytes(int(o) for o in src.split(".")),
        bytes(int(o) for o in dst.split(".")),
    )
    return b"\x02" * 6 + b"\x03" * 6 + struct.pack(">H", 0x0800) + ip + l4


def _pcap(path, packets):
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
def both_ways(tmp_path):
    """The healthy case: the probe on both interfaces, both directions."""
    pkts = []
    for i in range(20):
        pkts.append((100.0 + i, _pkt(CLIENT, PROBE)))
        pkts.append((100.0 + i, _pkt(PROBE, CLIENT)))
    for iface in ("veth2", "veth4"):
        _pcap(tmp_path / f"{iface}.pcap", pkts)
    return {i: tmp_path / f"{i}.pcap" for i in ("veth2", "veth4")}


def test_a_probe_on_both_legs_passes(both_ways):
    r = check_probe_path(both_ways, [PROBE])
    assert r["ok"] is True
    assert r["problems"] == []
    assert r["evidence"]["veth2"]["to_probe"] == 20
    assert r["evidence"]["veth2"]["from_probe"] == 20
    assert r["evidence"]["veth4"]["to_probe"] == 20


def test_a_downstream_bypass_fails_loudly(tmp_path):
    """The mgmt0 hazard: the return leg leaves the shaped path entirely.

    The root namespace holds a /32 host route to ns1 over the management link
    that is more specific than the veth2 connected route. Measured, the probe
    does cross veth2 -- but if that ever flipped, the downstream leg would skip
    both the shaper and the capture while still producing plausible numbers.
    """
    up_only = [(100.0 + i, _pkt(CLIENT, PROBE)) for i in range(20)]
    both = up_only + [(100.0 + i, _pkt(PROBE, CLIENT)) for i in range(20)]
    _pcap(tmp_path / "veth2.pcap", up_only)  # downstream went via mgmt0
    _pcap(tmp_path / "veth4.pcap", both)
    pcaps = {i: tmp_path / f"{i}.pcap" for i in ("veth2", "veth4")}

    with pytest.raises(RuntimeError, match="did not cross the shaped path"):
        check_probe_path(pcaps, [PROBE])

    r = check_probe_path(pcaps, [PROBE], enforce=False)
    assert r["ok"] is False
    assert any("mgmt0" in p for p in r["problems"])
    # The configured policy is recorded, not the caller's choice of when to
    # raise, so a run can write its artifacts first and still abort.
    assert r["strict"] is True


def test_a_missing_capture_is_a_problem_not_a_pass(both_ways):
    with pytest.raises(RuntimeError, match="no capture was taken"):
        check_probe_path({"veth2": both_ways["veth2"]}, [PROBE])


def test_an_absent_probe_on_one_leg_fails(tmp_path):
    pkts = [(100.0 + i, _pkt(CLIENT, PROBE)) for i in range(10)]
    pkts += [(100.0 + i, _pkt(PROBE, CLIENT)) for i in range(10)]
    _pcap(tmp_path / "veth2.pcap", pkts)
    _pcap(tmp_path / "veth4.pcap", [(100.0, _pkt(CLIENT, CDN))])
    with pytest.raises(RuntimeError, match="no probe packets at all"):
        check_probe_path(
            {i: tmp_path / f"{i}.pcap" for i in ("veth2", "veth4")}, [PROBE]
        )


def test_evidence_separates_the_app_from_the_probe(tmp_path):
    """The app's packets are counted apart, so the guard cannot be fooled."""
    pkts = [(100.0, _pkt(CLIENT, PROBE)), (100.0, _pkt(PROBE, CLIENT))]
    pkts += [(100.0 + i, _pkt(CDN, CLIENT, 1400)) for i in range(50)]
    _pcap(tmp_path / "veth2.pcap", pkts)
    ev = probe_path_evidence({"veth2": tmp_path / "veth2.pcap"}, [PROBE])
    assert ev["veth2"] == {"to_probe": 1, "from_probe": 1, "other": 50}
