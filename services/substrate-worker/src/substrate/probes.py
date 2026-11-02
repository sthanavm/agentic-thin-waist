"""Link probe endpoints (IRTT, NetVibe), shared by every substrate-worker profile.

An isochronous UDP round-trip measurement across the shaped link: server in
ns2, client in ns1, so the probe traverses the same bottleneck (`veth2`) the
browser's traffic does and is measured by the same capture.

This lives in its own module because the three worker profiles -- `main`,
`main_local` and `main_aws` -- each need the identical endpoint, and three
hand-kept copies of a 276-line block drift. The profiles differ in how they
*shape* (WAN-interface detection, qtrace polling); the probe does not differ
at all, so it is defined once here and attached to each profile's app by
`register_irtt_routes`.

The NetVibe probe arrived the same way -- copied verbatim into all three
profiles -- and is kept here for the same reason, attached by
`register_netvibe_routes`.

Same lifecycle as /replay: start returns immediately with an id, the probe
runs in the background alongside whatever else is using the link, and stop
collects the result. That is what lets it run CONCURRENTLY with a browser app
without touching the orchestrator's app sequencing.
"""

import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

ACTIVE_IRTT: dict = {}  # irtt_id → session dict
ACTIVE_NETVIBE: dict = {}  # netvibe_id → session dict


class IrttRequest(BaseModel):
    """One isochronous UDP round-trip measurement across the shaped link.

    Server in ns2 and client in ns1, so the probe traverses the same bottleneck
    (`veth2`) the browser's traffic does and is measured by the same capture.
    """

    duration_seconds: int = Field(60, ge=1, description="How long the client sends for")
    interval_ms: int = Field(
        100, ge=1, description="Send interval; 100 ms is ~12 kbps of probe traffic"
    )
    server_namespace: str = Field("ns2", description="Namespace the server binds in")
    client_namespace: str = Field("ns1", description="Namespace the client runs in")
    server_address: str = Field(
        "172.16.2.1", description="ns2's veth3 address, reached from ns1 via veth2"
    )
    port: int = Field(2112, ge=1, le=65535, description="irtt's default port")
    packet_length: int = Field(
        0, ge=0, description="Payload bytes; 0 leaves irtt's default"
    )


class IrttResponse(BaseModel):
    irtt_id: str
    status: str
    server_address: str
    port: int
    # The orchestrator registers these as attribution ground truth so the probe
    # does not land in the browser's per-app pcap or the shared-cap sum.
    endpoint_ips: list[str]
    start_time: str


class IrttStatusResponse(BaseModel):
    irtt_id: str
    status: str  # "running" while the client is sending, else "finished"
    server_address: str
    port: int
    endpoint_ips: list[str]
    start_time: str
    server_alive: bool
    client_returncode: Optional[int] = None


IRTT_BIN = os.environ.get("IRTT_BIN", "irtt")


def _terminate(proc: subprocess.Popen) -> None:
    """Stop a background process, escalating only if it ignores SIGTERM."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _irtt_available() -> Optional[str]:
    """Path to the irtt binary, or None. Never raises."""
    return shutil.which(IRTT_BIN)


def start_irtt(cfg: IrttRequest) -> IrttResponse:
    binary = _irtt_available()
    if binary is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"irtt binary {IRTT_BIN!r} not found on PATH. Install it in the "
                f"worker image (apt-get install irtt) or set IRTT_BIN."
            ),
        )

    # The server's -d is the longest test it will ACCEPT, not its own lifetime,
    # so it has to be at least the client's duration or the client is refused.
    server_max = cfg.duration_seconds + 60
    out_path = f"/tmp/irtt_{uuid.uuid4().hex[:10]}.json"
    bind = f"{cfg.server_address}:{cfg.port}"

    # `timeout` is a hard backstop, not the normal stop path: DELETE /irtt
    # terminates the server. Without it, an orchestrator that dies mid-run
    # would leave a server holding the port, and the next run's bind would
    # fail with an error that looks nothing like the real cause.
    server_cmd = (
        f"timeout -k 5 {server_max + 60}"
        f" ip netns exec {cfg.server_namespace} {binary} server"
        f" -b {bind} -d {server_max}s -i 1ms --timeout=10s"
    )
    try:
        server_proc = subprocess.Popen(
            server_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to start irtt server: {exc}"
        )

    # Readiness, not a sleep: `irtt client -n` connects and validates the test
    # parameters without sending a test. Starting the client against a server
    # that has not bound yet fails with "connection refused", which would look
    # like a network problem rather than a race.
    probe_cmd = (
        f"ip netns exec {cfg.client_namespace} {binary} client" f" -n -Q -d 1s {bind}"
    )
    deadline = time.time() + 10.0
    ready, last_err = False, ""
    while time.time() < deadline:
        if server_proc.poll() is not None:
            _, err = server_proc.communicate()
            raise HTTPException(
                status_code=500,
                detail=(
                    f"irtt server exited immediately (rc="
                    f"{server_proc.returncode}): {err.decode(errors='replace')[:300]}"
                ),
            )
        probe = subprocess.run(probe_cmd, shell=True, capture_output=True, timeout=15)
        if probe.returncode == 0:
            ready = True
            break
        last_err = probe.stderr.decode(errors="replace").strip()[:200]
        time.sleep(0.5)
    if not ready:
        _terminate(server_proc)
        raise HTTPException(
            status_code=504,
            detail=(
                f"irtt server bound to {bind} did not accept a client from "
                f"{cfg.client_namespace} within 10s: {last_err}"
            ),
        )

    client_cmd = [
        f"ip netns exec {cfg.client_namespace} {binary} client",
        f"-i {cfg.interval_ms}ms",
        f"-d {cfg.duration_seconds}s",
        "-Q",
        f"-o {out_path}",
    ]
    if cfg.packet_length:
        client_cmd.append(f"-l {cfg.packet_length}")
    client_cmd.append(bind)
    try:
        client_proc = subprocess.Popen(
            " ".join(client_cmd),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception as exc:
        _terminate(server_proc)
        raise HTTPException(
            status_code=500, detail=f"Failed to start irtt client: {exc}"
        )

    irtt_id = str(uuid.uuid4())
    ACTIVE_IRTT[irtt_id] = {
        "irtt_id": irtt_id,
        "server_proc": server_proc,
        "client_proc": client_proc,
        "out_path": out_path,
        "server_address": cfg.server_address,
        "port": cfg.port,
        "start_time": datetime.utcnow().isoformat(),
        "request": cfg.model_dump(),
    }
    return IrttResponse(
        irtt_id=irtt_id,
        status="started",
        server_address=cfg.server_address,
        port=cfg.port,
        endpoint_ips=[cfg.server_address],
        start_time=ACTIVE_IRTT[irtt_id]["start_time"],
    )


def get_irtt(irtt_id: str) -> IrttStatusResponse:
    session = ACTIVE_IRTT.get(irtt_id)
    if session is None:
        raise HTTPException(
            status_code=404, detail=f"IRTT session not found: {irtt_id}"
        )
    client_proc: subprocess.Popen = session["client_proc"]
    server_proc: subprocess.Popen = session["server_proc"]
    rc = client_proc.poll()
    return IrttStatusResponse(
        irtt_id=irtt_id,
        status="running" if rc is None else "finished",
        server_address=session["server_address"],
        port=session["port"],
        endpoint_ips=[session["server_address"]],
        start_time=session["start_time"],
        server_alive=server_proc.poll() is None,
        client_returncode=rc,
    )


def delete_irtt(irtt_id: str):
    """Stop the probe and hand back its JSON result.

    The output file lives inside the worker, so the result is returned in the
    response body rather than left for the caller to fetch separately.
    """
    session = ACTIVE_IRTT.get(irtt_id)
    if session is None:
        raise HTTPException(
            status_code=404, detail=f"IRTT session not found: {irtt_id}"
        )

    client_proc: subprocess.Popen = session["client_proc"]
    server_proc: subprocess.Popen = session["server_proc"]

    # The client is given a moment to finish of its own accord: killing it
    # mid-test means no JSON is written at all, and a truncated probe is worth
    # more than none. The server is stopped only after.
    client_stderr = ""
    if client_proc.poll() is None:
        try:
            _, err = client_proc.communicate(timeout=20)
            client_stderr = err.decode(errors="replace")[:500]
        except subprocess.TimeoutExpired:
            _terminate(client_proc)
    else:
        try:
            _, err = client_proc.communicate(timeout=5)
            client_stderr = err.decode(errors="replace")[:500]
        except Exception:
            pass
    _terminate(server_proc)

    result, error = None, None
    out_path = session["out_path"]
    try:
        if os.path.exists(out_path):
            with open(out_path) as fh:
                result = json.load(fh)
        else:
            error = (
                f"irtt wrote no output to {out_path} "
                f"(client rc={client_proc.returncode}): {client_stderr.strip()[:300]}"
            )
    except (ValueError, OSError) as exc:
        error = f"irtt output at {out_path} unreadable: {type(exc).__name__}: {exc}"
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass

    del ACTIVE_IRTT[irtt_id]
    return {
        "irtt_id": irtt_id,
        "status": "stopped",
        "server_address": session["server_address"],
        "port": session["port"],
        "endpoint_ips": [session["server_address"]],
        "client_returncode": client_proc.returncode,
        "request": session["request"],
        "result": result,
        "error": error,
    }


def register_irtt_routes(app: FastAPI) -> None:
    """Attach POST/GET/DELETE /irtt to one profile's app.

    The handlers are defined undecorated above so that all three profiles get
    byte-identical behaviour from one definition; this is the only place the
    routes are bound.
    """
    app.post("/irtt", response_model=IrttResponse)(start_irtt)
    app.get("/irtt/{irtt_id}", response_model=IrttStatusResponse)(get_irtt)
    app.delete("/irtt/{irtt_id}")(delete_irtt)


class NetvibeRequest(BaseModel):
    """One NetVibe probe run: bench here, its probe server in ns2, sensor in ns1."""

    duration_seconds: int = Field(60, ge=1)
    rate_per_second: float = Field(10.0, gt=0, le=10)
    server_address: str = Field(
        "172.16.2.1", description="ns2's veth3 address; the probe server's media"
    )
    signaling_address: str = Field(
        "172.16.2.2",
        description=(
            "Where bench serves the sensor API. Bench runs in the worker's own "
            "namespace, so this is the root side of veth4, reachable from ns1"
        ),
    )
    signaling_port: int = Field(9999, ge=1, le=65535)
    media_port: int = Field(10443, ge=1, le=65535)
    stub_api_port: int = Field(9898, ge=1, le=65535)
    sensor_id: str = Field("pramana-ns1")
    server_namespace: str = Field("ns2")
    client_namespace: str = Field("ns1")
    escalate: bool = Field(False)
    escalation_rates: list[float] = Field(default_factory=lambda: [20.0])
    trigger_delay_ms: int = Field(5, ge=0)


class NetvibeResponse(BaseModel):
    netvibe_id: str
    status: str
    server_address: str
    media_port: int
    endpoint_ips: list[str]
    start_time: str


class NetvibeStatusResponse(BaseModel):
    netvibe_id: str
    status: str
    server_address: str
    media_port: int
    endpoint_ips: list[str]
    start_time: str
    bench_alive: bool
    sensor_returncode: Optional[int] = None


# ═════════════════════════════════════════════════════════════════════════════
#  NetVibe — the lab's continuous-QoE probe, in bench mode
#
#  Bench mode is NetVibe's own testbed path: the production probe server with a
#  SQLite sink instead of Pub/Sub, and no signaling, Postgres, ClickHouse or
#  device login. It takes a `netns` per probe server, so the server is placed in
#  ns2 by configuration rather than by wrapping it, and the sensor
#  (`netvibe-cli test`) runs in ns1. Same start/stop lifecycle as /replay and
#  /irtt, so the probe shares the link with a browser app instead of following
#  it.
# ═════════════════════════════════════════════════════════════════════════════
NETVIBE_BIN_DIR = os.environ.get("NETVIBE_BIN_DIR", "/opt/netvibe/bin")

# The sensor asks the API whether a speed test is due and, with no API to ask,
# decides to run one. An NDT7 test would saturate the link under measurement,
# so bench is paired with a stub that answers "not due". Relying on m-lab being
# unreachable is not a control.
_NETVIBE_STUB_API_SRC = r"""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.endswith("/speedtests/latest"):
            self._send(200, {"last_test": None, "due": False})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        self._send(200, {"ok": True})

    def log_message(self, *a):
        pass

HTTPServer((sys.argv[1], int(sys.argv[2])), H).serve_forever()
"""


def _netvibe_bin(name: str) -> Optional[str]:
    candidate = os.path.join(NETVIBE_BIN_DIR, name)
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return shutil.which(name)


def start_netvibe(cfg: NetvibeRequest) -> NetvibeResponse:
    bench_bin = _netvibe_bin("netvibe-bench")
    cli_bin = _netvibe_bin("netvibe-cli")
    missing = [
        n for n, b in (("netvibe-bench", bench_bin), ("netvibe-cli", cli_bin)) if not b
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=(
                f"NetVibe binaries not found: {', '.join(missing)}. Build them "
                f"from SNL-UCSB/netvibe (make bench netvibe-cli, Go 1.25) into "
                f"{NETVIBE_BIN_DIR} or onto PATH, or set NETVIBE_BIN_DIR."
            ),
        )

    netvibe_id = str(uuid.uuid4())
    work = f"/tmp/netvibe_{netvibe_id[:10]}"
    os.makedirs(work, exist_ok=True)
    out_sqlite = os.path.join(work, "bench.sqlite")
    # state_dir is left for bench to create: it makes it 0700 and refuses one
    # another user owns or that group/other can reach.
    state_dir = os.path.join(work, "state")

    probe: dict = {
        "name": "pramana-fixed",
        "rate_per_second": cfg.rate_per_second,
        "spacing": "fixed",
        "max_gap_ms": 2000,
        "echo_timeout_ms": 5000,
    }
    if cfg.escalate:
        probe.update(
            {
                "name": "pramana-escalating",
                "escalation_rates": list(cfg.escalation_rates),
                "trigger_delay_ms": cfg.trigger_delay_ms,
                "trigger_window_ms": 2000,
                "trigger_percentile": 90,
                "rung_hold_ms": 2000,
                "cooldown_ms": 5000,
                "max_escalated_ms": 600000,
            }
        )
    bench_cfg = {
        "out": out_sqlite,
        "listen": f"{cfg.signaling_address}:{cfg.signaling_port}",
        "state_dir": state_dir,
        "public_ip": cfg.server_address,
        "session_seconds": 86400,
        "probe_servers": [
            {
                "label": "pramana",
                "media_port": cfg.media_port,
                # Bench places its own child; this is what the key is for.
                "netns": cfg.server_namespace,
                "public_ip": cfg.server_address,
            }
        ],
        "probe": probe,
    }
    cfg_path = os.path.join(work, "bench.yaml")
    with open(cfg_path, "w") as fh:
        json.dump(bench_cfg, fh)  # bench reads YAML; JSON is valid YAML

    stub_path = os.path.join(work, "stub_api.py")
    with open(stub_path, "w") as fh:
        fh.write(_NETVIBE_STUB_API_SRC)
    stub_cmd = (
        f"ip netns exec {cfg.server_namespace} python3 {stub_path}"
        f" {cfg.server_address} {cfg.stub_api_port}"
    )
    bench_cmd = (
        f"timeout -k 5 {cfg.duration_seconds + 180} {bench_bin}" f" --config {cfg_path}"
    )
    try:
        stub_proc = subprocess.Popen(
            stub_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        bench_proc = subprocess.Popen(
            bench_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start bench: {exc}")

    # Readiness from bench's own /ready, which names any probe server that is
    # down, rather than a sleep that hides a server which never bound.
    ready_url = f"http://{cfg.signaling_address}:{cfg.signaling_port}/ready"
    deadline = time.time() + 60.0
    ready, last = False, ""
    while time.time() < deadline:
        if bench_proc.poll() is not None:
            out, _ = bench_proc.communicate()
            _terminate(stub_proc)
            raise HTTPException(
                status_code=500,
                detail=(
                    f"netvibe-bench exited during startup (rc="
                    f"{bench_proc.returncode}): "
                    f"{out.decode(errors='replace')[-400:]}"
                ),
            )
        probe_resp = subprocess.run(
            f"curl -s -m 3 {ready_url}",
            shell=True,
            capture_output=True,
            timeout=15,
        )
        body = probe_resp.stdout.decode(errors="replace").replace(" ", "")
        if probe_resp.returncode == 0 and '"ready":true' in body:
            ready = True
            break
        last = body[:160]
        time.sleep(1.0)
    if not ready:
        _terminate(bench_proc)
        _terminate(stub_proc)
        raise HTTPException(
            status_code=504,
            detail=f"netvibe-bench never reported ready at {ready_url}: {last}",
        )

    sensor_cmd = (
        f"timeout -k 5 {cfg.duration_seconds + 120}"
        f" ip netns exec {cfg.client_namespace} {cli_bin} test"
        f" --signaling-host http://{cfg.signaling_address}"
        f" --signaling-port {cfg.signaling_port}"
        f" --api-host http://{cfg.server_address}"
        f" --api-port {cfg.stub_api_port}"
        f" --sensor-id {cfg.sensor_id}"
        # Host candidates only: a STUN-derived candidate could put the media on
        # a path that never crosses the shaped link.
        f" --no-stun"
    )
    try:
        sensor_proc = subprocess.Popen(
            sensor_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
    except Exception as exc:
        _terminate(bench_proc)
        _terminate(stub_proc)
        raise HTTPException(status_code=500, detail=f"Failed to start sensor: {exc}")

    ACTIVE_NETVIBE[netvibe_id] = {
        "netvibe_id": netvibe_id,
        "bench_proc": bench_proc,
        "sensor_proc": sensor_proc,
        "stub_proc": stub_proc,
        "work": work,
        "out_sqlite": out_sqlite,
        "server_address": cfg.server_address,
        "signaling_address": cfg.signaling_address,
        "media_port": cfg.media_port,
        "start_time": datetime.utcnow().isoformat(),
        "request": cfg.model_dump(),
    }
    # Both addresses are the probe's: media on ns2's veth3, the sensor API on
    # the root side of veth4. Registering both keeps either out of the app's
    # per-app pcap and out of the shared-cap sum.
    endpoints = sorted({cfg.server_address, cfg.signaling_address})
    return NetvibeResponse(
        netvibe_id=netvibe_id,
        status="started",
        server_address=cfg.server_address,
        media_port=cfg.media_port,
        endpoint_ips=endpoints,
        start_time=ACTIVE_NETVIBE[netvibe_id]["start_time"],
    )


def get_netvibe(netvibe_id: str) -> NetvibeStatusResponse:
    session = ACTIVE_NETVIBE.get(netvibe_id)
    if session is None:
        raise HTTPException(
            status_code=404, detail=f"NetVibe session not found: {netvibe_id}"
        )
    bench: subprocess.Popen = session["bench_proc"]
    sensor: subprocess.Popen = session["sensor_proc"]
    rc = sensor.poll()
    return NetvibeStatusResponse(
        netvibe_id=netvibe_id,
        status="running" if rc is None else "finished",
        server_address=session["server_address"],
        media_port=session["media_port"],
        endpoint_ips=sorted({session["server_address"], session["signaling_address"]}),
        start_time=session["start_time"],
        bench_alive=bench.poll() is None,
        sensor_returncode=rc,
    )


def delete_netvibe(netvibe_id: str):
    """Stop the probe and say where its records are.

    Order matters: the sensor is stopped first, then bench, which on SIGTERM
    ends its sessions, waits for its servers to flush and writes
    `meta.stopped_at_ns`. The file is only complete once bench exits, so
    killing bench first would lose the tail of the run.
    """
    session = ACTIVE_NETVIBE.get(netvibe_id)
    if session is None:
        raise HTTPException(
            status_code=404, detail=f"NetVibe session not found: {netvibe_id}"
        )
    _terminate(session["sensor_proc"])
    bench: subprocess.Popen = session["bench_proc"]
    if bench.poll() is None:
        bench.terminate()
        try:
            bench.wait(timeout=30)
        except subprocess.TimeoutExpired:
            bench.kill()
    _terminate(session["stub_proc"])

    out = session["out_sqlite"]
    error = None
    size = None
    if os.path.exists(out):
        size = os.path.getsize(out)
        if size == 0:
            error = f"bench wrote an empty file at {out}"
    else:
        error = f"bench wrote no file at {out}"

    del ACTIVE_NETVIBE[netvibe_id]
    return {
        "netvibe_id": netvibe_id,
        "status": "stopped",
        "server_address": session["server_address"],
        "media_port": session["media_port"],
        "endpoint_ips": sorted(
            {session["server_address"], session["signaling_address"]}
        ),
        # The file stays in the worker for the caller to fetch; it can be large
        # and is not JSON.
        "out_sqlite": out,
        "out_sqlite_bytes": size,
        "work_dir": session["work"],
        "request": session["request"],
        "error": error,
    }


def register_netvibe_routes(app: FastAPI) -> None:
    """Attach POST/GET/DELETE /netvibe to one profile's app.

    Same reasoning as `register_irtt_routes`: the handlers above are defined
    undecorated so all three profiles get identical behaviour from one
    definition, and this is the only place the routes are bound.
    """
    app.post("/netvibe", response_model=NetvibeResponse)(start_netvibe)
    app.get("/netvibe/{netvibe_id}", response_model=NetvibeStatusResponse)(get_netvibe)
    app.delete("/netvibe/{netvibe_id}")(delete_netvibe)
