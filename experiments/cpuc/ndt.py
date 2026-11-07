"""ndt7-client in JSON mode, and parsing its summary.

The reference implementation runs `ndt7-client --no-verify --scheme wss
--server <ip>` and keeps the raw human-readable stdout, which cannot be read
back into a CSV. The only change needed is `-format=json`; everything else
about how it is invoked is kept, so our numbers stay comparable with the
published speedtest dataset.

Flags verified against the binary our own substrate-worker image builds
(m-lab/ndt7-client-go v0.9.1, `-help`):

    -format value   output format to use: 'human' or 'json' for batch processing
    -server string  optional ndt7 server hostname
    -no-verify      skip TLS certificate verification
    -scheme value   "wss" or "ws"
    -timeout        time after which the test is aborted (default 55s)

WHAT ndt7 DOES NOT GIVE US: packet loss. The summary carries
`Retransmission` = BytesRetrans/BytesSent from TCPInfo, which is a
retransmission ratio, not a packet-loss rate. IQB's thresholds are stated as
packet loss (1% / 0.1%), so the analysis step is comparing a retransmission
proxy against a loss threshold. That is recorded in the row as
`ndt_retransmission` as well as `ndt_loss`, with the same value and a basis
string, so nobody later reads `ndt_loss` as though ndt7 measured loss.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

WORKER_CONTAINER = "substrate-worker"
# The worker image already carries the ndt7 client at this path.
NDT_BIN = os.environ.get("CPUC_NDT_BIN", "/usr/local/bin/ndt-client")
CLIENT_NS = "ns1"


def _ns_prefix(ns: str) -> str:
    """Run the client inside the worker CONTAINER's ns1, not via nsenter.

    nsenter changes only the network namespace, so the process keeps the HOST's
    /etc/resolv.conf -- 127.0.0.53 (systemd-resolved), which is not reachable
    from inside ns1. The first pilot NDT run failed exactly there:

        lookup locate.measurementlab.net on 127.0.0.53:53: connection refused

    and ndt7-client still emitted a summary full of zeros. Entering through the
    container picks up ITS resolv.conf (8.8.8.8), which resolves and reaches
    M-Lab's Locate API -- the same path the browser uses to reach YouTube.

    `env -u TIMEOUT` is required here: the worker container is started with
    TIMEOUT=300000 (for the substrate HTTP server's own use), and ndt-client
    also parses a -timeout flag from that same env var name via its flag
    library. Without stripping it, every run fails immediately with
    "failed to parse flags (... Could not set argument timeout to the value
    of environment variable TIMEOUT=\"300000\" ...)" and emits no JSON
    summary at all -- confirmed live, 21/21 runs invalid before this fix.
    """
    return f"docker exec {WORKER_CONTAINER} ip netns exec {ns} env -u TIMEOUT "


RETRANSMISSION_BASIS = (
    "ndt7 Retransmission = BytesRetrans/BytesSent from TCPInfo; a "
    "retransmission ratio, NOT a packet-loss rate"
)


@dataclass
class NdtResult:
    ok: bool
    download_mbps: Optional[float] = None
    upload_mbps: Optional[float] = None
    download_minrtt_ms: Optional[float] = None
    upload_minrtt_ms: Optional[float] = None
    download_retransmission: Optional[float] = None
    upload_retransmission: Optional[float] = None
    server_fqdn: Optional[str] = None
    server_ip: Optional[str] = None
    returncode: Optional[int] = None
    raw_path: Optional[str] = None
    error: str = ""

    def as_row(self) -> dict:
        return {
            "ndt_down": self.download_mbps,
            "ndt_up": self.upload_mbps,
            # The plan doc's schema names one MinRTT. ndt7 reports one per
            # subtest; download's is the one taken, and upload's is kept beside
            # it rather than averaged into something neither client measured.
            "ndt_minrtt": self.download_minrtt_ms,
            "ndt_minrtt_upload": self.upload_minrtt_ms,
            "ndt_loss": self.download_retransmission,
            "ndt_retransmission": self.download_retransmission,
            "ndt_retransmission_upload": self.upload_retransmission,
            "ndt_loss_basis": RETRANSMISSION_BASIS,
            "ndt_server": self.server_fqdn or self.server_ip,
            "ndt_ok": self.ok,
            "ndt_error": self.error,
            "ndt_raw_path": self.raw_path,
        }


def _failures(text: str) -> list[str]:
    """The Failure strings ndt7-client reported, for the row's error column."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("Key") == "error":
            val = obj.get("Value") or {}
            f = val.get("Failure") or ""
            if f:
                out.append(f"{val.get('Test', '?')}: {f[:200]}")
    return out


def _vu(node: Any) -> Optional[float]:
    """Read a {"Value": x, "Unit": "..."} pair."""
    if isinstance(node, dict) and "Value" in node:
        try:
            return float(node["Value"])
        except (TypeError, ValueError):
            return None
    return None


def parse_ndt_json(text: str) -> NdtResult:
    """Pull the summary out of `-format=json` output.

    The emitter writes newline-delimited JSON events and the summary is one of
    them, so this scans every parseable line and keeps the last object that
    carries a Download or Upload subtest. Scanning rather than taking the last
    line means an extra trailing event cannot break parsing.
    """
    best: Optional[dict] = None
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        # The summary may be the object itself or wrapped as {"Key","Value"}.
        for cand in (obj, obj.get("Value") if isinstance(obj, dict) else None):
            if isinstance(cand, dict) and (
                isinstance(cand.get("Download"), dict)
                or isinstance(cand.get("Upload"), dict)
            ):
                best = cand
    if best is None:
        return NdtResult(ok=False, error="no ndt7 summary found in JSON output")

    dl = best.get("Download") or {}
    ul = best.get("Upload") or {}
    res = NdtResult(
        ok=True,
        download_mbps=_vu(dl.get("Throughput")),
        upload_mbps=_vu(ul.get("Throughput")),
        download_minrtt_ms=_vu(dl.get("Latency")),
        upload_minrtt_ms=_vu(ul.get("Latency")),
        download_retransmission=_vu(dl.get("Retransmission")),
        upload_retransmission=_vu(ul.get("Retransmission")),
        server_fqdn=best.get("ServerFQDN"),
        server_ip=best.get("ServerIP"),
    )
    if res.download_mbps is None and res.upload_mbps is None:
        return NdtResult(ok=False, error="summary carried no throughput")
    # ndt7-client emits a well-formed summary of ZEROS when the test never ran
    # (for example when Locate could not be resolved). Accepting that would
    # write 0.0 Mb/s into the dataset as though it had been measured, and IQB's
    # verdict would then read it as a link that fails every threshold.
    if not (res.download_mbps or 0) and not (res.upload_mbps or 0):
        errs = "; ".join(_failures(text)) or "no failure reported"
        return NdtResult(
            ok=False,
            error=f"ndt7 summary is all zeros, so no test completed: {errs}",
            server_fqdn=res.server_fqdn,
        )
    return res


def run_ndt(
    server: Optional[str],
    raw_path: str,
    scheme: str = "wss",
    no_verify: bool = True,
    timeout_s: int = 120,
    in_namespace: bool = True,
    runner=None,
) -> NdtResult:
    """Run one ndt7 test and write the raw JSON beside the parsed row.

    The raw JSON is always kept: the plan doc asks for it, and a parsed row
    with no raw output behind it cannot be rechecked after the fact.
    """
    # No server: let the client ask M-Lab's Locate API for the nearest one.
    # The lab's own ndt-server (10.208.20.119:4443) is unreachable from ns1, and
    # Locate answers 200 there, so this is the path that works. It also means the
    # measured MinRTT is our netem delay PLUS the real path to the chosen server;
    # `ndt_minrtt` is recorded so that base is recoverable after the fact.
    cmd = (
        f"{NDT_BIN} -format=json -scheme {scheme} "
        f"{'-no-verify ' if no_verify else ''}"
        + (f"-server {server}" if server else "")
    ).strip()
    if in_namespace:
        cmd = f"{_ns_prefix(CLIENT_NS)}{cmd}"

    if runner is None:

        def runner(c, timeout):  # noqa: ANN001
            return subprocess.run(
                c, shell=True, capture_output=True, text=True, timeout=timeout
            )

    try:
        proc = runner(cmd, timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001 - a dead probe must not kill the batch
        return NdtResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    os.makedirs(os.path.dirname(raw_path), exist_ok=True)
    # Never overwrite: the plan doc says so, and a silently reused path makes
    # two runs indistinguishable.
    if os.path.exists(raw_path):
        return NdtResult(
            ok=False,
            returncode=proc.returncode,
            error=f"raw path already exists, refusing to overwrite: {raw_path}",
        )
    with open(raw_path, "w") as fh:
        fh.write(text)

    res = parse_ndt_json(text)
    res.returncode = proc.returncode
    res.raw_path = raw_path
    if proc.returncode != 0 and res.ok:
        # A non-zero exit with a usable summary is worth recording, not hiding.
        res.error = f"ndt7-client exited {proc.returncode} but emitted a summary"
    elif proc.returncode != 0:
        res.error = res.error or f"ndt7-client exited {proc.returncode}"
    return res
