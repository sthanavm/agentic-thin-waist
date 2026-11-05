"""Shaping, latency, loss and the pre-run calibration gate.

Borrowed from SNL-UCSB/thinwaist-contextual-datasets
`speedtest_data_collection/src/controler.py`, which already does the two things
we want to keep identical to the published dataset:

  * HTB rate limiting with a `pfifo` leaf, applied per direction -- `veth2`
    carries download, `veth4` upload.
  * netem delay split HALF PER DIRECTION, so the two halves sum to the target
    RTT rather than doubling it. Egress is delayed on `veth3` inside ns2;
    ingress is redirected to an `ifb0` and delayed there, because netem cannot
    delay ingress in place.

Three things it does not have, added here:

  1. `tc netem loss`, in BOTH directions. Stage 1 runs every corner twice, once
     clean and once at IQB's loss rate for that corner.
  2. A calibration gate: iperf3 and ping before every run, skipping the
     condition if rate or RTT is off by more than 5%, and checking measured
     loss against the configured value.
  3. Nothing here replays CTP. Stage 1 has no cross traffic, so the reference
     implementation's profile loop does not apply; see `runner.py`.

The loss and delay share one netem qdisc per direction on purpose. Stacking a
second netem under the first would also work, but it makes the effective delay
depend on which qdisc drops first, and the dataset would not be comparable with
the published one.
"""

from __future__ import annotations

import math
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Optional

IPERF3_PORT = 5201


def _ns(ns: str) -> str:
    """Prefix that runs a host binary inside one of the worker's namespaces.

    ns1/ns2 live inside the substrate-worker container, so `ip netns exec`
    on the host cannot see them; see cpuc/netns.py for the route used.
    Resolved lazily and cached, because the worker's pid changes if it is
    restarted and a stale path would silently probe the wrong namespace.
    """
    from . import netns  # noqa: PLC0415 - avoids a hard dep for unit tests

    path = netns.netns_path(ns)  # raises if the worker is not running
    return f"sudo nsenter --net={path} "


DOWNSTREAM_IFACE = "veth2"  # host side of ns1: carries download
UPSTREAM_IFACE = "veth4"  # host side of ns2: carries upload
NS2_IFACE = "veth3"  # where netem delay/loss lives
CLIENT_NS = "ns1"
SERVER_NS = "ns2"
SERVER_ADDR = "172.16.2.1"

# Plan doc: "Skip any setting that is off by more than 5%."
CALIBRATION_TOLERANCE = 0.05


def _run(cmd: str, check: bool = True, timeout: Optional[float] = 120.0):
    return subprocess.run(
        cmd, shell=True, check=check, timeout=timeout, capture_output=True, text=True
    )


def shaping(
    download_mbps: float, upload_mbps: float, qdisc: str = "pfifo", r2q: int = 100
) -> None:
    """HTB rate limit per direction with `qdisc` as the leaf."""
    for iface, rate in (
        (DOWNSTREAM_IFACE, download_mbps),
        (UPSTREAM_IFACE, upload_mbps),
    ):
        _run(
            f"tc qdisc del dev {iface} root 2>/dev/null || true; "
            f"tc qdisc add dev {iface} root handle 1: htb default 10 r2q {r2q} && "
            f"tc class add dev {iface} parent 1: classid 1:10 htb "
            f"rate {rate}Mbit ceil {rate}Mbit && "
            f"tc qdisc add dev {iface} parent 1:10 {qdisc}"
        )


def _netem_args(half_delay_ms: float, loss_pct: float) -> str:
    parts = []
    if half_delay_ms > 0:
        parts.append(f"delay {half_delay_ms}ms")
    if loss_pct > 0:
        # `tc netem loss <p>%` is Bernoulli per packet, which is what the plan
        # doc asks for ("random loss via tc netem").
        parts.append(f"loss {loss_pct}%")
    return " ".join(parts)


def latency_and_loss(rtt_ms: float, loss_pct: float = 0.0) -> None:
    """Apply half the RTT and the per-direction loss on each direction.

    `loss_pct` is the ONE-WAY loss applied to each direction, which is what
    `tc netem loss` takes. A round trip therefore sees 1-(1-p)^2, nearly 2p for
    small p -- see `expected_round_trip_loss`. Getting that wrong is the
    difference between a calibration gate that passes and one that rejects
    every loss condition.
    """
    half = rtt_ms / 2.0
    args = _netem_args(half, loss_pct)

    # Always tear down first, so a previous condition cannot leak into this one.
    _run(
        f"ip netns exec {SERVER_NS} tc qdisc del dev {NS2_IFACE} root 2>/dev/null || true; "
        f"ip netns exec {SERVER_NS} tc qdisc del dev {NS2_IFACE} ingress 2>/dev/null || true; "
        f"ip netns exec {SERVER_NS} ip link del ifb0 2>/dev/null || true",
        check=False,
    )
    if not args:
        return  # 0 ms and no loss: leave the path clean

    # egress: packets leaving veth3 (towards the client) -- the download half
    _run(f"ip netns exec {SERVER_NS} tc qdisc add dev {NS2_IFACE} root netem {args}")

    # ingress: netem cannot delay ingress in place, so mirror to an ifb and
    # delay there -- the upload half.
    _run(
        f"ip netns exec {SERVER_NS} ip link add ifb0 type ifb && "
        f"ip netns exec {SERVER_NS} ip link set ifb0 up && "
        f"ip netns exec {SERVER_NS} tc qdisc add dev {NS2_IFACE} ingress && "
        f"ip netns exec {SERVER_NS} tc filter add dev {NS2_IFACE} parent ffff: "
        f"protocol all u32 match u32 0 0 action mirred egress redirect dev ifb0 && "
        f"ip netns exec {SERVER_NS} tc qdisc add dev ifb0 root netem {args}"
    )


def expected_round_trip_loss(one_way_loss_pct: float) -> float:
    """Loss a round trip sees when each direction drops `one_way_loss_pct`."""
    p = one_way_loss_pct / 100.0
    return (1.0 - (1.0 - p) ** 2) * 100.0


def loss_check_packets_needed(
    one_way_loss_pct: float, rel_precision: float = 0.15
) -> int:
    """How many pings it takes to measure this loss rate at all.

    At IQB's high-quality 0.1% one-way loss a round trip drops ~0.2% of
    packets, so 100 pings expect 0.2 losses: a result of 0 is entirely
    consistent with the configuration and verifies nothing. This returns the n
    at which the binomial standard error is `rel_precision` of the expected
    rate, which is what `calibrate` uses to decide whether a loss check is
    capable of failing before it reports a verdict.
    """
    p = expected_round_trip_loss(one_way_loss_pct) / 100.0
    if p <= 0:
        return 0
    # se = sqrt(p(1-p)/n); want se <= rel_precision * p.
    #
    # The default rel_precision is 0.15 rather than something looser because
    # the check has to be able to catch the specific mistake this module is
    # most likely to make: applying loss in one direction only, or comparing a
    # one-way figure against a round-trip measurement. At 1% one-way the
    # difference between those two readings is 0.99 pp, and a 3-sigma band only
    # separates them from n ~ 2200 upwards. At 0.5 it would not, and the gate
    # would pass a half-configured link.
    return int(math.ceil((1.0 - p) / (p * rel_precision**2)))


@dataclass
class CalibrationResult:
    ok: bool
    skip_reason: str = ""
    target_down_mbps: float = 0.0
    target_up_mbps: float = 0.0
    target_rtt_ms: float = 0.0
    measured_down_mbps: Optional[float] = None
    measured_up_mbps: Optional[float] = None
    measured_rtt_ms: Optional[float] = None
    configured_loss_pct: float = 0.0
    expected_rt_loss_pct: float = 0.0
    measured_rt_loss_pct: Optional[float] = None
    measured_loss_down_pct: Optional[float] = None
    measured_loss_up_pct: Optional[float] = None
    loss_probe: str = "none"
    loss_verified: Optional[bool] = None
    loss_note: str = ""
    ping_packets: int = 0
    failures: list[str] = field(default_factory=list)

    def as_row(self) -> dict:
        return {
            "calib_ok": self.ok,
            "calib_skip_reason": self.skip_reason,
            "calib_down_mbps": self.measured_down_mbps,
            "calib_up_mbps": self.measured_up_mbps,
            "calib_rtt_ms": self.measured_rtt_ms,
            "calib_loss_pct": self.measured_rt_loss_pct,
            "calib_loss_down_pct": self.measured_loss_down_pct,
            "calib_loss_up_pct": self.measured_loss_up_pct,
            "calib_loss_probe": self.loss_probe,
            "calib_loss_expected_pct": self.expected_rt_loss_pct,
            "calib_loss_verified": self.loss_verified,
            "calib_loss_note": self.loss_note,
            "calib_ping_packets": self.ping_packets,
        }


def _parse_iperf3_mbps(stdout: str, reverse: bool) -> Optional[float]:
    """bits_per_second from iperf3 --json, receiver side."""
    import json

    try:
        d = json.loads(stdout)
    except Exception:
        return None
    end = d.get("end") or {}
    node = end.get("sum_received") or end.get("sum_sent") or {}
    bps = node.get("bits_per_second")
    return float(bps) / 1e6 if bps else None


def _parse_iperf3_udp_loss(stdout: str) -> tuple[Optional[float], int]:
    """(lost_percent, packet_count) from `iperf3 -u --json`.

    This measures ONE-WAY loss on the direction under test, which is what
    `tc netem loss` is configured in. Comparing that directly avoids the
    round-trip conversion entirely, and UDP sends orders of magnitude more
    probes per second than ping, so 0.1% is resolvable in seconds instead of
    the ~222 s a ping run would need.
    """
    import json

    try:
        d = json.loads(stdout)
    except Exception:
        return None, 0
    node = (d.get("end") or {}).get("sum") or {}
    lost = node.get("lost_percent")
    pkts = node.get("packets") or 0
    try:
        return (float(lost) if lost is not None else None), int(pkts)
    except (TypeError, ValueError):
        return None, 0


def _parse_iperf3_udp_mbps(stdout: str) -> Optional[float]:
    """Receiver-side throughput from `iperf3 -u --json`, in Mb/s.

    This is what reveals the shaper's ceiling on a loss condition: UDP is sent
    above the configured rate, so what arrives is bounded by the shaper, not by
    a congestion controller reacting to drops.
    """
    import json

    try:
        d = json.loads(stdout)
    except Exception:
        return None
    node = (d.get("end") or {}).get("sum") or {}
    bps = node.get("bits_per_second")
    lost = node.get("lost_percent")
    if bps is None:
        return None
    rate = float(bps) / 1e6
    # iperf3's UDP "sum" is the SENT rate; subtract what did not arrive.
    if isinstance(lost, (int, float)):
        rate *= 1.0 - float(lost) / 100.0
    return rate


def _parse_ping(stdout: str) -> tuple[Optional[float], Optional[float], int]:
    """(avg_rtt_ms, loss_pct, packets_transmitted) from ping output."""
    rtt = None
    m = re.search(r"=\s*[\d.]+/([\d.]+)/", stdout)
    if m:
        rtt = float(m.group(1))
    loss = None
    m = re.search(r"([\d.]+)%\s*packet loss", stdout)
    if m:
        loss = float(m.group(1))
    sent = 0
    m = re.search(r"(\d+)\s+packets transmitted", stdout)
    if m:
        sent = int(m.group(1))
    return rtt, loss, sent


def calibrate(
    target_down_mbps: float,
    target_up_mbps: float,
    target_rtt_ms: float,
    configured_loss_pct: float = 0.0,
    tolerance: float = CALIBRATION_TOLERANCE,
    iperf_seconds: int = 5,
    ping_count: Optional[int] = None,
    runner=_run,
    ns_prefix=_ns,
) -> CalibrationResult:
    """Check the shaper actually holds before spending 90 s on a run.

    Returns a result whose `ok` is False when the condition should be SKIPPED
    rather than measured, per the plan doc's 5% rule. A skipped condition is
    recorded, not silently dropped.
    """
    exp_rt_loss = expected_round_trip_loss(configured_loss_pct)
    res = CalibrationResult(
        ok=True,
        target_down_mbps=target_down_mbps,
        target_up_mbps=target_up_mbps,
        target_rtt_ms=target_rtt_ms,
        configured_loss_pct=configured_loss_pct,
        expected_rt_loss_pct=exp_rt_loss,
    )

    # ── rate, both directions ───────────────────────────────────────────────
    # The server runs in ns2 and the client in ns1, so iperf3 crosses the same
    # shaped bottleneck the app will.
    #
    # TCP is used only on CLEAN conditions. On a loss condition TCP goodput is
    # limited by loss, not by the shaper: measured on this box at 25 Mb/s,
    # 100 ms RTT and 1% loss, a single TCP stream reached 5.45 Mb/s -- 78% below
    # the configured rate -- while the shaper was correct and netem was dropping
    # exactly 1% each way. Gating on that would reject all 36 Stage 1 loss runs
    # for a fault that does not exist. The Mathis bound puts a single flow near
    # MSS/(RTT*sqrt(p)) ~ 1.2 Mb/s there, so this is expected, not anomalous.
    #
    # So loss conditions measure capacity with UDP sent ABOVE the target: the
    # receiver's rate then reveals the shaper's ceiling rather than TCP's
    # ability to fill it, and the expected figure is the rate less the loss
    # netem is configured to drop.
    use_udp_for_rate = configured_loss_pct > 0
    for direction, target, reverse in (
        ("down", target_down_mbps, True),
        ("up", target_up_mbps, False),
    ):
        flag = "-R" if reverse else ""
        expected = (
            target * (1.0 - configured_loss_pct / 100.0) if use_udp_for_rate else target
        )
        try:
            if use_udp_for_rate:
                # 1.08x over the target, for at least 15 s. Both numbers are
                # measured, not guessed: at 1.2x over 5 s the reading came out
                # 9.9% high because HTB's burst allowance dominates a short
                # probe, while 1.05x/15 s read 1.5% high and 1.08x/20 s read
                # 0.7%. A short probe would fail a 5% gate on a correct shaper.
                udp_seconds = max(15, iperf_seconds * 3)
                out = runner(
                    f"{ns_prefix(CLIENT_NS)}iperf3 -c {SERVER_ADDR} -p {IPERF3_PORT} "
                    f"-u -b {target * 1.08:g}M -t {udp_seconds} -J {flag}",
                    check=False,
                    timeout=udp_seconds + 30,
                )
                measured = _parse_iperf3_udp_mbps(out.stdout)
            else:
                out = runner(
                    f"{ns_prefix(CLIENT_NS)}iperf3 -c {SERVER_ADDR} -p {IPERF3_PORT} "
                    f"-t {iperf_seconds} -J {flag}",
                    check=False,
                    timeout=iperf_seconds + 30,
                )
                measured = _parse_iperf3_mbps(out.stdout, reverse)
        except Exception as exc:  # noqa: BLE001 - a failed probe is a skip, not a crash
            measured = None
            res.failures.append(
                f"iperf3 {direction} raised {type(exc).__name__}: {exc}"
            )
        if direction == "down":
            res.measured_down_mbps = measured
        else:
            res.measured_up_mbps = measured
        if measured is None:
            res.ok = False
            res.failures.append(f"iperf3 {direction}: no result")
            continue
        if expected > 0 and abs(measured - expected) / expected > tolerance:
            res.ok = False
            res.failures.append(
                f"{direction} rate {measured:.2f} Mb/s is "
                f"{100 * abs(measured - expected) / expected:.1f}% off "
                f"{expected:g} ({'udp' if use_udp_for_rate else 'tcp'})"
            )

    # ── RTT and loss, from one ping run ─────────────────────────────────────
    # Sized so the loss check can actually fail. Without this a 0.1% condition
    # "passes" on 100 packets no matter what the shaper did.
    needed = (
        loss_check_packets_needed(configured_loss_pct) if configured_loss_pct else 0
    )
    count = ping_count if ping_count is not None else max(100, min(needed, 20000))
    try:
        out = runner(
            f"{ns_prefix(CLIENT_NS)}ping -n -i 0.01 -c {count} -W 2 {SERVER_ADDR}",
            check=False,
            timeout=count * 0.01 + 60,
        )
        rtt, loss, sent = _parse_ping(out.stdout)
    except Exception as exc:  # noqa: BLE001
        rtt, loss, sent = None, None, 0
        res.failures.append(f"ping raised {type(exc).__name__}: {exc}")

    res.measured_rtt_ms = rtt
    res.measured_rt_loss_pct = loss
    res.ping_packets = sent

    if rtt is None:
        res.ok = False
        res.failures.append("ping: no RTT")
    elif target_rtt_ms > 0 and abs(rtt - target_rtt_ms) / target_rtt_ms > tolerance:
        res.ok = False
        res.failures.append(
            f"RTT {rtt:.2f} ms is {100 * abs(rtt - target_rtt_ms) / target_rtt_ms:.1f}% "
            f"off {target_rtt_ms:g}"
        )

    # ── loss verification ───────────────────────────────────────────────────
    # Measured with iperf3 in UDP mode, per direction, because that reads the
    # one-way loss rate `tc netem loss` is actually configured in. The shaper
    # itself drops whatever exceeds the configured rate, so the probe is sent
    # well below the rate under test -- otherwise this would measure the
    # shaper, not netem.
    if configured_loss_pct <= 0:
        res.loss_verified = None
        res.loss_note = "no loss configured"
    else:
        probe_mbps = max(1.0, min(20.0, 0.5 * min(target_down_mbps, target_up_mbps)))
        measured: dict[str, Optional[float]] = {}
        counts: dict[str, int] = {}
        for direction, reverse in (("down", True), ("up", False)):
            flag = "-R" if reverse else ""
            try:
                out = runner(
                    f"{ns_prefix(CLIENT_NS)}iperf3 -c {SERVER_ADDR} -p {IPERF3_PORT} -u "
                    f"-b {probe_mbps}M -t {iperf_seconds * 4} -J {flag}",
                    check=False,
                    timeout=iperf_seconds * 4 + 30,
                )
                lost, pkts = _parse_iperf3_udp_loss(out.stdout)
            except Exception as exc:  # noqa: BLE001
                lost, pkts = None, 0
                res.failures.append(f"iperf3 -u {direction}: {type(exc).__name__}")
            measured[direction] = lost
            counts[direction] = pkts
        res.measured_loss_down_pct = measured.get("down")
        res.measured_loss_up_pct = measured.get("up")
        res.loss_probe = "iperf3_udp"

        have = {d: v for d, v in measured.items() if v is not None}
        if not have:
            res.ok = False
            res.loss_verified = False
            res.loss_note = "iperf3 -u produced no loss figure in either direction"
            res.failures.append("loss: no measurement")
        else:
            bad = []
            for direction, v in have.items():
                n = max(counts.get(direction, 0), 1)
                p = configured_loss_pct
                se = math.sqrt(max(p * (100.0 - p), 0.0) / n)
                band = max(3.0 * se, 0.05)
                if abs(v - p) > band:
                    bad.append(
                        f"{direction} {v:.4f}% vs configured {p:.4f}% "
                        f"(+/-{band:.4f}% at 3 sigma, n={n})"
                    )
            missing = [d for d in ("down", "up") if d not in have]
            res.loss_verified = not bad and not missing
            res.loss_note = "; ".join(
                bad
                + ([f"no figure for {', '.join(missing)}"] if missing else [])
                + (
                    [
                        f"down={res.measured_loss_down_pct}, "
                        f"up={res.measured_loss_up_pct}, configured="
                        f"{configured_loss_pct}% each way"
                    ]
                    if not bad and not missing
                    else []
                )
            )
            if not res.loss_verified:
                res.ok = False
                res.failures.append(f"loss: {res.loss_note}")

    if not res.ok and not res.skip_reason:
        res.skip_reason = "; ".join(res.failures)
    return res
