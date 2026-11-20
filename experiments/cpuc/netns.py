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
from pathlib import Path
from typing import Optional

WORKER_CONTAINER = "substrate-worker"
CLIENT_NS = "ns1"
SERVER_NS = "ns2"
CLIENT_IFACE = "veth1"  # ns1 side: egress = upload
SERVER_IFACE = "veth3"  # ns2 side: egress = download
SERVER_ADDR = "172.16.2.1"
CLIENT_ADDR = "172.16.1.1"

# Where replayed CTP packets are rewritten to land -- distinct from
# CLIENT_ADDR, so a capture can split app traffic from cross-traffic by
# destination IP alone. Must match the name registered against
# `ExperimentConfig.extra_probe_endpoint_ips` in drive_pramana.py/teams.py,
# or link_interference will (correctly, by its own rules) flag this traffic
# as unexplained contention.
CTP_PNAT_IP = "172.16.1.20"
CTP_PNAT_RULE = f"169.231.0.0/16:{CTP_PNAT_IP}"
# Inside the worker container, bind-mounted read-only from the host's CTP
# corpus (e.g. /home/student/ctp_data/download). NOT on the host: confirmed
# live that tcpreplay-edit is absent from the VM host (`which tcpreplay-edit`
# -> nothing) but present in the worker image
# (`docker exec substrate-worker which tcpreplay-edit` -> /usr/bin/tcpreplay-
# edit, v4.4.4) -- so replay must go through `docker exec`, never host nsenter.
CTP_CONTAINER_DIR = "/ctpsrc"


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


def assert_ctp_mount(container: str = WORKER_CONTAINER) -> None:
    """The worker must have the CTP corpus bind-mounted before replay can work.

    `/replay`'s own $CTP_DIR mount is a separate thing from this -- this harness
    drives tcpreplay-edit itself (see start_ctp_replay), so it needs its own
    mount, checked up front rather than discovered as a 400/ENOENT mid-batch.
    """
    out = subprocess.run(
        ["docker", "exec", container, "test", "-d", CTP_CONTAINER_DIR],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if out.returncode != 0:
        raise NamespaceError(
            f"{container!r} has no {CTP_CONTAINER_DIR} mount, so CTP replay "
            f"would read nothing. This is a one-time container recreation, "
            f"not something a flag can fix at run time:\n"
            f"  docker stop {container} && docker rm {container}\n"
            f"  docker run -d --name {container} --privileged --pid host \\\n"
            f"    -v /home/student/ctp_data/download:{CTP_CONTAINER_DIR}:ro \\\n"
            f"    <the rest of this container's existing mounts/env/image>"
        )


def start_ctp_replay(
    ctp_name: str,
    ctp_host_dir: str,
    seconds: int,
    pnat: str = CTP_PNAT_RULE,
    container: str = WORKER_CONTAINER,
) -> dict[str, object]:
    """Replay one CTP profile's download side through ns2:veth3, looped.

    Injected on the SERVER_IFACE inside ns2, so it rides the same shaped
    veth4->veth2 path the app's downlink crosses -- the same mechanism
    `run_direct_teams_only_experiment.py` already uses standalone. `--pnat`
    rewrites the replayed packets' destination to CTP_PNAT_IP, which is how a
    capture (and `link_interference`, once that IP's bucket is named via
    `ExperimentConfig.extra_probe_endpoint_ips`) tells this apart from the
    app's own traffic.

    `ctp_host_dir` is read on the HOST only to fail fast if the profile
    doesn't exist -- the replay itself reads the container's own bind-mounted
    copy at CTP_CONTAINER_DIR (see assert_ctp_mount), since tcpreplay-edit
    cannot run via host nsenter the way tc/iperf3 do.

    Fire-and-forget: `--loop=0` repeats the (short) profile pcap until
    `--duration` elapses, started just before the app's own measurement
    begins. This does not have Haarika's precise first-sample synchronization
    (arm_measurement_window in run_direct_teams_experiment.py) -- `seconds`
    should be given some margin over the app's duration_s to cover that.
    """
    source = Path(ctp_host_dir) / f"{ctp_name}.pcap"
    if not source.is_file():
        raise NamespaceError(f"CTP profile does not exist on host: {source}")
    assert_ctp_mount(container)
    cmd = (
        f"ip netns exec {SERVER_NS} tcpreplay-edit -i {SERVER_IFACE} "
        f"--pnat={pnat} --loop=0 --duration={seconds} "
        f"{CTP_CONTAINER_DIR}/{ctp_name}.pcap"
    )
    subprocess.run(
        ["docker", "exec", "--detach", container, "sh", "-c", cmd],
        check=True,
        timeout=30,
    )
    return {
        "ctp_profile": ctp_name,
        "ctp_direction": "download",
        "ctp_pnat": pnat,
        "ctp_replay_seconds": seconds,
    }


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
