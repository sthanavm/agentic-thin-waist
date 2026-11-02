"""POST/GET/DELETE /irtt — the probe's start/stop lifecycle.

Modelled on /replay: start returns immediately with an id and the probe runs in
the background, which is what lets it share the link with a browser app instead
of running after it. These tests drive the real FastAPI handlers with the
subprocess layer faked, so the command lines, the readiness handshake and the
failure paths are all checked without needing namespaces or a real binary.
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

with patch(
    "subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")
):
    from substrate.main import app
    import substrate.main as main_module

client = TestClient(app)

PAYLOAD = {
    "duration_seconds": 30,
    "interval_ms": 100,
    "server_address": "172.16.2.1",
    "port": 2112,
}


def _proc(returncode=None):
    """A Popen stand-in whose poll() reports `returncode`."""
    p = MagicMock(spec=subprocess.Popen)
    p.poll.return_value = returncode
    p.returncode = returncode
    p.communicate.return_value = (b"", b"")
    return p


@pytest.fixture(autouse=True)
def _clean_registry():
    main_module.ACTIVE_IRTT.clear()
    yield
    main_module.ACTIVE_IRTT.clear()


@pytest.fixture
def started():
    """Start a probe with the server alive and the readiness probe succeeding."""
    server, clientproc = _proc(None), _proc(None)
    with patch.object(
        main_module.shutil, "which", return_value="/usr/bin/irtt"
    ), patch.object(
        main_module.subprocess, "Popen", side_effect=[server, clientproc]
    ) as popen, patch.object(
        main_module.subprocess, "run", return_value=MagicMock(returncode=0, stderr=b"")
    ):
        resp = client.post("/irtt", json=PAYLOAD)
    return resp, popen, server, clientproc


def test_missing_binary_is_refused_with_an_actionable_message():
    """A probe that cannot run must say why, not start and report zeros."""
    with patch.object(main_module.shutil, "which", return_value=None):
        r = client.post("/irtt", json=PAYLOAD)
    assert r.status_code == 422
    body = r.json()["detail"]
    assert "not found on PATH" in body
    assert "apt-get install irtt" in body


def test_start_returns_immediately_with_an_id_and_endpoints(started):
    resp, _popen, _s, _c = started
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "started"
    assert body["irtt_id"]
    # The orchestrator registers these as attribution ground truth.
    assert body["endpoint_ips"] == ["172.16.2.1"]
    assert body["server_address"] == "172.16.2.1"
    assert body["port"] == 2112


def test_server_runs_in_ns2_and_client_in_ns1(started):
    """Jaber's spec: server on the server side, client on the client side."""
    _resp, popen, _s, _c = started
    server_cmd = popen.call_args_list[0].args[0]
    client_cmd = popen.call_args_list[1].args[0]
    assert "ip netns exec ns2" in server_cmd
    assert "server -b 172.16.2.1:2112" in server_cmd
    assert "ip netns exec ns1" in client_cmd
    assert "client" in client_cmd
    assert "-i 100ms" in client_cmd
    assert "-d 30s" in client_cmd


def test_server_accepts_at_least_the_clients_duration(started):
    """irtt's server -d is the longest test it ACCEPTS; too small refuses the client."""
    _resp, popen, _s, _c = started
    server_cmd = popen.call_args_list[0].args[0]
    assert "-d 90s" in server_cmd  # 30 requested + 60 margin


def test_server_lifetime_is_bounded_so_it_cannot_outlive_the_run(started):
    """An orphan would hold the port and make the next run's bind fail obscurely."""
    _resp, popen, _s, _c = started
    assert popen.call_args_list[0].args[0].startswith("timeout -k 5 150")


def test_readiness_is_checked_rather_than_slept_on():
    """`irtt client -n` validates against the server without sending a test.

    Starting the client before the server has bound fails with "connection
    refused", which reads as a network fault rather than a startup race, so
    the handshake is an actual check and not a sleep.
    """
    server, clientproc = _proc(None), _proc(None)
    with patch.object(
        main_module.shutil, "which", return_value="/usr/bin/irtt"
    ), patch.object(
        main_module.subprocess, "Popen", side_effect=[server, clientproc]
    ), patch.object(
        main_module.subprocess, "run", return_value=MagicMock(returncode=0, stderr=b"")
    ) as run, patch.object(
        main_module.time, "sleep"
    ) as sleep:
        assert client.post("/irtt", json=PAYLOAD).status_code == 200

    probe_cmd = run.call_args.args[0]
    assert "ip netns exec ns1" in probe_cmd
    assert " -n " in probe_cmd  # no test, validate only
    # It succeeded first time, so nothing was slept on.
    assert sleep.call_count == 0


def test_readiness_timeout_is_reported_not_swallowed():
    with patch.object(
        main_module.shutil, "which", return_value="/usr/bin/irtt"
    ), patch.object(
        main_module.subprocess, "Popen", return_value=_proc(None)
    ), patch.object(
        main_module.subprocess,
        "run",
        return_value=MagicMock(returncode=1, stderr=b"connection refused"),
    ), patch.object(
        main_module.time, "time", side_effect=[0.0, 0.0, 100.0, 100.0]
    ):
        r = client.post("/irtt", json=PAYLOAD)
    assert r.status_code == 504
    detail = r.json()["detail"]
    # The message has to name the bind address, the namespace and the
    # underlying error, or a reader cannot tell a race from a routing fault.
    assert "did not accept a client" in detail
    assert "172.16.2.1:2112" in detail
    assert "ns1" in detail
    assert "connection refused" in detail


def test_a_server_that_dies_at_once_is_reported_with_its_error():
    """The bind-conflict case: an orphan on the port must be legible."""
    dead = _proc(1)
    dead.communicate.return_value = (
        b"",
        b"listen udp4 172.16.2.1:2112: bind: address already in use",
    )
    with patch.object(
        main_module.shutil, "which", return_value="/usr/bin/irtt"
    ), patch.object(main_module.subprocess, "Popen", return_value=dead), patch.object(
        main_module.subprocess, "run", return_value=MagicMock(returncode=1, stderr=b"")
    ):
        r = client.post("/irtt", json=PAYLOAD)
    assert r.status_code == 500
    assert "address already in use" in r.json()["detail"]


def test_status_reports_running_then_finished(started):
    resp, _popen, server, clientproc = started
    irtt_id = resp.json()["irtt_id"]

    r = client.get(f"/irtt/{irtt_id}")
    assert r.json()["status"] == "running"
    assert r.json()["server_alive"] is True

    clientproc.poll.return_value = 0
    r = client.get(f"/irtt/{irtt_id}")
    assert r.json()["status"] == "finished"
    assert r.json()["client_returncode"] == 0


def test_status_of_an_unknown_id_is_404():
    assert client.get("/irtt/nope").status_code == 404


def test_delete_returns_the_parsed_result_and_clears_the_session(started, tmp_path):
    resp, _popen, _server, clientproc = started
    irtt_id = resp.json()["irtt_id"]
    out = tmp_path / "irtt.json"
    out.write_text(json.dumps({"stats": {"rtt": {"mean": 50667410}}}))
    main_module.ACTIVE_IRTT[irtt_id]["out_path"] = str(out)
    clientproc.poll.return_value = 0

    r = client.delete(f"/irtt/{irtt_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "stopped"
    assert body["result"]["stats"]["rtt"]["mean"] == 50667410
    assert body["error"] is None
    assert body["endpoint_ips"] == ["172.16.2.1"]
    # The session is gone and the output file cleaned up.
    assert irtt_id not in main_module.ACTIVE_IRTT
    assert not out.exists()


def test_delete_reports_a_probe_that_wrote_nothing(started):
    """No output must read as an error, never as a successful zero-latency run."""
    resp, _popen, _server, clientproc = started
    irtt_id = resp.json()["irtt_id"]
    main_module.ACTIVE_IRTT[irtt_id]["out_path"] = "/nonexistent/irtt.json"
    clientproc.poll.return_value = 1
    clientproc.returncode = 1
    clientproc.communicate.return_value = (b"", b"server closed connection")

    body = client.delete(f"/irtt/{irtt_id}").json()
    assert body["result"] is None
    assert "wrote no output" in body["error"]
    assert "server closed connection" in body["error"]


def test_delete_reports_unreadable_output(started, tmp_path):
    resp, _popen, _server, clientproc = started
    irtt_id = resp.json()["irtt_id"]
    out = tmp_path / "irtt.json"
    out.write_text("{not json")
    main_module.ACTIVE_IRTT[irtt_id]["out_path"] = str(out)
    clientproc.poll.return_value = 0

    body = client.delete(f"/irtt/{irtt_id}").json()
    assert body["result"] is None
    assert "unreadable" in body["error"]


def test_delete_of_an_unknown_id_is_404():
    assert client.delete("/irtt/nope").status_code == 404


def test_delete_lets_the_client_finish_before_killing_it(started):
    """A truncated probe is worth more than none, so the client is waited on."""
    resp, _popen, _server, clientproc = started
    irtt_id = resp.json()["irtt_id"]
    main_module.ACTIVE_IRTT[irtt_id]["out_path"] = "/nonexistent/irtt.json"
    client.delete(f"/irtt/{irtt_id}")
    clientproc.communicate.assert_called()
    assert clientproc.kill.call_count == 0
