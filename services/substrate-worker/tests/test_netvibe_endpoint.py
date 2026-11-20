"""POST/GET/DELETE /netvibe — NetVibe bench mode's start/stop lifecycle.

Bench mode is NetVibe's own testbed path: the production probe server with a
SQLite sink and none of signaling, Postgres, ClickHouse or device login. These
tests drive the real handlers with the subprocess layer faked, so the command
lines, the placement into ns2, the readiness handshake and the failure paths
are checked without namespaces or the Go binaries.
"""

import json
import os
import sqlite3
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

with patch(
    "subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")
):
    from substrate.main import app
    import substrate.main as main_module
    import substrate.probes as probes_module

client = TestClient(app)

PAYLOAD = {
    "duration_seconds": 60,
    "rate_per_second": 10,
    "server_address": "172.16.2.1",
    "signaling_address": "172.16.2.1",
    "media_port": 10443,
    "sensor_id": "pramana-ns1",
}


def _proc(returncode=None, out=b""):
    p = MagicMock(spec=subprocess.Popen)
    p.poll.return_value = returncode
    p.returncode = returncode
    p.communicate.return_value = (out, b"")
    return p


@pytest.fixture(autouse=True)
def _clean():
    main_module.ACTIVE_NETVIBE.clear()
    yield
    main_module.ACTIVE_NETVIBE.clear()


@pytest.fixture
def started(tmp_path):
    stub, bench, sensor = _proc(None), _proc(None), _proc(None)
    with patch.object(
        probes_module, "_netvibe_bin", side_effect=lambda n: f"/opt/netvibe/bin/{n}"
    ), patch.object(
        main_module.subprocess, "Popen", side_effect=[stub, bench, sensor]
    ) as popen, patch.object(
        main_module.subprocess,
        "run",
        return_value=MagicMock(returncode=0, stdout=b'{"down":[],"ready":true}'),
    ):
        resp = client.post("/netvibe", json=PAYLOAD)
    return resp, popen, bench, sensor, stub


def test_missing_binaries_are_refused_with_build_instructions():
    """A probe that cannot run must say so, not start and report nothing."""
    with patch.object(probes_module, "_netvibe_bin", return_value=None):
        r = client.post("/netvibe", json=PAYLOAD)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "netvibe-bench" in detail and "netvibe-cli" in detail
    assert "Go 1.25" in detail


def test_start_returns_an_id_and_the_probe_address(started):
    resp = started[0]
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "started"
    assert body["netvibe_id"]
    # Media and the sensor API are both on ns2's veth3 now, and must be kept
    # out of the app's figures.
    assert body["endpoint_ips"] == ["172.16.2.1"]


def test_bench_runs_in_ns2_and_listens_where_ns1_can_reach(started, tmp_path):
    """Bench in the worker's root namespace listened on 172.16.2.2, which ns1
    cannot reach: the sensor never registered and nothing was recorded. Bench
    now runs inside ns2, as the host fallback does, so its probe server child
    needs no `netns` key."""
    _resp, popen, _bench, _sensor, _stub = started
    bench_cmd = popen.call_args_list[1].args[0]
    assert "ip netns exec ns2 /opt/netvibe/bin/netvibe-bench" in bench_cmd
    session = next(iter(main_module.ACTIVE_NETVIBE.values()))
    cfg = json.loads(open(f"{session['work']}/bench.yaml").read())
    server = cfg["probe_servers"][0]
    assert "netns" not in server
    assert server["media_port"] == 10443
    assert server["public_ip"] == "172.16.2.1"
    assert cfg["listen"] == "172.16.2.1:9999"
    # No part of the deployed stack is involved.
    assert cfg["out"].endswith("bench.sqlite")


def test_the_sensor_runs_in_ns1_with_host_candidates_only(started):
    """A STUN candidate could put the media off the shaped path entirely."""
    _resp, popen, _bench, _sensor, _stub = started
    sensor_cmd = popen.call_args_list[2].args[0]
    assert "ip netns exec ns1" in sensor_cmd
    assert "netvibe-cli test" in sensor_cmd
    assert "--no-stun" in sensor_cmd
    assert "--signaling-host http://172.16.2.1" in sensor_cmd


def test_the_speedtest_is_suppressed_by_a_stub_not_by_luck(started):
    """An NDT7 test would saturate the link the probe is measuring.

    With no API to ask, the sensor decides a test is due and runs one. The stub
    answers the due check with `due: false`; hoping m-lab is unreachable is not
    a control.
    """
    _resp, popen, _bench, _sensor, _stub = started
    stub_cmd = popen.call_args_list[0].args[0]
    assert "ip netns exec ns2" in stub_cmd
    assert "stub_api.py" in stub_cmd
    session = next(iter(main_module.ACTIVE_NETVIBE.values()))
    src = open(f"{session['work']}/stub_api.py").read()
    assert '"due": False' in src
    assert "/speedtests/latest" in src
    sensor_cmd = popen.call_args_list[2].args[0]
    assert "--api-port 9898" in sensor_cmd


def test_bench_lifetime_is_bounded_so_it_cannot_outlive_the_run(started):
    _resp, popen, _bench, _sensor, _stub = started
    assert popen.call_args_list[1].args[0].startswith("exec timeout -k 5 240")


def test_readiness_is_asked_from_inside_ns2_where_bench_runs(tmp_path):
    """The worker's root namespace cannot reach ns2's veth3 address, so a
    readiness check from there times out although bench is up."""
    with patch.object(
        probes_module, "_netvibe_bin", side_effect=lambda n: f"/opt/netvibe/bin/{n}"
    ), patch.object(
        main_module.subprocess,
        "Popen",
        side_effect=[_proc(None), _proc(None), _proc(None)],
    ), patch.object(
        main_module.subprocess,
        "run",
        return_value=MagicMock(returncode=0, stdout=b'{"ready":true}'),
    ) as run:
        r = client.post("/netvibe", json=PAYLOAD)
    assert r.status_code == 200
    ready_cmd = run.call_args_list[0].args[0]
    assert ready_cmd.startswith("ip netns exec ns2 curl")
    assert "http://172.16.2.1:9999/ready" in ready_cmd


def test_every_command_is_execd_so_terminate_reaches_the_process(started):
    """Without `exec`, terminate() signals only /bin/sh and bench runs on,
    orphaned, never checkpointing its WAL."""
    _resp, popen, _bench, _sensor, _stub = started
    for call in popen.call_args_list:
        assert call.args[0].startswith("exec "), call.args[0]


def test_escalation_is_configured_only_when_asked(tmp_path):
    stub, bench, sensor = _proc(None), _proc(None), _proc(None)
    with patch.object(
        probes_module, "_netvibe_bin", side_effect=lambda n: f"/opt/netvibe/bin/{n}"
    ), patch.object(
        main_module.subprocess, "Popen", side_effect=[stub, bench, sensor]
    ), patch.object(
        main_module.subprocess,
        "run",
        return_value=MagicMock(returncode=0, stdout=b'{"ready":true}'),
    ):
        r = client.post("/netvibe", json={**PAYLOAD, "escalate": True})
    assert r.status_code == 200
    session = next(iter(main_module.ACTIVE_NETVIBE.values()))
    probe = json.loads(open(f"{session['work']}/bench.yaml").read())["probe"]
    assert probe["escalation_rates"] == [20.0]
    assert probe["trigger_delay_ms"] == 5
    assert probe["name"] == "pramana-escalating"


def test_a_steady_rate_above_tens_is_refused(tmp_path):
    """bench caps a steady rate at 10/s; faster comes from escalation rungs."""
    r = client.post("/netvibe", json={**PAYLOAD, "rate_per_second": 20})
    assert r.status_code == 422


def test_bench_that_dies_during_startup_is_reported_with_its_output():
    dead = _proc(1, out=b"listen udp4 172.16.2.1:10443: bind: address already in use")
    with patch.object(
        probes_module, "_netvibe_bin", side_effect=lambda n: f"/opt/netvibe/bin/{n}"
    ), patch.object(
        main_module.subprocess, "Popen", side_effect=[_proc(None), dead]
    ), patch.object(
        main_module.subprocess, "run", return_value=MagicMock(returncode=1, stdout=b"")
    ):
        r = client.post("/netvibe", json=PAYLOAD)
    assert r.status_code == 500
    assert "address already in use" in r.json()["detail"]


def test_a_readiness_timeout_names_the_url():
    """bench's /ready lists servers that are down, so this is a real check."""
    with patch.object(
        probes_module, "_netvibe_bin", side_effect=lambda n: f"/opt/netvibe/bin/{n}"
    ), patch.object(
        main_module.subprocess, "Popen", side_effect=[_proc(None), _proc(None)]
    ), patch.object(
        main_module.subprocess, "run", return_value=MagicMock(returncode=0, stdout=b"")
    ), patch.object(
        main_module.time, "time", side_effect=[0.0, 0.0, 1000.0, 1000.0]
    ):
        r = client.post("/netvibe", json=PAYLOAD)
    assert r.status_code == 504
    assert "never reported ready" in r.json()["detail"]
    assert "172.16.2.1:9999" in r.json()["detail"]


def test_status_reports_running_then_finished(started):
    resp, _popen, _bench, sensor, _stub = started
    nid = resp.json()["netvibe_id"]
    assert client.get(f"/netvibe/{nid}").json()["status"] == "running"
    sensor.poll.return_value = 0
    body = client.get(f"/netvibe/{nid}").json()
    assert body["status"] == "finished"
    assert body["bench_alive"] is True


def test_status_of_an_unknown_id_is_404():
    assert client.get("/netvibe/nope").status_code == 404


def _bench_db(path, probe_records=3):
    """A bench-shaped SQLite file in WAL mode with its rows still in the WAL.

    The connection is returned open with autocheckpoint off, which is how bench
    leaves it: the main file reads ~4 KB while the records sit in -wal.
    """
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    for t in ("sensors", "probe_sessions", "probe_records"):
        conn.execute(f'CREATE TABLE "{t}" (x INTEGER)')
    conn.execute("INSERT INTO sensors VALUES (1)")
    conn.execute("INSERT INTO probe_sessions VALUES (1)")
    conn.executemany(
        "INSERT INTO probe_records VALUES (?)", [(i,) for i in range(probe_records)]
    )
    conn.commit()
    return conn


def test_delete_stops_the_sensor_before_bench(started, tmp_path):
    """bench flushes its records and writes stopped_at_ns on SIGTERM.

    The file is only complete once bench exits, so stopping bench first would
    lose the tail of the run.
    """
    resp, _popen, bench, sensor, _stub = started
    nid = resp.json()["netvibe_id"]
    out = tmp_path / "bench.sqlite"
    conn = _bench_db(str(out))
    main_module.ACTIVE_NETVIBE[nid]["out_sqlite"] = str(out)

    body = client.delete(f"/netvibe/{nid}").json()
    conn.close()
    assert body["status"] == "stopped"
    assert body["error"] is None
    assert body["out_sqlite_bytes"] > 0
    assert body["endpoint_ips"] == ["172.16.2.1"]
    sensor.terminate.assert_called()
    bench.terminate.assert_called()
    assert nid not in main_module.ACTIVE_NETVIBE


def test_delete_counts_records_still_in_the_wal(started, tmp_path):
    """The main file alone read 4096 bytes while the data sat in the WAL, so a
    run looked empty when it was not. The report must see the WAL's rows."""
    resp = started[0]
    nid = resp.json()["netvibe_id"]
    out = tmp_path / "bench.sqlite"
    conn = _bench_db(str(out), probe_records=50)
    main_only = os.path.getsize(out)
    assert os.path.getsize(str(out) + "-wal") > 0
    main_module.ACTIVE_NETVIBE[nid]["out_sqlite"] = str(out)

    body = client.delete(f"/netvibe/{nid}").json()
    conn.close()
    assert body["records"] == {"sensors": 1, "probe_sessions": 1, "probe_records": 50}
    assert body["out_sqlite_bytes"] > main_only
    assert body["error"] is None


def test_delete_reports_a_run_that_recorded_no_probes(started, tmp_path):
    """A valid file with zero probe records is a failed measurement, not a
    quiet run: this is exactly what the unreachable-bench bug produced."""
    resp = started[0]
    nid = resp.json()["netvibe_id"]
    out = tmp_path / "bench.sqlite"
    conn = _bench_db(str(out), probe_records=0)
    main_module.ACTIVE_NETVIBE[nid]["out_sqlite"] = str(out)

    body = client.delete(f"/netvibe/{nid}").json()
    conn.close()
    assert body["records"]["probe_records"] == 0
    assert "recorded no probes" in body["error"]


def test_delete_reports_a_run_that_wrote_no_file(started):
    resp = started[0]
    nid = resp.json()["netvibe_id"]
    main_module.ACTIVE_NETVIBE[nid]["out_sqlite"] = "/nonexistent/bench.sqlite"
    body = client.delete(f"/netvibe/{nid}").json()
    assert "wrote no file" in body["error"]


def test_delete_reports_an_empty_file(started, tmp_path):
    """Zero bytes must read as a failure, not as a run with no congestion."""
    resp = started[0]
    nid = resp.json()["netvibe_id"]
    empty = tmp_path / "bench.sqlite"
    empty.write_bytes(b"")
    main_module.ACTIVE_NETVIBE[nid]["out_sqlite"] = str(empty)
    body = client.delete(f"/netvibe/{nid}").json()
    assert "empty file" in body["error"]


def test_delete_of_an_unknown_id_is_404():
    assert client.delete("/netvibe/nope").status_code == 404
