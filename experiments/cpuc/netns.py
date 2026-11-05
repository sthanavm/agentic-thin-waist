"""Host-side namespace access and the packet-loss implementation.

The worker's ns1/ns2 live inside the `substrate-worker` container, so they are
not in the host's /run/netns. They are reachable through the container's root
filesystem, which is the same route `pramana_helpers` already uses for the IRTT
and NetVibe host fallbacks:

    /proc/<worker pid>/root/run/netns/<ns>   +   sudo nsenter --net=...

WHY LOSS IS HERE AND NOT IN THE WORKER. `substrate_shape()` posts
download_mbps, upload_mbps, latency_ms, qdisc and buffer_packets -- and no loss
field. `ExperimentConfig.loss_pct` reaches the run slug and record.json but is
never applied, so a run configured for 1% loss would be LABELLED 1% while the
link dropped nothing. Nothing archived is affected (every stored run is 0pct),
but Stage 1 needs loss in both directions, so the CPUC layer applies it itself
rather than changing worker code.

Measured qdisc layout after a /shape call, which is what this builds on:

    root ns  veth2   htb <download>Mbit -> pfifo     (download rate)
    root ns  veth4   htb <upload>Mbit   -> pfifo     (upload rate)
    ns2      veth3   netem delay <rtt>ms             (the whole RTT, one way)
    ns1      veth1   noqueue                         (nothing)

So loss goes on:
  * DOWNLOAD: the existing ns2:veth3 netem, changed in place to carry
    `delay <rtt>ms loss <p>%`. Changing it rather than stacking a second netem
    keeps one queue on the path, so the delay the worker configured is still
    the delay the packet sees.
  * UPLOAD: a new netem on ns1:veth1, which only sees packets leaving ns1.

`/shape` resets both, so `install_shape_hook` wraps `substrate_shape` and
reapplies loss immediately after every call to it, including any re-shape
inside a run. The wrapper lives here; `pramana_helpers` is not modified.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from typing import Optional

WORKER_CONTAINER = "substrate-worker"
CLIENT_NS = "ns1"
SERVER_NS = "ns2"
CLIENT_IFACE = "veth1"  # ns1 side: egress = upload
SERVER_IFACE = "veth3"  # ns2 side: egress = download
SERVER_ADDR = "172.16.2.1"
CLIENT_ADDR = "172.16.1.1"


class NamespaceError(RuntimeError):
    pass


def worker_pid(container: str = WORKER_CONTAINER) -> int:
    out = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Pid}}", container],
        capture_output=True,
        text=True,
        timeout=30,
    )
    pid = out.stdout.strip()
    if out.returncode != 0 or not pid.isdigit() or pid == "0":
        raise NamespaceError(
            f"could not read pid of {container!r}: {out.stderr.strip()[:200]}"
        )
    return int(pid)


def netns_path(ns: str, container: str = WORKER_CONTAINER) -> str:
    return f"/proc/{worker_pid(container)}/root/run/netns/{ns}"


def ns_exec(
    ns: str, cmd: str, check: bool = True, timeout: Optional[float] = 120.0
) -> subprocess.CompletedProcess:
    """Run `cmd` inside one of the worker's namespaces, from the host."""
    full = f"sudo nsenter --net={shlex.quote(netns_path(ns))} sh -c {shlex.quote(cmd)}"
    return subprocess.run(
        full, shell=True, check=check, timeout=timeout, capture_output=True, text=True
    )


def _netem_spec(rtt_ms: float, loss_pct: float) -> str:
    parts = []
    if rtt_ms > 0:
        parts.append(f"delay {rtt_ms:g}ms")
    if loss_pct > 0:
        parts.append(f"loss {loss_pct:g}%")
    return " ".join(parts)


def apply_loss(loss_pct: float, rtt_ms: float) -> dict[str, str]:
    """Put `loss_pct` on BOTH directions, preserving the configured delay.

    Returns the qdisc lines actually in place afterwards, so the caller can
    record what was applied rather than what was intended.
    """
    # DOWNLOAD: change the worker's netem in place so delay survives.
    dl_spec = _netem_spec(rtt_ms, loss_pct)
    if dl_spec:
        existing = ns_exec(
            SERVER_NS, f"tc qdisc show dev {SERVER_IFACE}", check=False
        ).stdout
        verb = "change" if "netem" in existing else "add"
        ns_exec(SERVER_NS, f"tc qdisc {verb} dev {SERVER_IFACE} root netem {dl_spec}")

    # UPLOAD: ns1's egress carries no qdisc of its own, so add one.
    ns_exec(
        CLIENT_NS,
        f"tc qdisc del dev {CLIENT_IFACE} root 2>/dev/null || true",
        check=False,
    )
    if loss_pct > 0:
        ns_exec(
            CLIENT_NS, f"tc qdisc add dev {CLIENT_IFACE} root netem loss {loss_pct:g}%"
        )

    return {
        "download": ns_exec(
            SERVER_NS, f"tc qdisc show dev {SERVER_IFACE}", check=False
        ).stdout.strip(),
        "upload": ns_exec(
            CLIENT_NS, f"tc qdisc show dev {CLIENT_IFACE}", check=False
        ).stdout.strip(),
    }


def clear_loss(rtt_ms: float = 0.0) -> None:
    """Remove the upload netem and drop loss from the download one."""
    ns_exec(
        CLIENT_NS,
        f"tc qdisc del dev {CLIENT_IFACE} root 2>/dev/null || true",
        check=False,
    )
    spec = _netem_spec(rtt_ms, 0.0)
    if spec:
        ns_exec(
            SERVER_NS,
            f"tc qdisc change dev {SERVER_IFACE} root netem {spec}",
            check=False,
        )


def install_shape_hook(helpers_module, loss_pct: float, rtt_ms: float):
    """Make every `substrate_shape()` call be followed by applying loss.

    /shape rebuilds the qdiscs, so loss applied before a run would be wiped by
    the run's own shaping. Wrapping the function is the only place to reapply
    it that cannot be raced, and it leaves pramana_helpers untouched on disk.

    Returns a callable that restores the original.
    """
    original = helpers_module.substrate_shape

    def shape_then_loss(cfg, verify: bool = False):
        result = original(cfg, verify=verify)
        if loss_pct > 0:
            applied = apply_loss(loss_pct, rtt_ms)
            if isinstance(result, dict):
                result["cpuc_loss_applied"] = applied
        return result

    helpers_module.substrate_shape = shape_then_loss

    def restore() -> None:
        helpers_module.substrate_shape = original

    return restore


def container_netns_path(container: str = WORKER_CONTAINER) -> str:
    """The worker container's OWN network namespace.

    veth2/veth4 live here, not in ns1/ns2 and not on the host, which is why
    host `tc` cannot see them.
    """
    return f"/proc/{worker_pid(container)}/ns/net"


def _iface_bytes(iface: str) -> Optional[tuple[int, int]]:
    """(rx, tx) byte counters for an interface in the worker's own namespace."""
    out = subprocess.run(
        f"sudo nsenter --net={shlex.quote(container_netns_path())} "
        f"cat /proc/net/dev",
        shell=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    for line in out.stdout.splitlines():
        name, _, rest = line.partition(":")
        if name.strip() != iface:
            continue
        f = rest.split()
        if len(f) >= 9:
            return int(f[0]), int(f[8])
    return None


def link_idle_wait(
    iface: str = "veth2",
    max_mbps: float = 0.5,
    hold_s: float = 5.0,
    timeout_s: float = 120.0,
    sample_s: float = 1.0,
) -> tuple[bool, float]:
    """Wait until the shaped link has been quiet for `hold_s`.

    The load gate alone is not enough. It reads a 1-minute load average, which
    lags by far more than the gap between two runs, so calibration was starting
    while the previous run's Chrome was still tearing down: measured RTTs of
    242, 144 and 121 ms against a 100 ms target, with iperf3 returning no
    result at all. Watching bytes on the bottleneck interface is the direct
    signal and reacts in a second.

    Returns (quiet, last_observed_mbps).
    """
    deadline = time.monotonic() + timeout_s
    quiet_since: Optional[float] = None
    last = 0.0
    prev = _iface_bytes(iface)
    while time.monotonic() < deadline:
        time.sleep(sample_s)
        cur = _iface_bytes(iface)
        if prev is None or cur is None:
            prev = cur
            continue
        delta = (cur[0] - prev[0]) + (cur[1] - prev[1])
        prev = cur
        last = (delta * 8.0) / sample_s / 1e6
        if last <= max_mbps:
            quiet_since = quiet_since or time.monotonic()
            if time.monotonic() - quiet_since >= hold_s:
                return True, last
        else:
            quiet_since = None
    return False, last


def start_iperf3_server(port: int = 5201) -> None:
    """An iperf3 server in ns2 for the calibration gate.

    Idempotent, and left running between runs: starting one per run would race
    with the previous one's TIME_WAIT on the same port. Checked against the
    HOST process table rather than `ss` inside the namespace -- the server is a
    host process that merely lives in that netns, and the previous `ss`-based
    check let 12 duplicate spawns through, every one of which failed to bind
    and was left as a zombie.
    """
    probe = subprocess.run(
        ["pgrep", "-f", f"iperf3 -s -p {port}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if probe.returncode == 0 and probe.stdout.strip():
        return
    subprocess.run(
        f"sudo nsenter --net={shlex.quote(netns_path(SERVER_NS))} "
        f"setsid iperf3 -s -p {port} >/tmp/cpuc_iperf3_ns2.log 2>&1 &",
        shell=True,
        check=False,
        timeout=30,
    )
    time.sleep(1.0)
