"""IRTT probe endpoint, shared by every substrate-worker profile.

An isochronous UDP round-trip measurement across the shaped link: server in
ns2, client in ns1, so the probe traverses the same bottleneck (`veth2`) the
browser's traffic does and is measured by the same capture.

This lives in its own module because the three worker profiles -- `main`,
`main_local` and `main_aws` -- each need the identical endpoint, and three
hand-kept copies of a 276-line block drift. The profiles differ in how they
*shape* (WAN-interface detection, qtrace polling); the probe does not differ
at all, so it is defined once here and attached to each profile's app by
`register_irtt_routes`.

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
