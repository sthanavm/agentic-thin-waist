"""
Pramana experiment runner — helpers for the CPUC QoE research notebook.

This module drives the Agentic-Thin-Waist / NetGent stack on the lab VM to run
*real, measured* network experiments (solo and multi-app concurrent), verifies
that the results are physically correct (shaping actually happened), and saves a
structured dataset + plots under the repo.

────────────────────────────────────────────────────────────────────────────
Execution paths

  1. DIRECT (default) — drives the substrate worker (:8002) directly:
        POST /shape → POST /congestion → POST /capture(veth2) → POST /run(...)
     for each app, then downloads the pcap over HTTP, computes throughput,
     verifies shaping, and saves telemetry + a local dataset record. No LLM.

  2. INTENT — submits a plain-English sentence to the orchestrator (:8005),
     which parses + runs it. Requires the orchestration service to be running.

────────────────────────────────────────────────────────────────────────────
What the DEPLOYED stack can and cannot do (verified on the lab VM)

  * Traffic shaping + packet capture at the bottleneck (veth2): YES — and every
    DIRECT run VERIFIES the shaping from the pcap (avg throughput within ±15% of
    the cap, else the run is flagged / raises).
  * Player QoE (resolution, rebuffers, dropped frames): NOT AVAILABLE via the
    deployed substrate NetGent. That engine is the "v2" build whose action set is
    {go_to_url, click_element, send_keys, scroll, wait, ...} and has NO
    `start_stats_logging` action. So the DIRECT path measures the NETWORK side
    (throughput, stalls, delivered fraction, shaping fidelity). Records are
    tagged `player_qoe_available=false` so the dataset stays honest.
  * Queue-depth (qtrace) time series: NOT AVAILABLE (deployed `main_local` has
    no /qtrace).
  * Latency/loss are applied link-wide (netem), not per-flow.

Public API (``from pramana_helpers import *``):
    ExperimentConfig, run_experiment, run_direct, run_intent, run_and_display,
    run_sweep, stack_health, show_history, load_dataset, and the plot_* helpers.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Optional

import requests

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None

try:
    from IPython.display import display
except Exception:  # pragma: no cover
    def display(obj: Any) -> None:
        print(obj)


# ── Reuse the repo's pcap analysis ───────────────────────────────────────────
REPO = Path(__file__).resolve().parent
_SCRIPTS = REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

try:
    from analyze_pcap import _read_pcap, compute_throughput, pcap_summary  # type: ignore
except Exception:  # pragma: no cover
    _read_pcap = compute_throughput = pcap_summary = None  # type: ignore


# ═════════════════════════════════════════════════════════════════════════════
#  Configuration (override from the notebook before running)
# ═════════════════════════════════════════════════════════════════════════════
SUBSTRATE = os.environ.get("PRAMANA_SUBSTRATE_URL", "http://localhost:8002")
NETGENT = os.environ.get("PRAMANA_NETGENT_URL", "http://localhost:8003")
TELEMETRY = os.environ.get("PRAMANA_TELEMETRY_URL", "http://localhost:8004")
ORCH = os.environ.get("PRAMANA_ORCH_URL", "http://localhost:8005")

# Substrate topology (deployed local_docker profile).
DOWNSTREAM_IFACE = "veth2"   # shaped bottleneck (download) — capture HERE
UPSTREAM_IFACE = "veth4"     # upload / egress
NETGENT_NAMESPACE = "ns1"    # namespace the workflow runs in

# Local dataset + plots (under the repo).
RESULTS_ROOT = REPO / "results" / "pramana_runs"
DATASET_INDEX = RESULTS_ROOT / "dataset_index.jsonl"

# Correctness thresholds.
SHAPING_TOLERANCE = 0.15   # measured avg throughput must be within +15% of cap
STRICT_SHAPING = True      # raise on shaping-verification failure

# Capture timing: the pcap is downloaded over HTTP, which needs the capture to be
# "finished". We give tshark a duration = run time + this overhead so it stops on
# its own (then we download). Bump it if long browser warmups truncate captures.
CAPTURE_OVERHEAD_S = 45

# HTTP timeouts / polling.
HTTP_TIMEOUT = 60
_POLL_EVERY = 5
_MAX_POLLS = 360


# ═════════════════════════════════════════════════════════════════════════════
#  Application registry
# ═════════════════════════════════════════════════════════════════════════════
# The deployed substrate NetGent (v2) plays a page and generates real traffic via
# `go_to_url` + `wait`. `url` is the page it opens. Streaming apps autoplay where
# possible. Calls (zoom/meet) need a join URL supplied via cfg.app_urls.
APP_REGISTRY: dict[str, dict[str, Any]] = {
    "youtube": {
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&autoplay=1",
        "runtime": "browser", "type": "streaming",
    },
    "vimeo": {
        "url": "https://player.vimeo.com/video/76979871?autoplay=1",
        "runtime": "browser", "type": "streaming",
    },
    "twitch": {
        # For sustained live traffic, override with a live channel URL via app_urls.
        "url": "https://www.twitch.tv/",
        "runtime": "browser", "type": "streaming",
    },
    "tubi": {
        "url": "https://tubitv.com/",
        "runtime": "browser", "type": "streaming",
    },
    "zoom": {
        "url": "",  # supply a meeting join URL via cfg.app_urls["zoom"]
        "runtime": "browser", "type": "call",
    },
    "meet": {
        "url": "",  # supply a meeting join URL via cfg.app_urls["meet"]
        "runtime": "browser", "type": "call",
    },
    "wget": {
        "url": "http://speedtest.tele2.net/100MB.zip",
        "runtime": "shell", "type": "bulk",
    },
}


# ═════════════════════════════════════════════════════════════════════════════
#  Experiment description
# ═════════════════════════════════════════════════════════════════════════════
@dataclass
class ExperimentConfig:
    """Declarative description of one experiment (also the dataset schema)."""

    apps: list[str]
    bandwidth_mbps: float = 6.0
    upload_mbps: Optional[float] = None
    latency_ms: float = 0.0
    loss_pct: float = 0.0
    aqm: str = "pfifo"
    buffer_packets: int = 1000
    qdisc_params: Optional[dict[str, str]] = None
    cca: str = "cubic"
    duration_s: int = 60
    controller: str = "playwright"
    trial: int = 1
    tag: str = ""
    app_urls: Optional[dict[str, str]] = None   # per-app URL overrides

    experiment_id: str = ""
    slug: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.apps, str):
            self.apps = [self.apps]
        self.apps = [a.strip().lower() for a in self.apps if a.strip()]
        for a in self.apps:
            if a not in APP_REGISTRY:
                raise ValueError(f"Unknown app '{a}'. Known: {sorted(APP_REGISTRY)}")
        if self.upload_mbps is None:
            self.upload_mbps = self.bandwidth_mbps

    @property
    def concurrency(self) -> str:
        n = len(self.apps)
        return "solo" if n == 1 else f"concurrent{n}"

    def url_for(self, app: str) -> str:
        if self.app_urls and app in self.app_urls:
            return self.app_urls[app]
        return APP_REGISTRY[app].get("url", "")

    def make_slug(self) -> str:
        apps = "+".join(self.apps)
        slug = (
            f"{apps}_{self.bandwidth_mbps:g}mbps_{self.latency_ms:g}ms_"
            f"{self.loss_pct:g}pct_{self.aqm}_{self.cca}_{self.concurrency}_t{self.trial}"
        )
        return slug.replace("/", "-")


# ═════════════════════════════════════════════════════════════════════════════
#  Small utilities
# ═════════════════════════════════════════════════════════════════════════════
def _is_missing(v: Any) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _num(v: Any) -> str:
    if _is_missing(v):
        return "n/a"
    f = float(v)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def _measure(v: Any, unit: str, nd: int = 2) -> str:
    if _is_missing(v):
        return "n/a"
    return f"{round(float(v), nd):g} {unit}"


def _run_dir(cfg: ExperimentConfig) -> Path:
    suffix = cfg.experiment_id.split("-")[-1] if cfg.experiment_id else uuid.uuid4().hex[:8]
    d = RESULTS_ROOT / f"{cfg.slug}_{suffix}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _build_workflow(app: str, cfg: ExperimentConfig) -> dict[str, Any]:
    """Build a schema-clean workflow for the deployed substrate NetGent (v2).

    v2 states allow only {checks, actions, end_state, executed}; actions only
    {type, params}. v2 actions include go_to_url / wait (browser) — enough to
    open a video page and generate real streaming traffic for `duration_s`.
    """
    meta = APP_REGISTRY[app]

    if meta["runtime"] == "shell":
        # Bulk transfer baseline. (Adjust the action `type` if the deployed
        # shell agent uses a different verb than "shell".)
        return {
            "specification": app,
            "states": [{
                "checks": [],
                "actions": [{"type": "shell", "params": {
                    "command": f"wget -O /dev/null {cfg.url_for(app)}"}}],
                "end_state": "",
            }],
        }

    url = cfg.url_for(app)
    if not url:
        raise ValueError(
            f"App '{app}' has no URL. Supply one via "
            f"ExperimentConfig(app_urls={{'{app}': '<join/watch url>'}})."
        )
    return {
        "specification": app,
        "states": [{
            "checks": [],
            "actions": [
                {"type": "go_to_url", "params": {"url": url}},
                {"type": "wait", "params": {"seconds": cfg.duration_s}},
            ],
            "end_state": "",
        }],
    }


# ═════════════════════════════════════════════════════════════════════════════
#  Substrate-worker HTTP client (:8002)
# ═════════════════════════════════════════════════════════════════════════════
def substrate_shape(cfg: ExperimentConfig, verify: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "upstream_iface": UPSTREAM_IFACE,
        "downstream_iface": DOWNSTREAM_IFACE,
        "download_mbps": cfg.bandwidth_mbps,
        "upload_mbps": cfg.upload_mbps,
        "latency_ms": cfg.latency_ms,
        "qdisc": cfg.aqm,
        "buffer_packets": cfg.buffer_packets,
        "verify": verify,
    }
    if cfg.qdisc_params:
        payload["qdisc_params"] = cfg.qdisc_params
    if cfg.latency_ms > 0:
        payload["latency_location"] = "upstream"
    r = requests.post(f"{SUBSTRATE}/shape", json=payload, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def substrate_congestion(cfg: ExperimentConfig) -> dict[str, Any]:
    r = requests.post(
        f"{SUBSTRATE}/congestion",
        json={"algorithm": cfg.cca, "namespace": NETGENT_NAMESPACE},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def substrate_capture_start(filename: str, duration_s: int) -> dict[str, Any]:
    payload = {
        "interface": DOWNSTREAM_IFACE,   # only the shaped bottleneck
        "capture_filter": "",
        "filename": filename,
        "duration_seconds": duration_s,  # auto-stop so the pcap can be downloaded
    }
    r = requests.post(f"{SUBSTRATE}/capture", json=payload, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def substrate_capture_status(capture_id: str) -> dict[str, Any]:
    r = requests.get(f"{SUBSTRATE}/capture/{capture_id}", timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def substrate_capture_wait(capture_id: str, timeout_s: int) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    body: dict[str, Any] = {}
    while time.time() < deadline:
        body = substrate_capture_status(capture_id)
        if body.get("status") == "finished":
            return body
        time.sleep(2)
    return body


def substrate_capture_download(capture_id: str, dest: Path) -> bool:
    try:
        r = requests.get(f"{SUBSTRATE}/capture/{capture_id}/pcap", timeout=HTTP_TIMEOUT * 4)
        r.raise_for_status()
        dest.write_bytes(r.content)
        return dest.stat().st_size > 24
    except requests.RequestException as exc:
        print(f"  ! pcap download failed: {exc}")
        return False


def substrate_capture_delete(capture_id: str) -> None:
    try:
        requests.delete(f"{SUBSTRATE}/capture/{capture_id}", timeout=HTTP_TIMEOUT)
    except requests.RequestException:
        pass


def substrate_run(app: str, cfg: ExperimentConfig, reshape: bool) -> dict[str, Any]:
    workflow = _build_workflow(app, cfg)
    payload: dict[str, Any] = {
        "upstream_iface": UPSTREAM_IFACE,
        "downstream_iface": DOWNSTREAM_IFACE,
        "qdisc": cfg.aqm,
        "buffer_packets": cfg.buffer_packets,
        "latency_ms": cfg.latency_ms,
        "cca": cfg.cca,
        "cca_namespace": NETGENT_NAMESPACE,
        "workflow": workflow,
        "runtime": APP_REGISTRY[app]["runtime"],
        "experiment_max_seconds": float(cfg.duration_s + 120),
        "verify_shaping": False,
    }
    if reshape:
        payload["download_mbps"] = cfg.bandwidth_mbps
        payload["upload_mbps"] = cfg.upload_mbps
        if cfg.latency_ms > 0:
            payload["latency_location"] = "upstream"
    if cfg.qdisc_params:
        payload["qdisc_params"] = cfg.qdisc_params
    r = requests.post(f"{SUBSTRATE}/run", json=payload, timeout=HTTP_TIMEOUT * 20)
    r.raise_for_status()
    return r.json()


# ═════════════════════════════════════════════════════════════════════════════
#  Telemetry HTTP client (:8004)
# ═════════════════════════════════════════════════════════════════════════════
def telemetry_post_result(experiment_id: str, app: str, cfg: ExperimentConfig,
                          measured_throughput: Optional[float], qoe: dict[str, Any],
                          pcap_path: str, status: str = "success") -> Optional[dict[str, Any]]:
    payload = {
        "experiment_id": experiment_id,
        "trial_number": cfg.trial,
        "status": status,
        "bottleneck_state": {
            "configured_capacity": cfg.bandwidth_mbps,
            "configured_latency": cfg.latency_ms,
            "measured_throughput": measured_throughput,
            "measured_rtt": None,
        },
        "qoe_metrics": qoe,
        "contextual_tree": {
            "c_app": {"application": app},
            "c_trans": {"congestion_control": cfg.cca, "protocol": "tcp"},
        },
        "pcap_path": pcap_path,
    }
    try:
        r = requests.post(f"{TELEMETRY}/results", json=payload, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        print(f"  ! telemetry store failed for {app}: {exc}")
        return None


def telemetry_get_results(**params: Any) -> list[dict[str, Any]]:
    try:
        r = requests.get(f"{TELEMETRY}/results", params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json().get("results", [])
    except requests.RequestException:
        return []


# ═════════════════════════════════════════════════════════════════════════════
#  Capture reader — handles BOTH classic pcap and pcapng (tshark's default)
# ═════════════════════════════════════════════════════════════════════════════
import struct
from collections import namedtuple

_Pkt = namedtuple("_Pkt", ["ts", "orig_len", "data"])


def _read_pcapng(path: str):
    """Yield (ts, orig_len, b"") from a pcapng file (EPB + SPB blocks)."""
    with open(path, "rb") as fh:
        blob = fh.read()
    n = len(blob)
    off = 0
    endian = "<"
    tsresol = 1e-6  # default microseconds
    last_spb_len = 1500
    while off + 12 <= n:
        # peek block type + length using current endianness
        btype = struct.unpack_from(endian + "I", blob, off)[0]
        if btype == 0x0A0D0D0A:  # Section Header Block — read byte-order magic
            bom = struct.unpack_from("<I", blob, off + 8)[0]
            endian = "<" if bom == 0x1A2B3C4D else ">"
        blen = struct.unpack_from(endian + "I", blob, off + 4)[0]
        if blen < 12 or off + blen > n:
            break
        if btype == 0x00000001:  # Interface Description Block — timestamp resolution
            opt = off + 16
            while opt + 4 <= off + blen - 4:
                code, olen = struct.unpack_from(endian + "HH", blob, opt)
                if code == 0:
                    break
                if code == 9 and olen >= 1:  # if_tsresol
                    raw = blob[opt + 4]
                    tsresol = (1.0 / (2 ** (raw & 0x7F))) if (raw & 0x80) else (10.0 ** -(raw & 0x7F))
                opt += 4 + olen + ((4 - olen % 4) % 4)
        elif btype == 0x00000006:  # Enhanced Packet Block
            _iface, tsh, tsl, caplen, origlen = struct.unpack_from(endian + "IIIII", blob, off + 8)
            ts = ((tsh << 32) | tsl) * tsresol
            yield _Pkt(ts, origlen, b"")
        elif btype == 0x00000003:  # Simple Packet Block
            origlen = struct.unpack_from(endian + "I", blob, off + 8)[0]
            yield _Pkt(0.0, origlen, b"")
        off += blen


def _read_capture(path: str):
    """Return a list of packets from either classic pcap or pcapng."""
    p = Path(path)
    if not p.exists() or p.stat().st_size < 24:
        return []
    with open(path, "rb") as fh:
        magic = fh.read(4)
    if magic == b"\x0a\x0d\x0d\x0a":                       # pcapng
        return list(_read_pcapng(path))
    if _read_pcap is not None:                             # classic pcap
        try:
            return list(_read_pcap(path))
        except Exception:
            return []
    return []


def _throughput_series(packets, bin_s: float = 1.0):
    """(times, mbps) per bin — mirrors analyze_pcap.compute_throughput."""
    if not packets:
        return [], []
    t0 = min(p.ts for p in packets)
    from collections import defaultdict
    bins: dict[int, int] = defaultdict(int)
    for p in packets:
        bins[int((p.ts - t0) / bin_s)] += p.orig_len
    if not bins:
        return [], []
    times, rates = [], []
    for i in range(max(bins) + 1):
        times.append(i * bin_s)
        rates.append(bins.get(i, 0) * 8 / bin_s / 1e6)
    return times, rates


# ═════════════════════════════════════════════════════════════════════════════
#  Network metrics + shaping verification (from the pcap)
# ═════════════════════════════════════════════════════════════════════════════
@dataclass
class NetStats:
    packets: int = 0
    duration_s: float = 0.0
    total_mb: float = 0.0
    avg_throughput_mbps: Optional[float] = None
    peak_throughput_mbps: Optional[float] = None
    p95_throughput_mbps: Optional[float] = None
    delivered_fraction: Optional[float] = None   # avg / cap
    stall_seconds: Optional[float] = None        # seconds with ~no traffic
    shaping_verified: bool = False
    shaping_error_pct: Optional[float] = None


def analyze_pcap_netstats(pcap: Path, cap_mbps: float) -> NetStats:
    """Compute throughput stats from the capture and verify the cap was respected."""
    if not pcap.exists():
        return NetStats()
    packets = _read_capture(str(pcap))
    if not packets:
        return NetStats()

    total_bytes = sum(p.orig_len for p in packets)
    ts = [p.ts for p in packets if p.ts]
    duration = (max(ts) - min(ts)) if len(ts) > 1 else 0.0
    ns = NetStats(
        packets=len(packets),
        duration_s=round(duration, 2),
        total_mb=round(total_bytes / 1e6, 2),
        avg_throughput_mbps=round(total_bytes * 8 / max(duration, 0.001) / 1e6, 3) if duration else None,
    )

    # per-second throughput series for peak / p95 / stalls
    _, rates = _throughput_series(packets, 1.0)
    if rates:
        srt = sorted(rates)
        ns.peak_throughput_mbps = round(max(rates), 3)
        ns.p95_throughput_mbps = round(srt[min(len(srt) - 1, int(0.95 * len(srt)))], 3)
        stall_thresh = max(0.05 * cap_mbps, 0.05)
        ns.stall_seconds = float(sum(1 for r in rates if r < stall_thresh))

    if ns.avg_throughput_mbps is not None and cap_mbps > 0:
        ns.delivered_fraction = round(ns.avg_throughput_mbps / cap_mbps, 3)
        err = (ns.avg_throughput_mbps - cap_mbps) / cap_mbps
        ns.shaping_error_pct = round(err * 100, 1)
        # verified = did not blow past the cap (running under the cap is fine)
        ns.shaping_verified = ns.avg_throughput_mbps <= cap_mbps * (1 + SHAPING_TOLERANCE)
    return ns


def classify_traffic(app: str, net: NetStats, cfg: ExperimentConfig) -> str:
    """Network-side traffic-health verdict (player QoE is not available here).

    Important: for streaming, an idle download period is NOT a rebuffer — video
    players burst up to the cap to fill their buffer, then coast. So we classify
    on *what the traffic tells us*, not on idle time:

      fail      nothing meaningful streamed (no packets / < 0.5 MB)
      starved   throughput pinned near the cap for most of the session — the app
                wanted more bandwidth than the link could give (a QoE risk)
      served    got its data in bursts and coasted (healthy)
    """
    if net.packets == 0 or net.avg_throughput_mbps is None or (net.total_mb or 0) < 0.5:
        return "fail"
    cap = cfg.bandwidth_mbps if cfg else 0
    # "pinned near cap": p95 close to cap AND average is a large fraction of cap.
    if cap and net.p95_throughput_mbps and net.avg_throughput_mbps:
        pinned = (net.p95_throughput_mbps >= 0.9 * cap and
                  net.avg_throughput_mbps >= 0.8 * cap)
        if pinned:
            return "starved"
    return "served"


# Back-compat aliases (older notebooks referenced these names)
def extract_app_qoe(app: str, cfg: ExperimentConfig) -> dict[str, Any]:
    return {}   # deployed substrate v2 engine exposes no player QoE


def classify_qoe(app: str, qoe: dict[str, Any], net: NetStats) -> str:
    return classify_traffic(app, net, None)  # type: ignore[arg-type]


# ═════════════════════════════════════════════════════════════════════════════
#  Plots
# ═════════════════════════════════════════════════════════════════════════════
def _plt():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt
    except Exception:
        return None


def plot_run_throughput(pcap: Path, cap_mbps: float, out: Path, title: str) -> Optional[Path]:
    plt = _plt()
    if plt is None or not pcap.exists():
        return None
    packets = _read_capture(str(pcap))
    times, rates = _throughput_series(packets, 1.0)
    if not times:
        return None
    avg = sum(rates) / len(rates)
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(times, rates, linewidth=1.0, color="#2196F3")
    ax.fill_between(times, rates, alpha=0.15, color="#2196F3")
    ax.axhline(cap_mbps, color="green", linewidth=1.2, alpha=0.8,
               label=f"cap {cap_mbps:g} Mbps")
    ax.axhline(cap_mbps * (1 + SHAPING_TOLERANCE), color="orange", linestyle=":",
               linewidth=1.0, alpha=0.7, label=f"+{int(SHAPING_TOLERANCE*100)}% tol")
    ax.axhline(avg, color="red", linestyle="--", linewidth=0.9, alpha=0.8,
               label=f"avg {avg:.2f} Mbps")
    ax.set_xlabel("Time (s)"); ax.set_ylabel("Throughput (Mbps)")
    ax.set_title(title); ax.set_xlim(left=0); ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.3); ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout(); fig.savefig(out, dpi=150); plt.close(fig)
    return out


def plot_app_qoe_timeseries(app: str, cfg: ExperimentConfig, out: Path) -> Optional[Path]:
    return None   # no player-QoE time series on the deployed engine


# ── metric lookup for sweep plots (network_stats first) ──────────────────────
_NET_METRICS = {"avg_throughput_mbps", "peak_throughput_mbps", "p95_throughput_mbps",
                "delivered_fraction", "stall_seconds", "total_mb"}


def _metric_of(rec: dict[str, Any], metric: str) -> Optional[float]:
    if metric in _NET_METRICS:
        return (rec.get("network_stats") or {}).get(metric)
    # else look in per-app player qoe (empty on this deployment)
    for pa in (rec.get("per_app") or {}).values():
        v = (pa.get("qoe") or {}).get(metric)
        if v is not None:
            return v
    return None


def _sweep_line(records, xkey_fn, metric, xlabel, out: Path):
    plt = _plt()
    if plt is None or not records:
        return None
    xs, ys = [], []
    for r in records:
        v = _metric_of(r, metric)
        if v is not None:
            xs.append(xkey_fn(r)); ys.append(v)
    if not xs:
        print(f"  (no data for metric '{metric}')")
        return None
    xs, ys = zip(*sorted(zip(xs, ys)))
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(xs, ys, marker="o", color="#2196F3")
    ax.set_xlabel(xlabel); ax.set_ylabel(metric.replace("_", " "))
    ax.set_title(f"{metric.replace('_',' ')} vs {xlabel}")
    ax.grid(True, alpha=0.3)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(); fig.savefig(out, dpi=150); plt.close(fig)
    print(f"Saved: {out}")
    return out


def plot_qoe_vs_bandwidth(records, out: Path, metric: str = "avg_throughput_mbps"):
    return _sweep_line(records, lambda r: r["config"]["bandwidth_mbps"], metric,
                       "bandwidth (Mbps)", out)


def plot_qoe_vs_latency(records, out: Path, metric: str = "stall_seconds"):
    return _sweep_line(records, lambda r: r["config"]["latency_ms"], metric,
                       "latency (ms)", out)


def plot_qoe_vs_concurrency(records, out: Path, metric: str = "avg_throughput_mbps"):
    return _sweep_line(records, lambda r: len(r["config"]["apps"]), metric,
                       "concurrency (#apps)", out)


def plot_throughput_vs_bandwidth(records, out: Path):
    return _sweep_line(records, lambda r: r["config"]["bandwidth_mbps"],
                       "avg_throughput_mbps", "configured bandwidth (Mbps)", out)


def plot_group_comparison(groups: dict[str, list[dict[str, Any]]], out: Path,
                          metric: str = "avg_throughput_mbps"):
    """Bar chart comparing a metric across labelled groups (AQM, equity, ...)."""
    plt = _plt()
    if plt is None or not groups:
        return None
    labels, vals = [], []
    for label, recs in groups.items():
        ms = [_metric_of(r, metric) for r in recs if _metric_of(r, metric) is not None]
        if ms:
            labels.append(label); vals.append(sum(ms) / len(ms))
    if not labels:
        print(f"  (no data for metric '{metric}')")
        return None
    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(labels, vals, color=plt.cm.tab10(range(len(labels))))
    for b, v in zip(bars, vals):
        ax.annotate(f"{v:.2f}", xy=(b.get_x() + b.get_width() / 2, v),
                    xytext=(0, 3), textcoords="offset points", ha="center", fontsize=9)
    ax.set_ylabel(metric.replace("_", " "))
    ax.set_title(f"{metric.replace('_',' ')} comparison")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(); fig.savefig(out, dpi=150); plt.close(fig)
    print(f"Saved: {out}")
    return out


def plot_aqm_comparison(records_by_aqm, out: Path, metric: str = "avg_throughput_mbps"):
    return plot_group_comparison(records_by_aqm, out, metric)


def plot_equity_comparison(well_served, redlined, out: Path,
                           metric: str = "avg_throughput_mbps"):
    return plot_group_comparison(
        {"well-served": [well_served], "redlined": [redlined]}, out, metric)


# ═════════════════════════════════════════════════════════════════════════════
#  Dataset record
# ═════════════════════════════════════════════════════════════════════════════
def _write_record(record: dict[str, Any], run_dir: Path) -> Path:
    (run_dir / "record.json").write_text(json.dumps(record, indent=2, default=str))
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    with open(DATASET_INDEX, "a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
    return run_dir / "record.json"


# ═════════════════════════════════════════════════════════════════════════════
#  DIRECT execution path
# ═════════════════════════════════════════════════════════════════════════════
def run_direct(cfg: ExperimentConfig) -> dict[str, Any]:
    """Run one experiment (solo or concurrent) on the substrate worker."""
    cfg.experiment_id = f"exp-{uuid.uuid4().hex[:10]}"
    cfg.slug = cfg.make_slug()
    run_dir = _run_dir(cfg)
    apps = cfg.apps
    concurrent = len(apps) > 1

    print(f"▶ DIRECT run: {cfg.slug}")
    print(f"  cap={cfg.bandwidth_mbps}Mbps lat={cfg.latency_ms}ms loss={cfg.loss_pct}% "
          f"aqm={cfg.aqm} cca={cfg.cca} dur={cfg.duration_s}s apps={apps}")

    print("  • shaping ...")
    shape = substrate_shape(cfg, verify=not concurrent)
    substrate_congestion(cfg)

    cap_duration = cfg.duration_s + CAPTURE_OVERHEAD_S + 15 * (len(apps) - 1)
    print(f"  • capture on {DOWNSTREAM_IFACE} (auto-stop {cap_duration}s) ...")
    cap = substrate_capture_start(cfg.slug, duration_s=cap_duration)
    capture_id = cap["capture_id"]

    run_results: dict[str, Any] = {}
    print(f"  • running {len(apps)} workflow(s) "
          f"({'concurrent' if concurrent else 'solo'}) ...")
    if concurrent:
        with ThreadPoolExecutor(max_workers=len(apps)) as ex:
            futs = {ex.submit(substrate_run, a, cfg, False): a for a in apps}
            for fut in as_completed(futs):
                a = futs[fut]
                try:
                    res = fut.result()
                    run_results[a] = res
                    ok = (res.get("result") or [{}])[0].get("success")
                    print(f"    {'✓' if ok else '✗'} {a} (success={ok})")
                except Exception as exc:
                    run_results[a] = {"status": "failed", "error": str(exc)}
                    print(f"    ✗ {a} failed: {exc}")
    else:
        a = apps[0]
        try:
            res = substrate_run(a, cfg, reshape=False)
            run_results[a] = res
            ok = (res.get("result") or [{}])[0].get("success")
            print(f"    {'✓' if ok else '✗'} {a} (success={ok})")
            if not ok:
                err = (res.get("result") or [{}])[0].get("error")
                print(f"      workflow error: {str(err)[:200]}")
        except Exception as exc:
            run_results[a] = {"status": "failed", "error": str(exc)}
            print(f"    ✗ {a} failed: {exc}")

    print("  • finalising capture + downloading pcap ...")
    substrate_capture_wait(capture_id, timeout_s=cap_duration + 60)
    local_pcap = run_dir / "capture.pcap"
    got = substrate_capture_download(capture_id, local_pcap)
    substrate_capture_delete(capture_id)

    net = analyze_pcap_netstats(local_pcap, cfg.bandwidth_mbps) if got else NetStats()
    if net.avg_throughput_mbps is not None:
        verdict = "OK" if net.shaping_verified else "OVER CAP"
        print(f"  • shaping check: avg {net.avg_throughput_mbps:.2f} / cap "
              f"{cfg.bandwidth_mbps:g} Mbps ({net.shaping_error_pct:+.1f}%) → {verdict}"
              f"  | delivered {net.delivered_fraction}, stalls {net.stall_seconds}s")
    else:
        print("  • shaping check: no packets captured")

    if STRICT_SHAPING and net.avg_throughput_mbps is not None and not net.shaping_verified:
        raise RuntimeError(
            f"Shaping verification FAILED for {cfg.slug}: measured "
            f"{net.avg_throughput_mbps:.2f} Mbps > {cfg.bandwidth_mbps:g} Mbps "
            f"+{int(SHAPING_TOLERANCE*100)}%. Not trusting this run."
        )

    per_app: dict[str, Any] = {}
    for a in apps:
        verdict = classify_traffic(a, net, cfg)
        per_app[a] = {"type": APP_REGISTRY[a]["type"], "traffic_verdict": verdict,
                      "player_qoe_available": False, "qoe": {}}
        print(f"    · {a}: traffic={verdict}")

    plots: dict[str, str] = {}
    tp = plot_run_throughput(
        local_pcap, cfg.bandwidth_mbps, run_dir / "throughput.png",
        title=f"{cfg.slug} — throughput "
              f"(shaping {'verified' if net.shaping_verified else 'UNVERIFIED'})")
    if tp:
        plots["throughput"] = str(tp)

    overall_ok = net.shaping_verified or net.avg_throughput_mbps is None
    for a in apps:
        telemetry_post_result(cfg.experiment_id, a, cfg,
                              measured_throughput=net.avg_throughput_mbps,
                              qoe={}, pcap_path=str(local_pcap),
                              status="success" if overall_ok else "shaping_failed")

    record = {
        "experiment_id": cfg.experiment_id,
        "slug": cfg.slug,
        "mode": "direct",
        "timestamp": time.time(),
        "config": {**{k: v for k, v in asdict(cfg).items()
                      if k not in ("experiment_id", "slug")},
                   "concurrency": cfg.concurrency},
        "limitations": {
            "player_qoe_available": False,
            "player_qoe_reason": "deployed substrate NetGent (v2) has no start_stats_logging action",
            "latency_scope": "link-wide (netem, not per-flow)",
            "qtrace_available": False,
            "per_app_throughput": False,  # single shared capture on veth2
        },
        "network_stats": asdict(net),
        "shaping_applied": shape.get("bottleneck_state", shape),
        "per_app": per_app,
        "workflow_ok": all((r.get("result") or [{}])[0].get("success")
                           for r in run_results.values() if isinstance(r, dict)),
        "artifacts": {"pcap": str(local_pcap) if got else "",
                      "plots": plots, "dir": str(run_dir)},
    }
    _write_record(record, run_dir)
    print(f"  ✓ saved → {run_dir}")
    return record


# ═════════════════════════════════════════════════════════════════════════════
#  INTENT execution path (orchestrator :8005)
# ═════════════════════════════════════════════════════════════════════════════
_TERMINAL = {"complete", "completed", "failed"}


def _submit_intent(intent: str, context: Optional[dict] = None) -> str:
    body: dict[str, Any] = {"intent": intent}
    if context:
        body["context"] = context
    resp = requests.post(f"{ORCH}/intent", json=body, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["orchestration_id"]


def _poll(orch_id: str) -> dict[str, Any]:
    last = None
    body: dict[str, Any] = {}
    started = time.time()
    for _ in range(_MAX_POLLS):
        body = requests.get(f"{ORCH}/orchestration/{orch_id}", timeout=HTTP_TIMEOUT).json()
        status = body.get("status")
        if status != last:
            print(f"  {status} ... ({int(time.time() - started)}s)")
            last = status
        if status in _TERMINAL:
            break
        time.sleep(_POLL_EVERY)
    return body


def _orch_experiment_ids(orch_id: str) -> list[str]:
    body = requests.get(f"{ORCH}/orchestration/{orch_id}/results", timeout=HTTP_TIMEOUT).json()
    return [r.get("experiment_id") for r in body.get("results", []) if r.get("experiment_id")]


def _telemetry_rows(experiment_id: str, retries: int = 4) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for attempt in range(retries):
        # GET :8004/results?experiment_id=<id>&limit=1 (most recent first)
        rows = telemetry_get_results(experiment_id=experiment_id, limit=1)
        if rows and not _is_missing(rows[0].get("measured_throughput")):
            break
        if attempt < retries - 1:
            time.sleep(3)
    return rows


def _throughput_from_pcap_path(pcap_path: Optional[str],
                               cap_mbps: Optional[float]) -> Optional[float]:
    """Fallback: compute avg throughput (Mbps) directly from a captured pcap.

    ``pcap_path`` is whatever telemetry recorded. It is read with the
    format-detecting loader (classic pcap *or* pcapng). Only works if the file
    is reachable from where the notebook runs (i.e. on the VM, or a local copy).
    """
    if not pcap_path:
        return None
    candidates = [Path(pcap_path)]
    # also try the basename under the local results dir, in case the notebook
    # runs off-host but a copy was pulled locally
    candidates.append(RESULTS_ROOT / Path(pcap_path).name)
    for p in candidates:
        try:
            if p.exists() and p.stat().st_size > 24:
                ns = analyze_pcap_netstats(p, cap_mbps or 0)
                if ns.avg_throughput_mbps is not None:
                    return ns.avg_throughput_mbps
        except Exception:
            continue
    return None


def _app_name(experiment_id: str | None, fallback: str = "unknown") -> str:
    if not experiment_id:
        return fallback
    return experiment_id.split("_", 1)[0]


def run_and_display(intent: str, context: Optional[dict] = None) -> None:
    """Submit an INTENT sentence to the orchestrator, wait, and summarise."""
    print("Submitting intent to Pramana orchestrator ...")
    try:
        orch_id = _submit_intent(intent, context=context)
    except requests.RequestException as exc:
        print(f"✗ Could not reach the orchestration service at {ORCH}")
        print(f"  {exc}")
        print("  Use the DIRECT path (EXECUTION_MODE='direct') instead.")
        return
    print(f"  orchestration_id: {orch_id}")
    status_body = _poll(orch_id)
    status = status_body.get("status")
    if status not in {"complete", "completed"}:
        print(f"\n✗ Experiment failed: {status_body.get('error') or 'unknown'}")
        return
    experiment_ids = _orch_experiment_ids(orch_id)
    if not experiment_ids:
        print("\n✓ Orchestration complete, but no experiment results were recorded.")
        return
    multi = len(experiment_ids) > 1
    print(f"\n✓ {len(experiment_ids)} experiment(s) complete\n" if multi else "")
    for idx, exp_id in enumerate(experiment_ids, start=1):
        rows = _telemetry_rows(exp_id)
        row = rows[0] if rows else {}

        # Throughput/RTT from the telemetry row; if throughput is null there,
        # fall back to computing it straight from the captured pcap.
        throughput = row.get("measured_throughput")
        rtt = row.get("measured_rtt")
        tp_note = ""
        if _is_missing(throughput):
            computed = _throughput_from_pcap_path(
                row.get("pcap_path"), row.get("configured_capacity"))
            if computed is not None:
                throughput = computed
                tp_note = "  (computed from pcap)"
            elif row.get("pcap_path"):
                tp_note = "  (telemetry null; pcap not reachable from here)"

        print(f"✓ Experiment {idx}" if multi else "✓ Experiment complete")
        print(f"  App:        {_app_name(exp_id)}")
        print(f"  Capacity:   {_measure(row.get('configured_capacity'), 'Mbps')}")
        print(f"  Latency:    {_measure(row.get('configured_latency'), 'ms', 0)}")
        print(f"  Throughput: {_measure(throughput, 'Mbps')}{tp_note}")
        print(f"  RTT:        {_measure(rtt, 'ms', 0)}")


def _intent_context_from_cfg(cfg: ExperimentConfig) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "capacities": [cfg.bandwidth_mbps],
        "latencies": [cfg.latency_ms],
        "cc_algorithms": [cfg.cca],
        "aqm_policy": cfg.aqm,
        "buffer_packets": cfg.buffer_packets,
        "duration_seconds": cfg.duration_s,
        "num_trials": 1,
    }
    if cfg.qdisc_params:
        ctx["qdisc_params"] = cfg.qdisc_params
    return ctx


def _intent_text_from_cfg(cfg: ExperimentConfig) -> str:
    apps = " and ".join(cfg.apps)
    verb = "run" if len(cfg.apps) == 1 else "run concurrently"
    loss = f", {cfg.loss_pct:g}% loss" if cfg.loss_pct else ""
    return (f"{verb} {apps} on a {cfg.bandwidth_mbps:g} Mbps bottleneck with "
            f"{cfg.latency_ms:g} ms latency{loss} for {cfg.duration_s} seconds "
            f"using {cfg.cca} congestion control and a {cfg.aqm} queue.")


def run_intent(cfg: ExperimentConfig) -> dict[str, Any]:
    intent = _intent_text_from_cfg(cfg)
    print(f"▶ INTENT run: {intent}")
    run_and_display(intent, context=_intent_context_from_cfg(cfg))
    return {"mode": "intent", "intent": intent}


# ═════════════════════════════════════════════════════════════════════════════
#  Dispatcher + sweeps
# ═════════════════════════════════════════════════════════════════════════════
def run_experiment(cfg: ExperimentConfig, mode: str = "direct") -> dict[str, Any]:
    mode = mode.lower()
    if mode == "direct":
        return run_direct(cfg)
    if mode == "intent":
        return run_intent(cfg)
    raise ValueError("mode must be 'direct' or 'intent'")


def run_sweep(base: ExperimentConfig, axis: str, values: Iterable[Any],
              mode: str = "direct") -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    values = list(values)
    print(f"═══ SWEEP over {axis}: {values} ═══")
    base_fields = {k: v for k, v in asdict(base).items()
                   if k not in ("experiment_id", "slug")}
    for i, val in enumerate(values, 1):
        cfg = ExperimentConfig(**{**base_fields, axis: val})
        cfg.tag = base.tag or f"sweep_{axis}"
        print(f"\n[{i}/{len(values)}] {axis}={val}")
        try:
            rec = run_experiment(cfg, mode=mode)
            if rec:
                rec.setdefault("sweep", {})["axis"] = axis
                rec["sweep"]["value"] = val
                records.append(rec)
        except Exception as exc:
            print(f"  ✗ {axis}={val} failed: {exc}")
    return records


# ═════════════════════════════════════════════════════════════════════════════
#  Health + history + dataset
# ═════════════════════════════════════════════════════════════════════════════
def stack_health() -> dict[str, str]:
    checks = {
        "substrate (:8002)": f"{SUBSTRATE}/health",
        "netgent (:8003)": f"{NETGENT}/health",
        "telemetry (:8004)": f"{TELEMETRY}/health",
        "orchestrator (:8005)": f"{ORCH}/health",
    }
    out: dict[str, str] = {}
    for name, url in checks.items():
        try:
            r = requests.get(url, timeout=4)
            out[name] = "up" if r.status_code == 200 else f"http {r.status_code}"
        except requests.RequestException:
            out[name] = "DOWN"
    for name, status in out.items():
        print(f"  {name:<22} {status}")
    if "DOWN" in out.get("orchestrator (:8005)", ""):
        print("  note: orchestrator is optional — DIRECT mode does not need it.")
    return out


def show_history(limit: int = 15, application: str | None = None) -> Any:
    params: dict[str, Any] = {"limit": limit, "sort_by": "created_at", "sort_order": "desc"}
    if application:
        params["application"] = application
    rows = telemetry_get_results(**params)
    if not rows:
        print("No experiments recorded in telemetry yet.")
        return None
    friendly = [{
        "when": (r.get("created_at") or "")[:19].replace("T", " "),
        "app": _app_name(r.get("experiment_id"), r.get("application") or "?"),
        "status": r.get("status"),
        "capacity (Mbps)": _num(r.get("configured_capacity")),
        "latency (ms)": _num(r.get("configured_latency")),
        "throughput (Mbps)": _num(r.get("measured_throughput")),
    } for r in rows]
    print(f"{len(friendly)} most recent experiment(s) in telemetry:")
    if pd is not None:
        df = pd.DataFrame(friendly)
        display(df)
        return df
    for row in friendly:
        print("  ", row)
    return friendly


def load_dataset() -> Any:
    if not DATASET_INDEX.exists():
        print(f"No dataset yet at {DATASET_INDEX}")
        return None
    rows = [json.loads(l) for l in DATASET_INDEX.read_text().splitlines() if l.strip()]
    if pd is None:
        return rows
    flat = []
    for r in rows:
        cfg = r.get("config", {})
        ns = r.get("network_stats", {})
        flat.append({
            "slug": r.get("slug"),
            "apps": "+".join(cfg.get("apps", [])),
            "concurrency": cfg.get("concurrency"),
            "bandwidth_mbps": cfg.get("bandwidth_mbps"),
            "latency_ms": cfg.get("latency_ms"),
            "loss_pct": cfg.get("loss_pct"),
            "aqm": cfg.get("aqm"), "cca": cfg.get("cca"),
            "avg_throughput_mbps": ns.get("avg_throughput_mbps"),
            "peak_throughput_mbps": ns.get("peak_throughput_mbps"),
            "delivered_fraction": ns.get("delivered_fraction"),
            "stall_seconds": ns.get("stall_seconds"),
            "shaping_verified": ns.get("shaping_verified"),
            "workflow_ok": r.get("workflow_ok"),
            "player_qoe": r.get("limitations", {}).get("player_qoe_available"),
        })
    return pd.DataFrame(flat)


__all__ = [
    "SUBSTRATE", "NETGENT", "TELEMETRY", "ORCH", "RESULTS_ROOT",
    "SHAPING_TOLERANCE", "STRICT_SHAPING", "CAPTURE_OVERHEAD_S",
    "APP_REGISTRY",
    "ExperimentConfig", "run_experiment", "run_direct", "run_intent",
    "run_and_display", "run_sweep",
    "analyze_pcap_netstats", "classify_traffic", "extract_app_qoe", "classify_qoe",
    "plot_run_throughput", "plot_app_qoe_timeseries",
    "plot_qoe_vs_bandwidth", "plot_qoe_vs_latency", "plot_qoe_vs_concurrency",
    "plot_throughput_vs_bandwidth", "plot_aqm_comparison", "plot_equity_comparison",
    "plot_group_comparison",
    "stack_health", "show_history", "load_dataset",
]
