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

────────────────────────────────────────────────────────────────────────────
Reporting conventions (research guidelines — enforced in code)

  * NEVER a combined throughput plot. Every run emits one throughput plot PER
    APP, showing only that app's traffic against the cap, with avg + peak drawn.
  * Video apps (youtube/twitch/vimeo/tubi) plot the DOWNLOAD direction only —
    upload is just ACKs, and summing the two reads a 10 Mbps stream as 11-12.
    Conferencing apps (zoom/meet) get a separate plot per direction.
  * Multi-app runs are split from the single shared capture: by each app's
    namespace alias IP when the worker provides them, otherwise by the remote
    endpoint, identified from the DNS answers and TLS SNI inside the very same
    capture. Per-app numbers land in `record.json` under `per_app_stats`.
  * Every run saves its PCAP — the raw evidence — next to `record.json` and the
    plots, including runs that fail shaping verification.
  * Plots are generated automatically when a run finishes; no separate call.
  * Alongside throughput, each app gets a `qoe_summary_<app>.png` of the QoE
    proxies the network side can honestly support: stall time, delivered
    fraction, peak-vs-average, and a served/starved verdict.

Public API (``from pramana_helpers import *``):
    ExperimentConfig, run_experiment, run_direct, run_intent, run_and_display,
    run_sweep, stack_health, show_history, load_dataset, and the plot_* helpers.
"""

from __future__ import annotations

import ipaddress
import json
import math
import os
import textwrap
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


# ── Repo layout ──────────────────────────────────────────────────────────────
# This module lives at <repo>/experiments/pramana/. HERE is its own directory
# (where run artifacts are written); REPO_ROOT is the agentic-thin-waist checkout
# root, which is what makes `shared` importable below.
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
REPO = HERE  # back-compat alias: anything anchored "next to this file"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The QoE definitions are a normal same-repo package — no path hunting, no env
# var, nothing to configure for a fresh clone.
try:
    from shared import apps as apps_registry  # noqa: E402
    from shared import qoe as qoe_lib  # noqa: E402
except ImportError as exc:  # pragma: no cover - surfaced loudly, never guessed at
    raise ImportError(
        "pramana_helpers could not import `shared.apps` / `shared.qoe` from the "
        f"agentic-thin-waist checkout at {REPO_ROOT}.\n"
        "This module must live at <repo>/experiments/pramana/pramana_helpers.py "
        "with the repo's `shared/` package alongside it. If you copied the file "
        "elsewhere, move it back or run from the repo root."
    ) from exc


# ═════════════════════════════════════════════════════════════════════════════
#  Configuration (override from the notebook before running)
# ═════════════════════════════════════════════════════════════════════════════
SUBSTRATE = os.environ.get("PRAMANA_SUBSTRATE_URL", "http://localhost:8002")
NETGENT = os.environ.get("PRAMANA_NETGENT_URL", "http://localhost:8003")
TELEMETRY = os.environ.get("PRAMANA_TELEMETRY_URL", "http://localhost:8004")
ORCH = os.environ.get("PRAMANA_ORCH_URL", "http://localhost:8005")

# Substrate topology (deployed local_docker profile).
DOWNSTREAM_IFACE = "veth2"  # shaped bottleneck (download) — capture HERE
UPSTREAM_IFACE = "veth4"  # upload / egress
NETGENT_NAMESPACE = "ns1"  # namespace the workflow runs in

# Local dataset + plots. Defaults to `results/` beside this file (gitignored);
# override with PRAMANA_RESULTS_DIR to write somewhere else (e.g. a big disk).
RESULTS_ROOT = (
    Path(os.environ.get("PRAMANA_RESULTS_DIR", str(HERE / "results"))) / "pramana_runs"
)
DATASET_INDEX = RESULTS_ROOT / "dataset_index.jsonl"

# Correctness thresholds.
SHAPING_TOLERANCE = 0.15  # measured avg throughput must be within +15% of cap
STRICT_SHAPING = True  # raise on shaping-verification failure

# Capture timing: the pcap is downloaded over HTTP, which needs the capture to be
# "finished". We give tshark a duration = run time + this overhead so it stops on
# its own (then we download). Bump it if long browser warmups truncate captures.
CAPTURE_OVERHEAD_S = 45

# HTTP timeouts / polling.
HTTP_TIMEOUT = 60
_POLL_EVERY = 5
_MAX_POLLS = 360


# ═════════════════════════════════════════════════════════════════════════════
#  QoE definitions (same-repo package)
# ═════════════════════════════════════════════════════════════════════════════
# `shared.apps` (what each app is and how it is driven) and `shared.qoe` (what a
# QoE metric means, per shared/models/README.md) are imported directly at the top
# of this file. `_bind_shared()` is kept only so existing call sites keep working;
# it no longer searches anywhere.
def _bind_shared() -> tuple[Any, Any]:
    """The QoE registry + summarizer. Same-repo import, always available."""
    return apps_registry, qoe_lib


def shared_repo_status() -> str:
    return (
        f"shared.apps + shared.qoe loaded from {REPO_ROOT} "
        f"({len(apps_registry.known_apps())} apps)"
    )


# ── Real-QoE collector (PR #167 browser driver, registry-driven) ────────────
COLLECTOR_IMAGE = os.environ.get(
    "PRAMANA_COLLECTOR_IMAGE", "video-qoe-collector:latest"
)
SUBSTRATE_CONTAINER = os.environ.get("PRAMANA_SUBSTRATE_CONTAINER", "substrate-worker")
DOCKER_BIN = os.environ.get("PRAMANA_DOCKER", "docker")
# The collector must run INSIDE ns1 or its traffic is not shaped. Display numbers
# are allocated per app; each gets its own Xvfb.
FIRST_DISPLAY_NUM = 99
COLLECT_ENABLED = os.environ.get("PRAMANA_COLLECT", "1") not in ("0", "false", "no")


# ═════════════════════════════════════════════════════════════════════════════
#  Application registry
# ═════════════════════════════════════════════════════════════════════════════
# The deployed substrate NetGent (v2) plays a page and generates real traffic via
# `go_to_url` + `wait`. `url` is the page it opens. Streaming apps autoplay where
# possible. Calls (zoom/meet) need a join URL supplied via cfg.app_urls.
APP_REGISTRY: dict[str, dict[str, Any]] = {
    "youtube": {
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&autoplay=1",
        "runtime": "browser",
        "type": "streaming",
        # Hostname suffixes that identify THIS app's traffic in the capture.
        # `googlevideo.com` / `sn-*.gvt1.com` are the media CDN; the rest are
        # page + thumbnail assets. Chrome's own Google traffic (component
        # updates, optimization-guide models) is NOT here — see BROWSER_INFRA.
        "domains": [
            "googlevideo.com",
            "youtube.com",
            "youtu.be",
            "ytimg.com",
            "ggpht.com",
            "youtube-nocookie.com",
            "yt3.ggpht.com",
        ],
        # Video: ACKs are negligible and combining them inflates the apparent
        # rate, so only the download direction is plotted.
        "plot_directions": ["download"],
        "color": "#FF0000",
    },
    "vimeo": {
        "url": "https://player.vimeo.com/video/76979871?autoplay=1",
        "runtime": "browser",
        "type": "streaming",
        "domains": ["vimeocdn.com", "vimeo.com", "akamaized.net/vimeo"],
        "plot_directions": ["download"],
        "color": "#1AB7EA",
    },
    "twitch": {
        # For sustained live traffic, override with a live channel URL via app_urls.
        "url": "https://www.twitch.tv/",
        "runtime": "browser",
        "type": "streaming",
        "domains": [
            "twitch.tv",
            "ttvnw.net",
            "jtvnw.net",
            "twitchcdn.net",
            "twitchsvc.net",
            "live-video.net",
        ],
        "plot_directions": ["download"],
        "color": "#9146FF",
    },
    "tubi": {
        "url": "https://tubitv.com/",
        "runtime": "browser",
        "type": "streaming",
        "domains": ["tubitv.com", "adrise.tv", "tubi.video", "tubi.io"],
        "plot_directions": ["download"],
        "color": "#FBC02D",
    },
    "zoom": {
        "url": "",  # supply a meeting join URL via cfg.app_urls["zoom"]
        "runtime": "browser",
        "type": "call",
        "domains": ["zoom.us", "zoomgov.com", "zoom.com", "zoom.zdassets.com"],
        # Conferencing is bidirectional — upload matters as much as download,
        # so each direction gets its own plot (never summed).
        "plot_directions": ["download", "upload"],
        "color": "#2D8CFF",
    },
    "meet": {
        "url": "",  # supply a meeting join URL via cfg.app_urls["meet"]
        "runtime": "browser",
        "type": "call",
        "domains": [
            "meet.google.com",
            "googleusercontent.com/meet",
            "stun.l.google.com",
            "meetings.googleapis.com",
        ],
        "plot_directions": ["download", "upload"],
        "color": "#00897B",
    },
    "wget": {
        "url": "http://speedtest.tele2.net/100MB.zip",
        "runtime": "shell",
        "type": "bulk",
        "domains": ["tele2.net", "speedtest.tele2.net"],
        "plot_directions": ["download"],
        "color": "#607D8B",
    },
}


# Hostnames that belong to the *browser itself*, not to any app under test.
# Chrome pulls tens of MB of ML models and component updates from these while a
# video plays; counting them as app traffic is the single biggest source of
# inflated "throughput" in a shared capture.
BROWSER_INFRA_DOMAINS: tuple[str, ...] = (
    "optimizationguide-pa.googleapis.com",
    "edgedl.me.gvt1.com",
    "redirector.gvt1.com",
    "update.googleapis.com",
    "clients1.google.com",
    "clients2.google.com",
    "clients3.google.com",
    "clients4.google.com",
    "clients5.google.com",
    "clients6.google.com",
    "clientservices.googleapis.com",
    "safebrowsing.googleapis.com",
    "content-autofill.googleapis.com",
    "accounts.google.com",
    "android.clients.google.com",
    "android.l.google.com",
    "www.gstatic.com",
    "fonts.gstatic.com",
    "ssl.gstatic.com",
    "connectivitycheck.gstatic.com",
    "dns.google",
)


def app_domains(app: str) -> list[str]:
    """Hostname suffixes that identify `app`'s traffic in a capture."""
    return list(APP_REGISTRY.get(app, {}).get("domains") or [])


def app_plot_directions(app: str) -> list[str]:
    """Directions to plot for `app`.

    Video apps → ``["download"]`` only (upload is ACKs; summing the two inflates
    a 10 Mbps stream to 11-12 Mbps). Conferencing apps → both, plotted apart.
    """
    dirs = APP_REGISTRY.get(app, {}).get("plot_directions")
    if dirs:
        return list(dirs)
    return (
        ["download", "upload"]
        if APP_REGISTRY.get(app, {}).get("type") == "call"
        else ["download"]
    )


def app_color(app: str) -> str:
    return APP_REGISTRY.get(app, {}).get("color", "#2196F3")


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
    app_urls: Optional[dict[str, str]] = None  # per-app URL overrides
    # Conferencing apps only produce real QoE when a remote peer is publishing
    # video. Map app -> peer identifier/room; apps needing a peer without one
    # are SKIPPED rather than measured against their own local preview.
    peers: Optional[dict[str, str]] = None
    # Opt-in: pin YouTube's rendition (setPlaybackQualityRange) instead of
    # leaving ABR on auto. Default False — every existing run is unaffected.
    # Forced runs get a `_forcedq` slug so they never mix with the auto runs.
    force_max_quality: bool = False
    force_quality_level: str = "hd2160"

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
        if self.force_max_quality:
            # Keeps forced-quality runs from ever being grouped with, or
            # compared against, the auto-ABR runs of the same regime.
            slug += "_forcedq"
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
    suffix = (
        cfg.experiment_id.split("-")[-1] if cfg.experiment_id else uuid.uuid4().hex[:8]
    )
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
            "states": [
                {
                    "checks": [],
                    "actions": [
                        {
                            "type": "shell",
                            "params": {
                                "command": f"wget -O /dev/null {cfg.url_for(app)}"
                            },
                        }
                    ],
                    "end_state": "",
                }
            ],
        }

    url = cfg.url_for(app)
    if not url:
        raise ValueError(
            f"App '{app}' has no URL. Supply one via "
            f"ExperimentConfig(app_urls={{'{app}': '<join/watch url>'}})."
        )
    return {
        "specification": app,
        "states": [
            {
                "checks": [],
                "actions": [
                    {"type": "go_to_url", "params": {"url": url}},
                    {"type": "wait", "params": {"seconds": cfg.duration_s}},
                ],
                "end_state": "",
            }
        ],
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
        "interface": DOWNSTREAM_IFACE,  # only the shaped bottleneck
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
        r = requests.get(
            f"{SUBSTRATE}/capture/{capture_id}/pcap", timeout=HTTP_TIMEOUT * 4
        )
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
#  Real player-side QoE collector (real Chrome, inside the shaped namespace)
# ═════════════════════════════════════════════════════════════════════════════
# Browser driving is PR #167's `selenium_video_qoe/collect.py`: Xvfb + fluxbox +
# SeleniumBase/undetected-chromedriver running real headed google-chrome, all
# apps as sibling THREADS in ONE container (separate containers sharing a netns
# made the chromedriver sessions cross-attach). We reuse that and nothing else —
# the QoE derivation is shared/qoe.py.
def _run_cmd(args: list[str], timeout: int) -> tuple[int, str]:
    import subprocess

    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except FileNotFoundError as exc:
        return 127, str(exc)


def ns1_netns_path() -> Optional[str]:
    """Path to the ns1 network namespace, usable with `nsenter --net=`.

    ns1 is a *named* namespace bind-mounted at /run/netns/ns1 inside the
    substrate worker, so it is addressable from the host through that
    container's root: /proc/<container-host-pid>/root/run/netns/ns1.

    PR #167 instead read the anchor PID from /var/run/substrate/ns1.pid and used
    /proc/<pid>/ns/net. That only works when the substrate worker itself runs
    with `--pid host`; on this deployment it does not, so that PID is
    container-local and /proc/<pid>/ns/net on the host resolves to the ROOT
    namespace — the collector would run unshaped and the pcap would show none of
    its traffic. Resolving through the named namespace avoids that entirely.
    """
    rc, out = _run_cmd(
        [DOCKER_BIN, "inspect", "-f", "{{.State.Pid}}", SUBSTRATE_CONTAINER], 30
    )
    pid = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if rc != 0 or not pid.isdigit():
        return None
    return f"/proc/{pid}/root/run/netns/ns1"


def build_collector_jobs(
    cfg: ExperimentConfig, out_dir: Path
) -> tuple[list[dict], dict[str, str]]:
    """JOBS payload for the collector + {app: reason} for apps that are skipped."""
    reg, _ = _bind_shared()
    jobs: list[dict[str, Any]] = []
    skipped: dict[str, str] = {}
    display = FIRST_DISPLAY_NUM
    for app in cfg.apps:
        spec = reg.get(app)
        if not spec.has_player_qoe:
            continue  # shell app: driven by the substrate worker, no player
        url = reg.url_for(app, cfg.app_urls)
        if not url:
            skipped[app] = f"no URL configured for '{app}' (set app_urls['{app}'])"
            continue
        if spec.needs_peer and not (cfg.peers or {}).get(app):
            # Never emit conferencing numbers measured against a local preview.
            skipped[app] = (
                f"'{app}' needs a remote peer publishing video; none configured "
                f"(set peers={{'{app}': '<peer room/bot id>'}}). See REALQOE.md."
            )
            continue
        jobs.append(
            {
                "app": app,
                "kind": spec.kind,
                "url": url,
                "display_num": display,
                "out_path": f"/out/{app}_stats.jsonl",
                "duration_seconds": cfg.duration_s,
                "sample_interval_seconds": 1.0,
                "join_timeout_seconds": 180,
                "barrier_timeout_seconds": max(360, cfg.duration_s + 240),
                # Collector applies this to YouTube only; harmless for other apps.
                "force_max_quality": bool(cfg.force_max_quality),
                "force_quality_level": cfg.force_quality_level,
            }
        )
        display += 1
    return jobs, skipped


def run_qoe_collectors(cfg: ExperimentConfig, run_dir: Path) -> dict[str, Any]:
    """Drive every browser app for real and return where each wrote its samples."""
    reg, _ = _bind_shared()
    if reg is None:
        return {
            "jobs": [],
            "skipped": {},
            "stats": {},
            "error": "shared.apps unavailable",
        }
    jobs, skipped = build_collector_jobs(cfg, run_dir)
    result: dict[str, Any] = {"jobs": jobs, "skipped": skipped, "stats": {}, "log": ""}
    for app, why in skipped.items():
        print(f"    · {app}: SKIPPED — {why}")
    if not jobs:
        return result

    netns = ns1_netns_path()
    if not netns:
        result["error"] = (
            f"could not resolve the ns1 namespace via container "
            f"'{SUBSTRATE_CONTAINER}' — is it running, and is docker reachable?"
        )
        print(f"    ! {result['error']}")
        return result

    qoe_dir = run_dir / "qoe"
    qoe_dir.mkdir(parents=True, exist_ok=True)
    budget = int(cfg.duration_s + 420)
    args = [
        DOCKER_BIN,
        "run",
        "--rm",
        "--privileged",
        "--pid",
        "host",
        # ns1 routes to the real internet, not Docker's embedded resolver.
        "--dns",
        "8.8.8.8",
        "--env",
        f"JOBS={json.dumps(jobs)}",
        "--volume",
        f"{qoe_dir}:/out",
    ]
    # Dev override: run a host copy of collect.py inside the published image.
    # Lets a collector change be exercised without rebuilding a ~2GB image —
    # useful on a host that has no room to hold two copies of it at once.
    collector_src = os.environ.get("PRAMANA_COLLECTOR_SRC")
    if collector_src:
        src = Path(collector_src).expanduser().resolve()
        args += ["--volume", f"{src}:/app/collect.py:ro"]
        print(f"    · collector source overridden from {src}")
    args += [
        "--entrypoint",
        "nsenter",
        COLLECTOR_IMAGE,
        f"--net={netns}",
        "--",
        "python3",
        "collect.py",
    ]
    print(
        f"    · driving {len(jobs)} browser app(s) in real Chrome inside ns1 "
        f"({', '.join(j['app'] for j in jobs)}) ..."
    )
    rc, log = _run_cmd(args, timeout=budget)
    result["log"] = log
    result["returncode"] = rc
    (run_dir / "collector.log").write_text(log)
    for line in log.splitlines():
        if "summary:" in line or "failed" in line.lower():
            print(f"      {line.strip()[:160]}")
    if rc != 0:
        print(f"    ! collector exited {rc} (see {run_dir / 'collector.log'})")
    for j in jobs:
        f = qoe_dir / f"{j['app']}_stats.jsonl"
        if f.exists() and f.stat().st_size > 0:
            result["stats"][j["app"]] = str(f)
        else:
            skipped.setdefault(j["app"], "collector produced no samples")
    return result


def collect_player_qoe(
    cfg: ExperimentConfig, run_dir: Path, transfers: Optional[dict[str, dict]] = None
) -> dict[str, Any]:
    """Drive the apps, then reduce their samples with shared.qoe (the definitions)."""
    reg, qoelib = _bind_shared()
    if reg is None or qoelib is None:
        print(f"    ! {shared_repo_status()}")
        return {}
    run = run_qoe_collectors(cfg, run_dir)
    per_app: dict[str, Any] = {}
    for app in cfg.apps:
        spec = reg.get(app)
        if not spec.has_player_qoe:
            per_app[app] = qoelib.summarize(
                None, app, transfer=(transfers or {}).get(app)
            )
            continue
        reason = run["skipped"].get(app)
        per_app[app] = qoelib.summarize(
            run["stats"].get(app, []), app, skipped_reason=reason
        )
    return per_app


# ═════════════════════════════════════════════════════════════════════════════
#  Telemetry HTTP client (:8004)
# ═════════════════════════════════════════════════════════════════════════════
def _telemetry_qoe(q: dict[str, Any]) -> dict[str, Any]:
    """Project one app's QoE onto the flat QoEMetrics shape Telemetry stores.

    Timelines are dropped (they live in record.json); nulls are preserved so the
    dataset distinguishes "measured as zero" from "not measurable".
    """
    if not q:
        return {}
    keep = (
        "video_startup_time_ms",
        "mean_bitrate_mbps",
        "max_bitrate_mbps",
        "min_bitrate_mbps",
        "mean_watched_bitrate_mbps",
        "bitrate_changes",
        "rebuffer_events",
        "rebuffer_duration_ms",
        "stall_duration_ms",
        "video_resolution_p",
        "frame_rate_fps",
        "dropped_frame_pct",
        "dropped_video_frames",
        "total_video_frames",
        "resolution_changes",
        "mean_buffer_ahead_secs",
        "min_buffer_ahead_secs",
        "watched_seconds",
        "session_seconds",
        "connection_speed_estimate_mbps",
        "packet_loss_pct",
        "mean_jitter_secs",
        "is_live",
        "video_duration_secs",
        "delivered_fraction_of_video",
    )
    out = {k: q[k] for k in keep if k in q}
    out["player_qoe_available"] = q.get("player_qoe_available", False)
    out["status"] = q.get("status")
    if q.get("reason"):
        out["reason"] = q["reason"]
    if q.get("resolutions_observed"):
        out["resolutions_observed"] = q["resolutions_observed"]
    if q.get("transfer"):
        out["transfer"] = q["transfer"]
    return out


def telemetry_post_result(
    experiment_id: str,
    app: str,
    cfg: ExperimentConfig,
    measured_throughput: Optional[float],
    qoe: dict[str, Any],
    pcap_path: str,
    status: str = "success",
) -> Optional[dict[str, Any]]:
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
import re
import socket
import struct
from collections import defaultdict, namedtuple

_Pkt = namedtuple("_Pkt", ["ts", "orig_len", "data"])

# Link-layer types we know how to strip to get at the IP header.
_LT_ETHERNET, _LT_RAW, _LT_LINUX_SLL, _LT_LINUX_SLL2 = 1, 101, 113, 276
_LT_NULL, _LT_LOOP = 0, 108


def _iter_pcapng(path: str):
    """Yield (ts, orig_len, linktype, data) from a pcapng file (EPB + SPB blocks).

    Unlike the previous implementation this yields the *packet bytes*, which is
    what per-app attribution needs (IP headers, DNS answers, TLS SNI).
    """
    with open(path, "rb") as fh:
        blob = fh.read()
    n = len(blob)
    off = 0
    endian = "<"
    tsresol = 1e-6  # default microseconds
    linktype = _LT_ETHERNET
    while off + 12 <= n:
        btype = struct.unpack_from(endian + "I", blob, off)[0]
        if btype == 0x0A0D0D0A:  # Section Header Block — read byte-order magic
            bom = struct.unpack_from("<I", blob, off + 8)[0]
            endian = "<" if bom == 0x1A2B3C4D else ">"
        blen = struct.unpack_from(endian + "I", blob, off + 4)[0]
        if blen < 12 or off + blen > n:
            break
        if btype == 0x00000001:  # Interface Description Block
            linktype = struct.unpack_from(endian + "H", blob, off + 8)[0]
            opt = off + 16
            while opt + 4 <= off + blen - 4:
                code, olen = struct.unpack_from(endian + "HH", blob, opt)
                if code == 0:
                    break
                if code == 9 and olen >= 1:  # if_tsresol
                    raw = blob[opt + 4]
                    tsresol = (
                        (1.0 / (2 ** (raw & 0x7F)))
                        if (raw & 0x80)
                        else (10.0 ** -(raw & 0x7F))
                    )
                opt += 4 + olen + ((4 - olen % 4) % 4)
        elif btype == 0x00000006:  # Enhanced Packet Block
            _iface, tsh, tsl, caplen, origlen = struct.unpack_from(
                endian + "IIIII", blob, off + 8
            )
            ts = ((tsh << 32) | tsl) * tsresol
            data = blob[off + 28 : off + 28 + caplen]
            yield ts, origlen, linktype, data
        elif btype == 0x00000003:  # Simple Packet Block
            origlen = struct.unpack_from(endian + "I", blob, off + 8)[0]
            caplen = min(origlen, blen - 16)
            yield 0.0, origlen, linktype, blob[off + 12 : off + 12 + caplen]
        off += blen


def _iter_pcap_classic(path: str):
    """Yield (ts, orig_len, linktype, data) from a classic pcap, streaming."""
    with open(path, "rb") as fh:
        header = fh.read(24)
        if len(header) < 24:
            return
        magic = struct.unpack("<I", header[:4])[0]
        if magic in (0xA1B2C3D4, 0xA1B23C4D):
            endian, nano = "<", magic == 0xA1B23C4D
        elif magic in (0xD4C3B2A1, 0x4D3CB2A1):
            endian, nano = ">", magic == 0x4D3CB2A1
        else:
            return
        linktype = struct.unpack(endian + "I", header[20:24])[0]
        hdr_fmt = endian + "IIII"
        while True:
            ph = fh.read(16)
            if len(ph) < 16:
                break
            ts_sec, ts_frac, incl_len, orig_len = struct.unpack(hdr_fmt, ph)
            data = fh.read(incl_len)
            if len(data) < incl_len:
                break
            ts = ts_sec + ts_frac / (1e9 if nano else 1e6)
            yield ts, orig_len, linktype, data


def iter_capture(path: str):
    """Stream (ts, orig_len, linktype, data) from a pcap **or** pcapng file.

    Streaming matters: run captures are routinely 100-300 MB, and the old
    "read every packet into a list" path made per-app analysis memory-bound.
    """
    f = Path(path)
    if not f.exists() or f.stat().st_size < 24:
        return
    with open(path, "rb") as fh:
        magic = fh.read(4)
    if magic == b"\x0a\x0d\x0d\x0a":
        yield from _iter_pcapng(path)
    else:
        yield from _iter_pcap_classic(path)


def _read_capture(path: str):
    """Back-compat: materialise the whole capture as a list of `_Pkt`."""
    return [_Pkt(ts, ol, data) for ts, ol, _lt, data in iter_capture(path)]


def _l3(linktype: int, data: bytes) -> tuple[Optional[int], bytes]:
    """Strip the link layer. Returns (ethertype-ish family, l3 bytes).

    Family is 4 for IPv4, 6 for IPv6, None for anything else (ARP, LLDP, ...).
    """
    if linktype == _LT_ETHERNET:
        if len(data) < 14:
            return None, b""
        et = struct.unpack_from(">H", data, 12)[0]
        off = 14
        while et in (0x8100, 0x88A8) and len(data) >= off + 4:  # VLAN tags
            et = struct.unpack_from(">H", data, off + 2)[0]
            off += 4
        if et == 0x0800:
            return 4, data[off:]
        if et == 0x86DD:
            return 6, data[off:]
        return None, b""
    if linktype == _LT_LINUX_SLL:
        if len(data) < 16:
            return None, b""
        et = struct.unpack_from(">H", data, 14)[0]
        return (4 if et == 0x0800 else 6 if et == 0x86DD else None), data[16:]
    if linktype == _LT_LINUX_SLL2:
        if len(data) < 20:
            return None, b""
        et = struct.unpack_from(">H", data, 0)[0]
        return (4 if et == 0x0800 else 6 if et == 0x86DD else None), data[20:]
    if linktype == _LT_RAW:
        if not data:
            return None, b""
        v = data[0] >> 4
        return (4 if v == 4 else 6 if v == 6 else None), data
    if linktype in (_LT_NULL, _LT_LOOP):
        if len(data) < 4:
            return None, b""
        return 4, data[4:]
    return None, b""


def _is_private_ip(ip: str) -> bool:
    """RFC1918/loopback/link-local test. String prefixes get this wrong:
    "172.2" also matches public 172.2.x.x and 172.200.x.x."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


def _parse_ip(fam: int, buf: bytes):
    """Return (src, dst, proto, l4_bytes) or None."""
    if fam == 4:
        if len(buf) < 20:
            return None
        ihl = (buf[0] & 0x0F) * 4
        if ihl < 20 or len(buf) < ihl:
            return None
        proto = buf[9]
        return (
            socket.inet_ntoa(buf[12:16]),
            socket.inet_ntoa(buf[16:20]),
            proto,
            buf[ihl:],
        )
    if fam == 6:
        if len(buf) < 40:
            return None
        proto = buf[6]
        src = socket.inet_ntop(socket.AF_INET6, buf[8:24])
        dst = socket.inet_ntop(socket.AF_INET6, buf[24:40])
        return src, dst, proto, buf[40:]
    return None


def _throughput_series(packets, bin_s: float = 1.0):
    """(times, mbps) per bin — mirrors analyze_pcap.compute_throughput."""
    if not packets:
        return [], []
    t0 = min(p.ts for p in packets)
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


def _series_from_bins(bins: dict[int, int], nbins: int, bin_s: float):
    """Dense (times, mbps) over [0, nbins) from a sparse bin→bytes map."""
    times = [i * bin_s for i in range(nbins)]
    rates = [bins.get(i, 0) * 8 / bin_s / 1e6 for i in range(nbins)]
    return times, rates


# ═════════════════════════════════════════════════════════════════════════════
#  Per-app attribution — split one shared capture into per-app traffic
# ═════════════════════════════════════════════════════════════════════════════
# Two mechanisms, in priority order:
#
#   1. LOCAL ALIAS IP ("namespace IP") — when the substrate has assigned each app
#      its own source IP (POST /shape/per_app_marks), every packet carries the
#      app identity in its local endpoint. Exact, no heuristics. Used whenever
#      `local_ip_map` is supplied.
#
#   2. REMOTE HOSTNAME — the deployed substrate runs every browser app in the
#      SAME namespace (ns1, one source IP), so mechanism 1 is unavailable there.
#      Instead we learn IP→hostname from the plaintext DNS answers and TLS SNI
#      inside the very same capture, then map hostname→app by domain suffix.
#      Measured coverage on real YouTube+Vimeo captures: >99% of bytes.
#
# Traffic that belongs to the browser rather than any app (Chrome component
# updates, optimization-guide model downloads — routinely *tens of MB*) is
# bucketed as `browser_infra` and kept out of every per-app plot.

_HOST_RE = re.compile(rb"[A-Za-z0-9._-]{4,253}")
# YouTube's media CDN also appears as `r6---sn-<id>.gvt1.com`; plain `gvt1.com`
# hosts (edgedl, redirector) are Chrome's own downloads, so match the sn- form.
_YT_GVT_RE = re.compile(r"(^|\.)r[0-9]+-+sn-[a-z0-9-]+\.gvt1\.com$")


def _dns_name(buf: bytes, off: int) -> tuple[Optional[str], int]:
    """Decode a (possibly compressed) DNS name. Returns (name, next_offset)."""
    labels: list[str] = []
    jumped = False
    nxt = off
    for _ in range(128):
        if off >= len(buf):
            return None, len(buf)
        ln = buf[off]
        if ln == 0:
            off += 1
            break
        if ln & 0xC0 == 0xC0:
            if off + 2 > len(buf):
                return None, len(buf)
            ptr = struct.unpack_from(">H", buf, off)[0] & 0x3FFF
            if not jumped:
                nxt = off + 2
            off = ptr
            jumped = True
            continue
        off += 1
        labels.append(buf[off : off + ln].decode("ascii", "replace"))
        off += ln
    return ".".join(labels), (nxt if jumped else off)


def _harvest_dns(l4: bytes, out: dict[str, str]) -> None:
    """Record IP→hostname from a DNS response (A / AAAA, following CNAMEs)."""
    dns = l4[8:]
    if len(dns) < 12:
        return
    flags, qd, an = struct.unpack_from(">HHH", dns, 2)
    if not flags & 0x8000 or an == 0:  # responses only
        return
    off = 12
    qname = None
    for _ in range(qd):
        nm, off = _dns_name(dns, off)
        if qname is None:
            qname = nm
        off += 4
    # A CNAME chain ends on the CDN's own name (`e42.a.akamaiedge.net`), which
    # says nothing about the app. The *queried* name is the app-identifying one
    # (`vod-adaptive-ak.vimeocdn.com`), so prefer the question and fall back to
    # the record's owner name.
    for _ in range(an):
        owner, off = _dns_name(dns, off)
        if off + 10 > len(dns):
            return
        rtype, _rclass, _ttl, rdlen = struct.unpack_from(">HHIH", dns, off)
        off += 10
        rdata = dns[off : off + rdlen]
        off += rdlen
        name = qname or owner
        try:
            if rtype == 1 and rdlen == 4:
                out.setdefault(socket.inet_ntoa(rdata), name or "")
            elif rtype == 28 and rdlen == 16:
                out.setdefault(socket.inet_ntop(socket.AF_INET6, rdata), name or "")
        except OSError:
            continue


def _harvest_sni(l4: bytes, dst: str, out: dict[str, str]) -> None:
    """Record dst-IP→SNI from a TLS ClientHello (TCP payload)."""
    if len(l4) < 20:
        return
    doff = (l4[12] >> 4) * 4
    b = l4[doff:]
    # TLS record: handshake(0x16), then ClientHello(0x01)
    if len(b) < 45 or b[0] != 0x16 or b[5] != 0x01:
        return
    try:
        o = 5 + 4 + 2 + 32  # record + hs hdr + version + random
        o += 1 + b[o]  # session id
        o += 2 + struct.unpack_from(">H", b, o)[0]  # cipher suites
        o += 1 + b[o]  # compression methods
        ext_len = struct.unpack_from(">H", b, o)[0]
        o += 2
        end = min(o + ext_len, len(b))
        while o + 4 <= end:
            etype, elen = struct.unpack_from(">HH", b, o)
            o += 4
            if etype == 0 and o + 5 <= len(b):  # server_name
                nlen = struct.unpack_from(">H", b, o + 3)[0]
                host = b[o + 5 : o + 5 + nlen].decode("ascii", "replace")
                if host:
                    out.setdefault(dst, host)
                return
            o += elen
    except (struct.error, IndexError):
        return


def _host_matches(host: str, domains: Iterable[str]) -> bool:
    h = (host or "").lower().rstrip(".")
    if not h:
        return False
    for d in domains:
        d = d.lower()
        if "/" in d:  # e.g. "akamaized.net/vimeo" → both parts
            a, b = d.split("/", 1)
            if h.endswith(a) and b in h:
                return True
            continue
        if h == d or h.endswith("." + d):
            return True
    return False


def classify_host(host: str, apps: Iterable[str]) -> str:
    """Map a hostname to one of `apps`, or 'browser_infra' / 'other'."""
    h = (host or "").lower().rstrip(".")
    if not h:
        return "other"
    if _YT_GVT_RE.search(h):
        return "youtube" if "youtube" in list(apps) else "other"
    for app in apps:
        if _host_matches(h, app_domains(app)):
            return app
    if _host_matches(h, BROWSER_INFRA_DOMAINS):
        return "browser_infra"
    return "other"


def _is_private(ip: str) -> bool:
    if ":" in ip:
        return ip.lower().startswith(("fc", "fd", "fe80"))
    try:
        a, b = (int(x) for x in ip.split(".")[:2])
    except ValueError:
        return False
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


@dataclass
class AppTraffic:
    """Time-binned throughput for one app in one direction."""

    app: str
    direction: str  # "download" | "upload"
    times: list[float] = field(default_factory=list)
    mbps: list[float] = field(default_factory=list)
    total_mb: float = 0.0
    packets: int = 0
    avg_throughput_mbps: Optional[float] = None
    peak_throughput_mbps: Optional[float] = None
    p95_throughput_mbps: Optional[float] = None
    stall_seconds: Optional[float] = None
    stall_bins: list[int] = field(default_factory=list)
    delivered_fraction: Optional[float] = None  # share of the run with traffic
    mean_over_cap: Optional[float] = None  # avg ÷ cap
    classification: str = "no_traffic"  # served | starved | no_traffic
    hosts: list[str] = field(default_factory=list)

    def as_record(self) -> dict[str, Any]:
        """Record-safe dict (drops the full series to keep record.json small)."""
        d = asdict(self)
        d.pop("times", None)
        d.pop("mbps", None)
        d.pop("stall_bins", None)
        return d


@dataclass
class CaptureAttribution:
    """The result of splitting one capture into per-app, per-direction traffic."""

    method: str = "none"  # local_alias_ip | remote_hostname | none
    note: str = ""  # why attribution produced nothing, if so
    bin_s: float = 1.0
    duration_s: float = 0.0
    nbins: int = 0
    local_ips: list[str] = field(default_factory=list)
    per_app: dict[str, dict[str, AppTraffic]] = field(default_factory=dict)
    host_bytes: dict[str, int] = field(default_factory=dict)
    bucket_mb: dict[str, float] = field(default_factory=dict)
    attributed_fraction: float = 0.0  # share of bytes mapped to a named bucket
    total_mb: float = 0.0

    def traffic(self, app: str, direction: str) -> Optional[AppTraffic]:
        return (self.per_app.get(app) or {}).get(direction)


def _finalise(
    at: AppTraffic, cap_mbps: float, bins: dict[int, int], nbins: int, bin_s: float
) -> AppTraffic:
    """Fill the derived QoE-proxy metrics for one app/direction series."""
    at.times, at.mbps = _series_from_bins(bins, nbins, bin_s)
    total_bytes = sum(bins.values())
    at.total_mb = round(total_bytes / 1e6, 3)
    if not at.mbps or total_bytes == 0:
        at.classification = "no_traffic"
        return at

    span = nbins * bin_s
    at.avg_throughput_mbps = round(total_bytes * 8 / max(span, 1e-9) / 1e6, 3)
    at.peak_throughput_mbps = round(max(at.mbps), 3)
    srt = sorted(at.mbps)
    at.p95_throughput_mbps = round(srt[min(len(srt) - 1, int(0.95 * len(srt)))], 3)

    # Stall = a bin where throughput fell below 5% of the cap (near-zero traffic).
    stall_thresh = max(0.05 * cap_mbps, 0.01)
    at.stall_bins = [i for i, r in enumerate(at.mbps) if r < stall_thresh]
    at.stall_seconds = round(len(at.stall_bins) * bin_s, 2)
    # Delivered fraction = share of the run that actually had active traffic.
    at.delivered_fraction = (
        round(1.0 - len(at.stall_bins) / nbins, 3) if nbins else None
    )
    if cap_mbps > 0:
        at.mean_over_cap = round(at.avg_throughput_mbps / cap_mbps, 3)
        # Supervisor's rule: peak ≥ 30% of cap means the app really streamed.
        at.classification = (
            "served" if at.peak_throughput_mbps >= 0.30 * cap_mbps else "starved"
        )
    else:
        at.classification = "served"
    return at


def attribute_capture(
    pcap: Path,
    apps: Iterable[str],
    cap_mbps: float,
    bin_s: float = 1.0,
    local_ip_map: Optional[dict[str, str]] = None,
) -> CaptureAttribution:
    """Split a shared capture into per-app download/upload throughput series.

    `local_ip_map` maps app → its namespace/alias IP. When supplied (substrate
    per-app marks), attribution is exact and by local IP. Otherwise the remote
    endpoint is identified from in-capture DNS answers + TLS SNI.
    """
    apps = [a for a in apps]
    res = CaptureAttribution(bin_s=bin_s)
    if not pcap.exists() or pcap.stat().st_size < 24:
        return res

    ip_host: dict[str, str] = {}
    ip_from_alias = {ip: app for app, ip in (local_ip_map or {}).items() if ip}
    frames = 0

    # ── pass 1: learn IP→hostname, local endpoints, and the time base ────────
    endpoint_pkts: dict[str, int] = defaultdict(int)
    t0 = None
    tmax = 0.0
    total_pkts = 0
    for ts, _ol, lt, data in iter_capture(str(pcap)):
        frames += 1
        fam, l3 = _l3(lt, data)
        if fam is None:
            continue
        parsed = _parse_ip(fam, l3)
        if parsed is None:
            continue
        src, dst, proto, l4 = parsed
        if ts:
            t0 = ts if t0 is None else min(t0, ts)
            tmax = max(tmax, ts)
        total_pkts += 1
        endpoint_pkts[src] += 1
        endpoint_pkts[dst] += 1
        if proto == 17 and len(l4) >= 8:
            sport, _dport = struct.unpack_from(">HH", l4, 0)
            if sport == 53:
                _harvest_dns(l4, ip_host)
        elif proto == 6:
            _harvest_sni(l4, dst, ip_host)

    if not total_pkts or t0 is None:
        # Distinguish "empty capture" from "capture has no readable packet
        # payloads" — the latter happens with synthetic/length-only pcaps (e.g.
        # the generated `results/phase*` fixtures), where per-app attribution is
        # impossible in principle rather than broken here.
        if frames:
            res.note = (
                f"{frames} frames read but no IP headers could be parsed — "
                f"the capture carries lengths/timestamps only (synthetic or "
                f"payload-stripped), so it cannot be split per app"
            )
            print(f"  ! {res.note}")
        else:
            res.note = "capture is empty"
        return res

    res.duration_s = round(max(tmax - t0, 0.0), 2)
    res.nbins = max(int(math.ceil(res.duration_s / bin_s)), 1)

    # Local endpoints: private IPs that appear on a large share of packets. With
    # per-app alias IPs there are several; with the deployed single-namespace
    # setup there is exactly one.
    if ip_from_alias:
        res.local_ips = sorted(ip_from_alias)
        res.method = "local_alias_ip"
    else:
        res.local_ips = sorted(
            ip
            for ip, c in endpoint_pkts.items()
            if _is_private(ip) and c >= 0.10 * total_pkts
        )
        if not res.local_ips and endpoint_pkts:
            res.local_ips = [max(endpoint_pkts, key=lambda k: endpoint_pkts[k])]
        res.method = "remote_hostname"
    local = set(res.local_ips)

    # ── pass 2: bin bytes per (bucket, direction) ────────────────────────────
    buckets: dict[tuple[str, str], dict[int, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    pkt_counts: dict[tuple[str, str], int] = defaultdict(int)
    bucket_hosts: dict[str, set[str]] = defaultdict(set)
    host_bytes: dict[str, int] = defaultdict(int)
    bucket_bytes: dict[str, int] = defaultdict(int)
    total_bytes = 0

    for ts, ol, lt, data in iter_capture(str(pcap)):
        fam, l3 = _l3(lt, data)
        if fam is None:
            continue
        parsed = _parse_ip(fam, l3)
        if parsed is None:
            continue
        src, dst, _proto, _l4 = parsed
        total_bytes += ol

        # Direction is defined by the local endpoint: toward it = download.
        if dst in local:
            direction, remote, localside = "download", src, dst
        elif src in local:
            direction, remote, localside = "upload", dst, src
        else:
            bucket_bytes["off_link"] += ol
            continue

        if res.method == "local_alias_ip":
            bucket = ip_from_alias.get(localside, "other")
        else:
            host = ip_host.get(remote, "")
            bucket = classify_host(host, apps)
            if host:
                host_bytes[host] += ol
                if bucket in apps:
                    bucket_hosts[bucket].add(host)

        bucket_bytes[bucket] += ol
        if bucket in apps:
            idx = min(int((ts - t0) / bin_s), res.nbins - 1) if ts else 0
            buckets[(bucket, direction)][idx] += ol
            pkt_counts[(bucket, direction)] += 1

    res.total_mb = round(total_bytes / 1e6, 3)
    res.bucket_mb = {
        k: round(v / 1e6, 3)
        for k, v in sorted(bucket_bytes.items(), key=lambda kv: -kv[1])
    }
    res.host_bytes = dict(sorted(host_bytes.items(), key=lambda kv: -kv[1])[:40])
    named = sum(v for k, v in bucket_bytes.items() if k != "other")
    res.attributed_fraction = round(named / total_bytes, 4) if total_bytes else 0.0

    for app in apps:
        res.per_app[app] = {}
        for direction in ("download", "upload"):
            at = AppTraffic(app=app, direction=direction)
            at.packets = pkt_counts.get((app, direction), 0)
            at.hosts = sorted(bucket_hosts.get(app, ()))[:12]
            res.per_app[app][direction] = _finalise(
                at, cap_mbps, buckets.get((app, direction), {}), res.nbins, bin_s
            )
    return res


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
    delivered_fraction: Optional[float] = None  # avg / cap
    stall_seconds: Optional[float] = None  # seconds with ~no traffic
    shaping_verified: bool = False
    shaping_error_pct: Optional[float] = None


def analyze_pcap_netstats(pcap: Path, cap_mbps: float) -> NetStats:
    """Compute throughput stats from the capture and verify the cap was respected."""
    if not pcap.exists():
        return NetStats()
    # One pass, keeping the link type so each packet's direction is known: the
    # cap shapes the DOWNLOAD direction, so everything compared against it must
    # be download-only. Summing both directions and testing that against a
    # download cap produced ~20 spurious SHAPING failures, while the packets
    # showed the shaper never let download exceed the cap by more than ~3%.
    packets: list = []
    ip_pkts: list[tuple] = []  # (pkt, src, dst)
    endpoint_pkts: dict[str, int] = defaultdict(int)
    for ts_, ol, lt, data in iter_capture(str(pcap)):
        pkt = _Pkt(ts_, ol, data)
        packets.append(pkt)
        fam, l3 = _l3(lt, data)
        if fam is None:
            continue
        parsed = _parse_ip(fam, l3)
        if parsed is None:
            continue
        src, dst, _proto, _l4 = parsed
        ip_pkts.append((pkt, src, dst))
        endpoint_pkts[src] += 1
        endpoint_pkts[dst] += 1
    if not packets:
        return NetStats()

    total_bytes = sum(p.orig_len for p in packets)
    ts = [p.ts for p in packets if p.ts]
    duration = (max(ts) - min(ts)) if len(ts) > 1 else 0.0

    # Local side = the endpoint the capture sits behind. Prefer a private
    # address; fall back to the busiest endpoint, as attribution does.
    local = {ip for ip in endpoint_pkts if _is_private_ip(ip)}
    if not local and endpoint_pkts:
        local = {max(endpoint_pkts, key=lambda k: endpoint_pkts[k])}
    down = [pkt for pkt, _src, dst in ip_pkts if dst in local]
    down_bytes = sum(p.orig_len for p in down)

    ns = NetStats(
        packets=len(packets),
        duration_s=round(duration, 2),
        total_mb=round(total_bytes / 1e6, 2),  # whole capture, both directions
        avg_throughput_mbps=(
            round(down_bytes * 8 / max(duration, 0.001) / 1e6, 3) if duration else None
        ),
    )

    # per-second throughput series for peak / p95 / stalls — shaped direction
    _, rates = _throughput_series(down or packets, 1.0)
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
        ns.shaping_verified = ns.avg_throughput_mbps <= cap_mbps * (
            1 + SHAPING_TOLERANCE
        )
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
        pinned = (
            net.p95_throughput_mbps >= 0.9 * cap
            and net.avg_throughput_mbps >= 0.8 * cap
        )
        if pinned:
            return "starved"
    return "served"


# Back-compat aliases (older notebooks referenced these names)
def extract_app_qoe(app: str, cfg: ExperimentConfig) -> dict[str, Any]:
    return {}  # deployed substrate v2 engine exposes no player QoE


def classify_qoe(app: str, qoe: dict[str, Any], net: NetStats) -> str:
    return classify_traffic(app, net, None)  # type: ignore[arg-type]


# ═════════════════════════════════════════════════════════════════════════════
#  Plots
# ═════════════════════════════════════════════════════════════════════════════
# Convention (research guideline — enforced here, not left to the caller):
#
#   * NEVER plot combined throughput. Every run produces one throughput plot
#     PER APP, showing only that app's traffic.
#   * Video apps (youtube/twitch/vimeo/tubi) plot the DOWNLOAD direction only.
#     Upload is ACKs; adding it makes a 10 Mbps stream read as 11-12 Mbps.
#   * Conferencing apps (zoom/meet) plot upload and download as SEPARATE plots,
#     because both directions carry real media.
#
# `generate_run_plots()` is called automatically at the end of every run.

DISPLAY_NAMES = {
    "youtube": "YouTube",
    "vimeo": "Vimeo",
    "twitch": "Twitch",
    "tubi": "Tubi",
    "zoom": "Zoom",
    "meet": "Google Meet",
    "wget": "Bulk download (wget)",
}


def display_name(app: str) -> str:
    return DISPLAY_NAMES.get(app, app.title())


def _plt():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        return plt
    except Exception:
        return None


def _regime(cfg: ExperimentConfig) -> str:
    """`6 Mbps / 50ms / pfifo` — the bottleneck regime, for plot titles."""
    bits = [f"{cfg.bandwidth_mbps:g} Mbps", f"{cfg.latency_ms:g}ms", cfg.aqm]
    if cfg.loss_pct:
        bits.insert(2, f"{cfg.loss_pct:g}% loss")
    return " / ".join(bits)


def _plot_title(
    app: str, cfg: ExperimentConfig, direction: str, both_directions: bool
) -> str:
    # Video apps get the canonical `Throughput — YouTube — 6 Mbps / 50ms / pfifo`.
    # Call apps produce two plots, so the direction is added to tell them apart.
    what = "Throughput" if not both_directions else f"Throughput ({direction})"
    return f"{what} — {display_name(app)} — {_regime(cfg)}"


def _shade_stalls(ax, at: AppTraffic, bin_s: float) -> int:
    """Shade every near-zero-throughput bin. Returns the number shaded.

    This is a NETWORK-idle band, not a stall: a video player bursts to fill its
    buffer then coasts, so idle-with-a-full-buffer is healthy. Real rebuffering
    comes from the player (shared.qoe) and is shaded separately.
    """
    if not at.stall_bins:
        return 0
    # Merge adjacent stall bins into spans so the legend stays readable.
    spans: list[tuple[float, float]] = []
    lo = prev = at.stall_bins[0]
    for i in at.stall_bins[1:]:
        if i == prev + 1:
            prev = i
            continue
        spans.append((lo * bin_s, (prev + 1) * bin_s))
        lo = prev = i
    spans.append((lo * bin_s, (prev + 1) * bin_s))
    for j, (a, b) in enumerate(spans):
        ax.axvspan(
            a,
            b,
            color="#F44336",
            alpha=0.10,
            label="idle / no active transfer" if j == 0 else None,
        )
    return len(spans)


def plot_app_throughput(
    at: AppTraffic, cfg: ExperimentConfig, out: Path, both_directions: bool = False
) -> Optional[Path]:
    """One app, one direction, one PNG: its traffic + the cap + avg/peak."""
    plt = _plt()
    # No traffic attributed (e.g. a skipped app) => nothing to plot, and the
    # derived stats are None. Never format None into a label.
    if plt is None or not at.times or at.avg_throughput_mbps is None:
        return None
    cap = cfg.bandwidth_mbps or 0.0
    color = app_color(at.app)
    fig, ax = plt.subplots(figsize=(12, 4.2))
    ax.plot(at.times, at.mbps, linewidth=1.0, color=color)
    ax.fill_between(at.times, at.mbps, alpha=0.15, color=color)
    ax.axhline(cap, color="green", linewidth=1.3, alpha=0.85, label=f"cap {cap:g} Mbps")
    if at.peak_throughput_mbps is not None:
        ax.axhline(
            at.peak_throughput_mbps,
            color="#7B1FA2",
            linestyle="-.",
            linewidth=1.0,
            alpha=0.85,
            label=f"peak {at.peak_throughput_mbps:.2f} Mbps",
        )
    if at.avg_throughput_mbps is not None:
        ax.axhline(
            at.avg_throughput_mbps,
            color="red",
            linestyle="--",
            linewidth=1.0,
            alpha=0.85,
            label=f"avg {at.avg_throughput_mbps:.2f} Mbps",
        )
    ax.set_xlabel("Time (s)")
    ax.set_ylabel(f"{at.direction.title()} throughput (Mbps)")
    ax.set_title(_plot_title(at.app, cfg, at.direction, both_directions))
    ax.set_xlim(0, max(at.times) if at.times else 1)
    ax.set_ylim(
        bottom=0, top=max(cap * 1.25, (at.peak_throughput_mbps or 0) * 1.15, 0.1)
    )
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", fontsize=8)
    ax.annotate(
        f"avg {at.avg_throughput_mbps:.2f} Mbps   peak {at.peak_throughput_mbps:.2f} Mbps"
        f"   total {at.total_mb:.1f} MB",
        xy=(0.01, 0.96),
        xycoords="axes fraction",
        va="top",
        fontsize=9,
        bbox=dict(boxstyle="round,pad=0.35", fc="white", ec="#BBBBBB", alpha=0.85),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def _panel_verdict(at: AppTraffic, pq: dict[str, Any]) -> tuple[str, str, str]:
    """Honest classification for one QoE-summary panel: (label, why, colour).

    The network side only ever sees bytes. An app can pull megabytes and never
    render a frame, so a verdict drawn from throughput alone must never claim
    the app played. When the player answered, the player decides; when it did
    not, the label stays explicitly provisional.
    """
    real = bool(pq.get("player_qoe_available"))
    status = str(pq.get("status") or "")
    reason = str(pq.get("reason") or "")

    if real:
        # Real player metrics: the media clock is the arbiter.
        watched = pq.get("watched_seconds") or 0
        if pq.get("video_resolution_p") and watched > 0:
            rebuffers = pq.get("rebuffer_events") or 0
            frozen_ms = pq.get("rebuffer_duration_ms") or 0
            if rebuffers or frozen_ms:
                return (
                    "degraded",
                    f"player clock advanced but rebuffered {rebuffers}× "
                    f"({frozen_ms:.0f} ms frozen): playback was impaired",
                    "#EF6C00",
                )
            return (
                "served",
                f"player clock advanced {watched:.0f}s with no rebuffering: "
                "the app really streamed",
                "#2E7D32",
            )
        return (
            "no_playback",
            "player exposed no advancing media clock: the app did not play",
            "#C62828",
        )

    # No usable player metrics — but the player may still have reported that
    # playback never started, which is evidence, not a gap.
    if status in ("no_data", "skipped") and reason.startswith("no_playback"):
        return (
            "no_playback",
            "player clock never advanced past 0 for the whole run: "
            "the app did not play",
            "#C62828",
        )

    # Nothing from the player at all: network-side only, and provisional.
    if at.classification == "no_traffic":
        return ("no_traffic", "no bytes attributed to this app", "#C62828")
    if at.classification == "starved":
        return (
            "starved (network only)",
            "peak < 30% of cap: the app got almost nothing " "— playback unconfirmed",
            "#C62828",
        )
    return (
        "network activity only — playback unconfirmed",
        "peak ≥ 30% of cap, so bytes moved; without player data that is "
        "NOT evidence the app streamed",
        "#EF6C00",
    )


def plot_app_qoe_summary(
    at: AppTraffic,
    cfg: ExperimentConfig,
    out: Path,
    player_qoe: Optional[dict[str, Any]] = None,
    bin_s: float = 1.0,
) -> Optional[Path]:
    """QoE summary for one app.

    When `player_qoe` carries real player-side metrics (``shared.qoe.summarize``
    output with ``player_qoe_available`` true), the figure shows the REAL
    signals: buffer-ahead with true rebuffer spans shaded, the resolution
    timeline, and the dropped-frame percentage — and says so on its face.

    Without them it falls back to network-derived proxies and labels them as
    proxies: throughput, an *idle* band (explicitly not a stall claim), the
    delivered fraction, peak vs average, and a served/starved verdict.
    """
    plt = _plt()
    if plt is None or not at.times or at.avg_throughput_mbps is None:
        return None
    cap = cfg.bandwidth_mbps or 0.0
    pct = (lambda v: 100 * (v or 0) / cap) if cap > 0 else (lambda v: float("nan"))
    color = app_color(at.app)

    pq = player_qoe or {}
    real = bool(pq.get("player_qoe_available"))
    series = pq.get("series") or {}
    buf_series = series.get("buffer_ahead_secs") or []
    res_series = series.get("resolution_p") or []
    rebuffer_spans = pq.get("rebuffer_spans") or []

    panels = (
        1 + (1 if (real and buf_series) else 0) + (1 if (real and res_series) else 0)
    )
    heights = [2] + [1] * (panels - 1)
    # Text below the axes is positioned in INCHES-normalised units: a fixed
    # figure fraction shrinks as panels are added and collides with the x-axis.
    # Both branches now carry a two-line classification box, and the honest
    # footnote can wrap to a second line — reserve room for both.
    reserve_in = 1.95
    fig_h = 4.4 + 1.9 * (panels - 1) + reserve_in
    fig, axes = plt.subplots(
        panels,
        1,
        figsize=(12, fig_h),
        sharex=True,
        gridspec_kw={"height_ratios": heights},
    )
    axes = [axes] if panels == 1 else list(axes)
    ax = axes[0]

    # ── panel 1: this app's throughput ───────────────────────────────────────
    nstalls = _shade_stalls(ax, at, bin_s)
    ax.plot(
        at.times,
        at.mbps,
        linewidth=1.0,
        color=color,
        label=f"{display_name(at.app)} {at.direction}",
    )
    ax.fill_between(at.times, at.mbps, alpha=0.15, color=color)
    ax.axhline(cap, color="green", linewidth=1.3, alpha=0.85, label=f"cap {cap:g} Mbps")
    ax.axhline(
        at.peak_throughput_mbps or 0,
        color="#7B1FA2",
        linestyle="-.",
        linewidth=1.0,
        alpha=0.9,
        label=f"peak {at.peak_throughput_mbps:.2f} Mbps",
    )
    ax.axhline(
        at.avg_throughput_mbps or 0,
        color="red",
        linestyle="--",
        linewidth=1.0,
        alpha=0.9,
        label=f"avg {at.avg_throughput_mbps:.2f} Mbps",
    )
    # Real rebuffers, from the player — drawn over the throughput so the reader
    # can see that idle network time and frozen playback are different things.
    for j, (a, b) in enumerate(rebuffer_spans):
        ax.axvspan(
            a,
            b,
            color="#D32F2F",
            alpha=0.30,
            hatch="//",
            zorder=3,
            label="REBUFFER (player)" if j == 0 else None,
        )
    ax.set_ylabel(f"{at.direction.title()} tput (Mbps)")
    ax.set_ylim(
        bottom=0, top=max(cap * 1.25, (at.peak_throughput_mbps or 0) * 1.15, 0.1)
    )
    ax.set_xlim(0, max(at.times))
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", fontsize=8, ncol=2)
    ax.set_title(
        f"QoE summary — {display_name(at.app)} — {_regime(cfg)}"
        + ("   [real player-side metrics]" if real else "   [network proxies]")
    )

    idx = 1
    # ── panel 2: buffer ahead + real rebuffer spans ──────────────────────────
    if real and buf_series:
        bx = axes[idx]
        idx += 1
        bt = [d["t"] for d in buf_series]
        bv = [d["v"] for d in buf_series]
        bx.plot(bt, bv, linewidth=1.1, color="#1565C0")
        bx.fill_between(bt, bv, alpha=0.18, color="#1565C0")
        for j, (a, b) in enumerate(rebuffer_spans):
            bx.axvspan(
                a,
                b,
                color="#D32F2F",
                alpha=0.30,
                hatch="//",
                label="rebuffer" if j == 0 else None,
            )
        bx.set_ylabel("buffer ahead (s)")
        bx.grid(True, alpha=0.3)
        bx.set_ylim(bottom=0)
        if rebuffer_spans:
            bx.legend(loc="upper right", fontsize=8)

    # ── panel 3: rendered resolution over time ───────────────────────────────
    if real and res_series:
        rx = axes[idx]
        idx += 1
        rt = [d["t"] for d in res_series]
        rv = [d["v"] for d in res_series]
        rt2 = rt + [max(at.times)]
        rv2 = rv + [rv[-1]]
        rx.step(rt2, rv2, where="post", linewidth=1.4, color="#2E7D32")
        rx.scatter(rt, rv, s=18, color="#2E7D32", zorder=3)
        rx.set_ylabel("resolution (p)")
        rx.set_xlabel("Time (s)")
        rx.grid(True, alpha=0.3)
        ys = sorted(set(rv))
        rx.set_yticks(ys)
        rx.set_ylim(min(ys) * 0.8, max(ys) * 1.2)
    else:
        axes[-1].set_xlabel("Time (s)")

    # ── metrics panel under the axes ─────────────────────────────────────────
    if real:

        def g(k, fmt="{}", sfx=""):
            v = pq.get(k)
            return "n/a" if v is None else (fmt.format(v) + sfx)

        line1 = (
            f"startup  {g('video_startup_time_ms','{:.0f}',' ms')}"
            f"          rebuffers  {g('rebuffer_events')}"
            f" ({g('rebuffer_duration_ms','{:.0f}',' ms')} total)"
            f"          resolution  {g('video_resolution_p','{}','p')}"
            f"          fps  {g('frame_rate_fps','{:.1f}')}"
        )
        line2 = (
            f"dropped frames  {g('dropped_frame_pct','{:.2f}','%')}"
            f"          mean buffer  {g('mean_buffer_ahead_secs','{:.1f}',' s')}"
            f"          bitrate  {g('mean_bitrate_mbps','{:.2f}',' Mbps')}"
            f"          watched  {g('watched_seconds','{:.0f}',' s')}"
        )
        note = (
            "player_qoe_available = TRUE — metrics above are REAL player-side "
            "measurements, not network proxies. Null = a signal this player "
            "does not expose (see REALQOE.md), never a stand-in."
        )
    else:
        line1 = (
            f"delivered fraction  {100 * (at.delivered_fraction or 0):.1f}% of the run had active traffic"
            f"          idle time  {at.stall_seconds:g}s in {nstalls} span(s)"
        )
        line2 = (
            f"peak  {at.peak_throughput_mbps:.2f} Mbps ({pct(at.peak_throughput_mbps):.0f}% of cap)"
            f"          average  {at.avg_throughput_mbps:.2f} Mbps"
            f"          delivered  {at.total_mb:.1f} MB"
        )
        # Two different worlds land here: the player answered "it never played"
        # (evidence), or there was no player at all (a gap). Say which.
        said_no_playback = str(pq.get("status") or "") in (
            "no_data",
            "skipped",
        ) and str(pq.get("reason") or "").startswith("no_playback")
        if said_no_playback:
            note = (
                "player_qoe_available = FALSE — the player WAS sampled and its "
                "media clock never advanced past 0, so playback did not happen; "
                "resolution / rebuffers / dropped frames are therefore not "
                "measurable. The shaded band is network idle time, which is not "
                "evidence of a stall."
            )
        else:
            note = (
                "player_qoe_available = FALSE — no player stats for this run, so "
                "resolution / rebuffers / dropped frames are NOT measured. Metrics "
                "above are network proxies only and cannot confirm playback. The "
                "shaded band is network idle time, which is not evidence of a stall."
            )

    # The footnote is long enough to run off a 12in canvas at 7.5pt; wrap it so
    # the honest caveat stays fully readable instead of being clipped.
    note = textwrap.fill(note, width=170)
    note_lines = note.count("\n") + 1
    extra = 0.13 * (note_lines - 1)  # room for the wrapped lines

    y = lambda inches: inches / fig_h  # inches from the figure bottom
    fig.text(
        0.012,
        y(reserve_in - 0.62 + extra),
        line1 + "\n" + line2,
        fontsize=9,
        va="bottom",
        ha="left",
        color="#212121",
        linespacing=1.6,
    )
    verdict, why, vcolor = _panel_verdict(at, pq)
    # Verdict and rationale on separate lines: some labels are long enough that
    # a single line runs off the canvas and truncates the caveat mid-word.
    fig.text(
        0.012,
        y(0.38 + extra),
        f"classification:  {verdict.upper()}\n{textwrap.fill(why, width=118)}",
        fontsize=9.5,
        va="bottom",
        ha="left",
        color=vcolor,
        weight="bold",
        linespacing=1.45,
        bbox=dict(boxstyle="round,pad=0.35", fc="white", ec=vcolor, lw=1.3, alpha=0.95),
    )
    fig.text(
        0.012,
        y(0.10),
        note,
        fontsize=7.5,
        color="#616161",
        va="bottom",
        linespacing=1.5,
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, y(reserve_in), 1, 1))
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def generate_run_plots(
    attribution: CaptureAttribution,
    cfg: ExperimentConfig,
    run_dir: Path,
    player_qoe: Optional[dict[str, dict]] = None,
) -> dict[str, str]:
    """Every plot for one run — called automatically when a run finishes.

    Per app: `throughput_<app>[_<direction>].png` and `qoe_summary_<app>...png`.
    No combined-throughput plot is produced, by design.
    """
    plots: dict[str, str] = {}
    if _plt() is None:
        print("  ! matplotlib unavailable — skipping plots")
        return plots

    for app in cfg.apps:
        directions = app_plot_directions(app)
        both = len(directions) > 1
        for direction in directions:
            at = attribution.traffic(app, direction)
            if at is None or not at.times or at.avg_throughput_mbps is None:
                pq = (player_qoe or {}).get(app) or {}
                why = (
                    pq.get("reason")
                    or attribution.note
                    or "no traffic attributed to this app"
                )
                print(f"    · {app} ({direction}): no plot — {why}")
                continue
            sfx = f"_{direction}" if both else ""
            tp = plot_app_throughput(
                at, cfg, run_dir / f"throughput_{app}{sfx}.png", both_directions=both
            )
            if tp:
                plots[f"throughput_{app}{sfx}"] = str(tp)
            qs = plot_app_qoe_summary(
                at,
                cfg,
                run_dir / f"qoe_summary_{app}{sfx}.png",
                player_qoe=(player_qoe or {}).get(app),
                bin_s=attribution.bin_s,
            )
            if qs:
                plots[f"qoe_summary_{app}{sfx}"] = str(qs)
            print(
                f"    · {app} ({direction}): throughput + qoe_summary saved "
                f"[{at.classification}, avg {at.avg_throughput_mbps:.2f} Mbps]"
            )
    return plots


def app_verdict(network_verdict: str, player_qoe: Optional[dict[str, Any]]) -> str:
    """Final per-app verdict, with the player's answer taking precedence.

    The network side cannot see whether the video actually played: an app can
    pull megabytes and still never render a frame. When real player QoE exists it
    decides — a session whose playback never advanced is `failed_to_deliver`
    however much traffic the link carried.
    """
    pq = player_qoe or {}
    if pq.get("player_qoe_available"):
        if pq.get("status") == "ok" and pq.get("video_resolution_p"):
            return "served"
        return "failed_to_deliver"
    if pq.get("status") in ("no_data", "skipped") and str(
        pq.get("reason", "")
    ).startswith("no_playback"):
        return "failed_to_deliver"
    return network_verdict


def replot_run(run_dir: Path | str, bin_s: float = 1.0) -> dict[str, str]:
    """Regenerate the per-app plots for a run that is already on disk.

    Reads `record.json` for the config and re-splits `capture.pcap`. Useful for
    bringing older runs (or runs captured before this convention) up to the
    per-app standard without re-running the experiment.
    """
    run_dir = Path(run_dir)
    rec_path = run_dir / "record.json"
    if not rec_path.exists():
        print(f"  ! no record.json in {run_dir}")
        return {}
    rec = json.loads(rec_path.read_text())
    conf = dict(rec.get("config") or {})
    conf.pop("concurrency", None)
    valid = {f for f in ExperimentConfig.__dataclass_fields__}
    cfg = ExperimentConfig(**{k: v for k, v in conf.items() if k in valid})
    cfg.experiment_id = rec.get("experiment_id", "")
    cfg.slug = rec.get("slug", "") or cfg.make_slug()

    pcap = Path((rec.get("artifacts") or {}).get("pcap") or (run_dir / "capture.pcap"))
    if not pcap.exists():
        pcap = run_dir / "capture.pcap"
    if not pcap.exists():
        print(f"  ! no capture.pcap in {run_dir}")
        return {}

    at = attribute_capture(pcap, cfg.apps, cfg.bandwidth_mbps, bin_s=bin_s)
    # Carry the record's real player QoE into the plots, so a re-render shows the
    # player-side panels rather than falling back to the network proxy view.
    plots = generate_run_plots(
        at, cfg, run_dir, player_qoe=(rec.get("player_qoe") or {})
    )
    rec.setdefault("artifacts", {})["plots"] = plots
    # Preserve the player-aware verdict: recomputing from the pcap alone would
    # silently downgrade a `failed_to_deliver` app back to the network's "served".
    pq_all = rec.get("player_qoe") or {}
    rec["per_app_stats"] = {
        a: {
            **{
                d: (at.traffic(a, d) or AppTraffic(a, d)).as_record()
                for d in ("download", "upload")
            },
            "plotted_directions": app_plot_directions(a),
            "classification": app_verdict(
                (
                    at.traffic(a, app_plot_directions(a)[0])
                    or AppTraffic(a, "download")
                ).classification,
                pq_all.get(a),
            ),
            "attribution_method": at.method,
            "player_qoe_status": (pq_all.get(a) or {}).get("status"),
            "player_qoe_available": bool(
                (pq_all.get(a) or {}).get("player_qoe_available")
            ),
        }
        for a in cfg.apps
    }
    rec["capture_buckets_mb"] = at.bucket_mb
    rec["plot_combined_throughput"] = False
    rec["per_app_plots"] = True
    rec["player_qoe_available"] = bool(
        qoe_lib.any_real_qoe(rec.get("player_qoe") or {})
        if (qoe_lib and rec.get("player_qoe"))
        else rec.get("player_qoe_available", False)
    )
    rec_path.write_text(json.dumps(rec, indent=2, default=str))
    print(f"  ✓ replotted {run_dir} ({len(plots)} plots)")
    return plots


def plot_run_throughput(*_args: Any, **_kwargs: Any) -> None:
    """Removed on purpose — combined throughput plots are not produced.

    A single plot of everything on the link sums unrelated flows (and, in a
    browser run, tens of MB of Chrome's own component downloads), which inflates
    the apparent app throughput. Use `plot_app_throughput` / `generate_run_plots`.
    """
    raise NotImplementedError(
        "Combined throughput plots are disabled by research convention. "
        "Every run generates one plot per app via generate_run_plots(); use "
        "plot_app_throughput(app_traffic, cfg, out) for a single app."
    )


def plot_app_qoe_timeseries(
    app: str, cfg: ExperimentConfig, out: Path
) -> Optional[Path]:
    return None  # no player-QoE time series on the deployed engine


# ── metric lookup for sweep plots ────────────────────────────────────────────
# Per-app first: a sweep point's "throughput" is that app's own throughput in its
# plotted direction, not the link total (which also carries the other apps and
# the browser's own downloads).
_NET_METRICS = {
    "avg_throughput_mbps",
    "peak_throughput_mbps",
    "p95_throughput_mbps",
    "delivered_fraction",
    "stall_seconds",
    "total_mb",
    "mean_over_cap",
}


def _metric_of(
    rec: dict[str, Any], metric: str, app: Optional[str] = None
) -> Optional[float]:
    """One metric from one run record.

    `app` picks a specific app; with `app=None` the value is averaged over the
    run's apps. Falls back to link-level `network_stats` only for legacy records
    written before per-app splitting existed.
    """
    pas = rec.get("per_app_stats") or {}
    if pas and metric in _NET_METRICS:
        wanted = [app] if app else list(pas)
        vals: list[float] = []
        for a in wanted:
            st = pas.get(a)
            if not st:
                continue
            direction = (st.get("plotted_directions") or ["download"])[0]
            v = (st.get(direction) or {}).get(metric)
            if v is not None:
                vals.append(float(v))
        if vals:
            return sum(vals) / len(vals)
    if metric in _NET_METRICS:
        return (rec.get("network_stats") or {}).get(metric)
    # else look in per-app player qoe (empty on this deployment)
    for pa in (rec.get("per_app") or {}).values():
        v = (pa.get("qoe") or {}).get(metric)
        if v is not None:
            return v
    return None


def _sweep_line(records, xkey_fn, metric, xlabel, out: Path, app: Optional[str] = None):
    plt = _plt()
    if plt is None or not records:
        return None
    xs, ys = [], []
    for r in records:
        v = _metric_of(r, metric, app=app)
        if v is not None:
            xs.append(xkey_fn(r))
            ys.append(v)
    if not xs:
        print(f"  (no data for metric '{metric}')")
        return None
    xs, ys = zip(*sorted(zip(xs, ys)))
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(xs, ys, marker="o", color=app_color(app) if app else "#2196F3")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(metric.replace("_", " "))
    who = f" — {display_name(app)}" if app else ""
    ax.set_title(f"{metric.replace('_',' ')} vs {xlabel}{who}")
    ax.grid(True, alpha=0.3)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"Saved: {out}")
    return out


def plot_qoe_vs_bandwidth(
    records, out: Path, metric: str = "avg_throughput_mbps", app: Optional[str] = None
):
    return _sweep_line(
        records,
        lambda r: r["config"]["bandwidth_mbps"],
        metric,
        "bandwidth (Mbps)",
        out,
        app=app,
    )


def plot_qoe_vs_latency(
    records, out: Path, metric: str = "stall_seconds", app: Optional[str] = None
):
    return _sweep_line(
        records,
        lambda r: r["config"]["latency_ms"],
        metric,
        "latency (ms)",
        out,
        app=app,
    )


def plot_qoe_vs_concurrency(
    records, out: Path, metric: str = "avg_throughput_mbps", app: Optional[str] = None
):
    return _sweep_line(
        records,
        lambda r: len(r["config"]["apps"]),
        metric,
        "concurrency (#apps)",
        out,
        app=app,
    )


def plot_throughput_vs_bandwidth(records, out: Path, app: Optional[str] = None):
    return _sweep_line(
        records,
        lambda r: r["config"]["bandwidth_mbps"],
        "avg_throughput_mbps",
        "configured bandwidth (Mbps)",
        out,
        app=app,
    )


def plot_group_comparison(
    groups: dict[str, list[dict[str, Any]]],
    out: Path,
    metric: str = "avg_throughput_mbps",
    app: Optional[str] = None,
):
    """Bar chart comparing a metric across labelled groups (AQM, equity, ...).

    `app` restricts the comparison to one app's own traffic; with `app=None` the
    run's apps are averaged. Either way the value is per-app, never the link sum.
    """
    plt = _plt()
    if plt is None or not groups:
        return None
    labels, vals = [], []
    for label, recs in groups.items():
        ms = [
            _metric_of(r, metric, app=app)
            for r in recs
            if _metric_of(r, metric, app=app) is not None
        ]
        if ms:
            labels.append(label)
            vals.append(sum(ms) / len(ms))
    if not labels:
        print(f"  (no data for metric '{metric}')")
        return None
    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(labels, vals, color=plt.cm.tab10(range(len(labels))))
    for b, v in zip(bars, vals):
        ax.annotate(
            f"{v:.2f}",
            xy=(b.get_x() + b.get_width() / 2, v),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            fontsize=9,
        )
    ax.set_ylabel(metric.replace("_", " "))
    ax.set_title(
        f"{metric.replace('_',' ')} comparison"
        + (f" — {display_name(app)}" if app else "")
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"Saved: {out}")
    return out


def plot_aqm_comparison(
    records_by_aqm,
    out: Path,
    metric: str = "avg_throughput_mbps",
    app: Optional[str] = None,
):
    return plot_group_comparison(records_by_aqm, out, metric, app=app)


def plot_equity_comparison(
    well_served,
    redlined,
    out: Path,
    metric: str = "avg_throughput_mbps",
    app: Optional[str] = None,
):
    return plot_group_comparison(
        {"well-served": [well_served], "redlined": [redlined]}, out, metric, app=app
    )


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
def _alias_ip_for(index: int) -> str:
    """Alias IP handed to app #index when the substrate supports per-app marks."""
    return f"172.16.1.{4 + index}"


def substrate_setup_per_app_marks(apps: list[str]) -> Optional[dict[str, str]]:
    """Ask the substrate to give each app its own namespace alias IP.

    Returns {app: bind_ip} when the worker supports `POST /shape/per_app_marks`,
    else None. The deployed `main_local` build does not implement the endpoint —
    every browser app shares `ns1` and one source IP — so this returns None there
    and attribution falls back to remote-hostname matching. When a worker that
    does implement it is deployed, per-app splitting becomes exact with no other
    change needed here.
    """
    if len(apps) < 2:
        return None
    marks = [
        {"app": a, "mark": i + 1, "bind_ip": _alias_ip_for(i), "proxy_port": 8081 + i}
        for i, a in enumerate(apps)
    ]
    try:
        r = requests.post(
            f"{SUBSTRATE}/shape/per_app_marks",
            json={"app_marks": marks},
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        body = r.json()
    except (requests.RequestException, ValueError):
        return None
    out: dict[str, str] = {}
    for entry in body.get("setup", []):
        if entry.get("bind_ip") and entry.get("status") in ("started", "alias_only"):
            out[entry["app"]] = entry["bind_ip"]
    return out or None


def substrate_teardown_per_app_marks() -> None:
    try:
        requests.delete(f"{SUBSTRATE}/shape/per_app_marks", timeout=HTTP_TIMEOUT)
    except requests.RequestException:
        pass


def run_direct(cfg: ExperimentConfig) -> dict[str, Any]:
    """Run one experiment (solo or concurrent) on the substrate worker.

    Every run — no exceptions, including runs whose shaping check fails — leaves
    behind, in one folder: `capture.pcap` (the raw evidence), `record.json`, and
    one throughput plot + one QoE-summary plot **per app**.
    """
    cfg.experiment_id = f"exp-{uuid.uuid4().hex[:10]}"
    cfg.slug = cfg.make_slug()
    run_dir = _run_dir(cfg)
    apps = cfg.apps
    concurrent = len(apps) > 1

    print(f"▶ DIRECT run: {cfg.slug}")
    print(
        f"  cap={cfg.bandwidth_mbps}Mbps lat={cfg.latency_ms}ms loss={cfg.loss_pct}% "
        f"aqm={cfg.aqm} cca={cfg.cca} dur={cfg.duration_s}s apps={apps}"
    )

    print("  • shaping ...")
    shape = substrate_shape(cfg, verify=not concurrent)
    substrate_congestion(cfg)

    # Per-app namespace IPs make the pcap split exact. Unsupported on the
    # deployed worker → falls back to in-capture hostname attribution.
    local_ip_map = substrate_setup_per_app_marks(apps) if concurrent else None
    if local_ip_map:
        print(f"  • per-app alias IPs: {local_ip_map}")

    cap_duration = cfg.duration_s + CAPTURE_OVERHEAD_S + 15 * (len(apps) - 1)
    print(f"  • capture on {DOWNSTREAM_IFACE} (auto-stop {cap_duration}s) ...")
    cap = substrate_capture_start(cfg.slug, duration_s=cap_duration)
    capture_id = cap["capture_id"]

    run_results: dict[str, Any] = {}
    reg, _qlib = _bind_shared()
    browser_apps = [a for a in apps if reg and reg.get(a).has_player_qoe]
    shell_apps = [a for a in apps if a not in browser_apps]
    player_qoe: dict[str, Any] = {}

    # ── real player QoE: drive actual Chrome inside the shaped namespace ─────
    if COLLECT_ENABLED and browser_apps and reg is not None:
        print(f"  • real QoE collector for {browser_apps} ...")
        player_qoe = collect_player_qoe(cfg, run_dir)
        for a in browser_apps:
            q = player_qoe.get(a) or {}
            run_results[a] = {
                "result": [{"success": q.get("status") == "ok"}],
                "qoe_status": q.get("status"),
            }
        apps_for_substrate = shell_apps
    else:
        if browser_apps and not COLLECT_ENABLED:
            print(
                "  • collector disabled (PRAMANA_COLLECT=0) — substrate workflows only"
            )
        elif browser_apps:
            print(f"  ! {shared_repo_status()}")
        apps_for_substrate = apps

    apps = apps_for_substrate if apps_for_substrate else []
    if not apps:
        apps = []
    (
        print(
            f"  • running {len(apps)} substrate workflow(s) "
            f"({'concurrent' if len(apps) > 1 else 'solo'}) ..."
        )
        if apps
        else None
    )
    concurrent = len(apps) > 1
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
    elif apps:
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

    apps = cfg.apps  # collector/substrate split is done; report on all apps

    print("  • finalising capture + downloading pcap ...")
    substrate_capture_wait(capture_id, timeout_s=cap_duration + 60)
    local_pcap = run_dir / "capture.pcap"
    got = substrate_capture_download(capture_id, local_pcap)
    if got:
        # Only drop the worker-side copy once ours is safely on disk.
        substrate_capture_delete(capture_id)
        print(
            f"  • pcap saved → {local_pcap} ({local_pcap.stat().st_size / 1e6:.1f} MB)"
        )
    else:
        print(
            f"  ! PCAP DOWNLOAD FAILED — the worker-side capture {capture_id} is "
            f"being KEPT so the raw evidence is not lost. Retrieve it with: "
            f"curl -o {local_pcap} {SUBSTRATE}/capture/{capture_id}/pcap"
        )
    if local_ip_map:
        substrate_teardown_per_app_marks()

    # ── link-level stats (shaping fidelity) ──────────────────────────────────
    net = analyze_pcap_netstats(local_pcap, cfg.bandwidth_mbps) if got else NetStats()
    if net.avg_throughput_mbps is not None:
        verdict = "OK" if net.shaping_verified else "OVER CAP"
        print(
            f"  • shaping check: avg {net.avg_throughput_mbps:.2f} / cap "
            f"{cfg.bandwidth_mbps:g} Mbps ({net.shaping_error_pct:+.1f}%) → {verdict}"
        )
    else:
        print("  • shaping check: no packets captured")

    # ── per-app split (never a combined view) ────────────────────────────────
    print("  • splitting capture per app ...")
    attribution = attribute_capture(
        local_pcap, apps, cfg.bandwidth_mbps, local_ip_map=local_ip_map
    )
    if attribution.method != "none":
        print(
            f"    method={attribution.method}  local={attribution.local_ips}  "
            f"{100 * attribution.attributed_fraction:.1f}% of bytes attributed"
        )
        for bucket, mb in list(attribution.bucket_mb.items())[:6]:
            print(f"      {bucket:16s} {mb:9.2f} MB")

    per_app: dict[str, Any] = {}
    per_app_stats: dict[str, Any] = {}
    for a in apps:
        dirs = app_plot_directions(a)
        stats = {
            d: (attribution.traffic(a, d) or AppTraffic(a, d)).as_record()
            for d in ("download", "upload")
        }
        # The plotted direction is the one that defines the verdict: download for
        # video, and for calls the worse of the two directions.
        verdicts = [
            (attribution.traffic(a, d) or AppTraffic(a, d)).classification for d in dirs
        ]
        verdict = (
            "starved"
            if "starved" in verdicts
            else (
                "no_traffic" if all(v == "no_traffic" for v in verdicts) else "served"
            )
        )
        # A network verdict cannot see whether the video actually played. When
        # real player QoE exists it overrides: a session whose playback never
        # advanced is failed-to-deliver, however much traffic the link carried.
        pq_a = player_qoe.get(a) or {}
        verdict = app_verdict(verdict, pq_a)
        stats["plotted_directions"] = dirs
        stats["classification"] = verdict
        stats["player_qoe_status"] = pq_a.get("status")
        stats["attribution_method"] = attribution.method
        # Mirror the app's actual player payload. Hardcoding False here made
        # every record disagree with its own player_qoe block, so any consumer
        # reading this side concluded "no player data" for runs that had it.
        stats["player_qoe_available"] = bool(pq_a.get("player_qoe_available"))
        per_app_stats[a] = stats
        per_app[a] = {
            "type": APP_REGISTRY[a]["type"],
            "traffic_verdict": verdict,
            "player_qoe_available": bool(
                (player_qoe.get(a) or {}).get("player_qoe_available")
            ),
            "qoe": player_qoe.get(a) or {},
        }
        dl = attribution.traffic(a, "download")
        print(
            f"    · {a}: {verdict}"
            + (
                f" (dl avg {dl.avg_throughput_mbps:.2f} / peak {dl.peak_throughput_mbps:.2f} Mbps, "
                f"delivered {100 * (dl.delivered_fraction or 0):.0f}%)"
                if dl and dl.avg_throughput_mbps is not None
                else ""
            )
        )

    # ── plots, automatically, for every run ──────────────────────────────────
    print("  • generating per-app plots ...")
    try:
        plots = generate_run_plots(attribution, cfg, run_dir, player_qoe=player_qoe)
    except Exception as exc:  # a plotting bug must never lose the run's evidence
        plots = {}
        print(
            f"    ! plot generation failed ({type(exc).__name__}: {exc}); "
            f"the pcap, per-app stats and record are still saved — "
            f"re-render later with replot_run('{run_dir}')"
        )

    overall_ok = net.shaping_verified or net.avg_throughput_mbps is None
    for a in apps:
        dl = attribution.traffic(a, "download")
        telemetry_post_result(
            cfg.experiment_id,
            a,
            cfg,
            measured_throughput=(dl.avg_throughput_mbps if dl else None),
            qoe=_telemetry_qoe(player_qoe.get(a) or {}),
            pcap_path=str(local_pcap),
            status="success" if overall_ok else "shaping_failed",
        )

    record = {
        "experiment_id": cfg.experiment_id,
        "slug": cfg.slug,
        "mode": "direct",
        "timestamp": time.time(),
        "config": {
            **{
                k: v
                for k, v in asdict(cfg).items()
                if k not in ("experiment_id", "slug")
            },
            "concurrency": cfg.concurrency,
        },
        # ── dataset conventions ─────────────────────────────────────────────
        # Throughput is reported and plotted per app, never combined; upload and
        # download are kept apart (video shows download only).
        "plot_combined_throughput": False,
        "per_app_plots": True,
        # Self-identifying: whether YouTube's rendition was pinned for this run
        # or left to ABR. Forced and auto runs must never be pooled.
        "force_max_quality": bool(cfg.force_max_quality),
        # Real, player-side QoE per app (shared/qoe.py against the QoEMetrics
        # definition in shared/models/README.md). Null fields are signals the
        # player genuinely does not expose — never placeholders.
        "player_qoe": player_qoe,
        # Honest about what the current NetGent build can measure. When the
        # stats-logging build is deployed this flips to true and the QoE summary
        # plot gains a player-side panel automatically.
        "player_qoe_available": bool(
            qoe_lib and qoe_lib.any_real_qoe(player_qoe) if player_qoe else False
        ),
        "limitations": {
            "player_qoe_available": bool(
                qoe_lib and qoe_lib.any_real_qoe(player_qoe) if player_qoe else False
            ),
            "player_qoe_source": (
                "real Chrome (Xvfb + SeleniumBase/undetected-chromedriver) driven "
                "inside the shaped ns1 namespace; metrics derived by shared/qoe.py"
            ),
            "player_qoe_skipped": {
                a: (player_qoe.get(a) or {}).get("reason")
                for a in cfg.apps
                if (player_qoe.get(a) or {}).get("status") in ("skipped", "no_data")
            },
            "latency_scope": "link-wide (netem, not per-flow)",
            "qtrace_available": False,
            "per_app_throughput": True,
            "per_app_attribution_method": attribution.method,
            "per_app_attribution_note": (
                "local_alias_ip = exact split by each app's namespace IP; "
                "remote_hostname = split by remote endpoint identified from "
                "in-capture DNS answers + TLS SNI (the deployed worker runs all "
                "browser apps in one namespace, so there is only one local IP)"
            ),
            "attributed_byte_fraction": attribution.attributed_fraction,
        },
        "network_stats": asdict(net),
        "per_app_stats": per_app_stats,
        "capture_buckets_mb": attribution.bucket_mb,
        "capture_top_hosts_bytes": attribution.host_bytes,
        "shaping_applied": shape.get("bottleneck_state", shape),
        "per_app": per_app,
        "workflow_ok": all(
            (r.get("result") or [{}])[0].get("success")
            for r in run_results.values()
            if isinstance(r, dict)
        ),
        "artifacts": {
            "pcap": str(local_pcap) if got else "",
            "pcap_saved": bool(got),
            "plots": plots,
            "dir": str(run_dir),
        },
    }

    # Write the record (and the pcap and plots above) BEFORE any strict-shaping
    # abort: a run that failed verification is still raw evidence worth keeping.
    if (
        STRICT_SHAPING
        and net.avg_throughput_mbps is not None
        and not net.shaping_verified
    ):
        record["shaping_verification_failed"] = True
        _write_record(record, run_dir)
        print(f"  ✓ saved → {run_dir} (flagged: shaping verification failed)")
        raise RuntimeError(
            f"Shaping verification FAILED for {cfg.slug}: measured "
            f"{net.avg_throughput_mbps:.2f} Mbps > {cfg.bandwidth_mbps:g} Mbps "
            f"+{int(SHAPING_TOLERANCE * 100)}%. Not trusting this run. "
            f"Artifacts were still saved to {run_dir}."
        )

    _write_record(record, run_dir)
    print(f"  ✓ saved → {run_dir}")
    print(
        f"    pcap: {'capture.pcap' if got else 'MISSING'} | "
        f"plots: {len(plots)} | record.json"
    )
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
        body = requests.get(
            f"{ORCH}/orchestration/{orch_id}", timeout=HTTP_TIMEOUT
        ).json()
        status = body.get("status")
        if status != last:
            print(f"  {status} ... ({int(time.time() - started)}s)")
            last = status
        if status in _TERMINAL:
            break
        time.sleep(_POLL_EVERY)
    return body


def _orch_experiment_ids(orch_id: str) -> list[str]:
    body = requests.get(
        f"{ORCH}/orchestration/{orch_id}/results", timeout=HTTP_TIMEOUT
    ).json()
    return [
        r.get("experiment_id")
        for r in body.get("results", [])
        if r.get("experiment_id")
    ]


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


def _throughput_from_pcap_path(
    pcap_path: Optional[str], cap_mbps: Optional[float]
) -> Optional[float]:
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
                row.get("pcap_path"), row.get("configured_capacity")
            )
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
    return (
        f"{verb} {apps} on a {cfg.bandwidth_mbps:g} Mbps bottleneck with "
        f"{cfg.latency_ms:g} ms latency{loss} for {cfg.duration_s} seconds "
        f"using {cfg.cca} congestion control and a {cfg.aqm} queue."
    )


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


def run_sweep(
    base: ExperimentConfig, axis: str, values: Iterable[Any], mode: str = "direct"
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    values = list(values)
    print(f"═══ SWEEP over {axis}: {values} ═══")
    base_fields = {
        k: v for k, v in asdict(base).items() if k not in ("experiment_id", "slug")
    }
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
    params: dict[str, Any] = {
        "limit": limit,
        "sort_by": "created_at",
        "sort_order": "desc",
    }
    if application:
        params["application"] = application
    rows = telemetry_get_results(**params)
    if not rows:
        print("No experiments recorded in telemetry yet.")
        return None
    friendly = [
        {
            "when": (r.get("created_at") or "")[:19].replace("T", " "),
            "app": _app_name(r.get("experiment_id"), r.get("application") or "?"),
            "status": r.get("status"),
            "capacity (Mbps)": _num(r.get("configured_capacity")),
            "latency (ms)": _num(r.get("configured_latency")),
            "throughput (Mbps)": _num(r.get("measured_throughput")),
        }
        for r in rows
    ]
    print(f"{len(friendly)} most recent experiment(s) in telemetry:")
    if pd is not None:
        df = pd.DataFrame(friendly)
        display(df)
        return df
    for row in friendly:
        print("  ", row)
    return friendly


def show_player_qoe(record: dict[str, Any]) -> Any:
    """Readable per-app real-QoE table for one run (notebook display helper).

    Shows the QoEMetrics fields; `n/a` means the player genuinely does not expose
    that signal on this app (see REALQOE.md) — it is never a placeholder zero.
    """
    pq = record.get("player_qoe") or {}
    if not pq:
        print("No player QoE in this record.")
        return None

    def fmt(v: Any, nd: int = 2, sfx: str = "") -> str:
        if v is None:
            return "n/a"
        if isinstance(v, float):
            return f"{round(v, nd):g}{sfx}"
        return f"{v}{sfx}"

    rows = []
    for app, q in pq.items():
        if not q.get("player_qoe_available"):
            # Skipped, no data, or a shell app that has no player by definition.
            note = q.get("reason") or ""
            tr = q.get("transfer") or {}
            if tr:
                note = (
                    f"transfer only: {fmt(tr.get('bytes'))} bytes in "
                    f"{fmt(tr.get('seconds'))}s @ "
                    f"{fmt(tr.get('mean_throughput_mbps'))} Mbps"
                )
            rows.append({"app": app, "status": q.get("status"), "note": note[:80]})
            continue
        rows.append(
            {
                "app": app,
                "status": "ok",
                "resolution": fmt(q.get("video_resolution_p"), sfx="p"),
                "startup": fmt(q.get("video_startup_time_ms"), 0, " ms"),
                "rebuffers": fmt(q.get("rebuffer_events")),
                "rebuf_time": fmt(q.get("rebuffer_duration_ms"), 0, " ms"),
                "dropped": fmt(q.get("dropped_frame_pct"), 2, "%"),
                "fps": fmt(q.get("frame_rate_fps"), 1),
                "bitrate": fmt(q.get("mean_bitrate_mbps"), 2, " Mbps"),
                "buffer": fmt(q.get("mean_buffer_ahead_secs"), 1, " s"),
                "watched": fmt(q.get("watched_seconds"), 0, " s"),
            }
        )
    avail = record.get("player_qoe_available")
    print(f"Real player-side QoE — player_qoe_available = {avail}")
    print(
        f"  source: real Chrome driven inside the shaped ns1 namespace; "
        f"metrics per shared/models/README.md QoEMetrics\n"
    )
    if pd is not None:
        df = pd.DataFrame(rows)
        display(df)
        return df
    for r in rows:
        print("  ", r)
    return rows


def rebuild_dataset_index() -> int:
    """Rewrite dataset_index.jsonl from the per-run record.json files.

    The index is append-only at run time, so a correction applied to the records
    (e.g. recomputing QoE after a summarizer fix) does not reach it. Rebuilding
    keeps the aggregate dataset consistent with the per-run evidence rather than
    leaving superseded numbers in the table the analysis reads.
    """
    records = []
    for rec in sorted(RESULTS_ROOT.glob("*/record.json")):
        try:
            records.append((rec.stat().st_mtime, json.loads(rec.read_text())))
        except (json.JSONDecodeError, OSError):
            continue
    records.sort(key=lambda kv: kv[0])
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    with open(DATASET_INDEX, "w") as fh:
        for _mt, r in records:
            fh.write(json.dumps(r, default=str) + "\n")
    print(f"dataset_index.jsonl rebuilt from {len(records)} run record(s)")
    return len(records)


def load_dataset(per_app: bool = True) -> Any:
    """Flatten the run index into a table.

    With `per_app=True` (default) there is one row per app per run, carrying that
    app's own throughput — the combined link total is deliberately not a column,
    because it is not a per-app measurement.
    """
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
        base = {
            "slug": r.get("slug"),
            "apps": "+".join(cfg.get("apps", [])),
            "concurrency": cfg.get("concurrency"),
            "bandwidth_mbps": cfg.get("bandwidth_mbps"),
            "latency_ms": cfg.get("latency_ms"),
            "loss_pct": cfg.get("loss_pct"),
            "aqm": cfg.get("aqm"),
            "cca": cfg.get("cca"),
            "shaping_verified": ns.get("shaping_verified"),
            "workflow_ok": r.get("workflow_ok"),
            "player_qoe": r.get(
                "player_qoe_available",
                r.get("limitations", {}).get("player_qoe_available"),
            ),
        }
        pas = r.get("per_app_stats") or {}
        if per_app and pas:
            for app, st in pas.items():
                direction = (st.get("plotted_directions") or ["download"])[0]
                d = st.get(direction) or {}
                pq = (r.get("player_qoe") or {}).get(app) or {}
                flat.append(
                    {
                        **base,
                        "app": app,
                        "direction": direction,
                        # real player-side QoE (null = not measurable)
                        "qoe_status": pq.get("status"),
                        "resolution_p": pq.get("video_resolution_p"),
                        "startup_ms": pq.get("video_startup_time_ms"),
                        "rebuffers": pq.get("rebuffer_events"),
                        "rebuffer_ms": pq.get("rebuffer_duration_ms"),
                        "dropped_pct": pq.get("dropped_frame_pct"),
                        "fps": pq.get("frame_rate_fps"),
                        "bitrate_mbps": pq.get("mean_bitrate_mbps"),
                        "avg_throughput_mbps": d.get("avg_throughput_mbps"),
                        "peak_throughput_mbps": d.get("peak_throughput_mbps"),
                        "p95_throughput_mbps": d.get("p95_throughput_mbps"),
                        "total_mb": d.get("total_mb"),
                        "stall_seconds": d.get("stall_seconds"),
                        "delivered_fraction": d.get("delivered_fraction"),
                        "classification": st.get("classification"),
                        "attribution": st.get("attribution_method"),
                    }
                )
        else:
            # Pre-convention run with no per-app split: report the link totals and
            # say so, rather than passing them off as one app's throughput.
            flat.append(
                {
                    **base,
                    "app": base["apps"],
                    "direction": "link-total",
                    "avg_throughput_mbps": ns.get("avg_throughput_mbps"),
                    "peak_throughput_mbps": ns.get("peak_throughput_mbps"),
                    "p95_throughput_mbps": ns.get("p95_throughput_mbps"),
                    "total_mb": ns.get("total_mb"),
                    "stall_seconds": ns.get("stall_seconds"),
                    "delivered_fraction": ns.get("delivered_fraction"),
                    "classification": None,
                    "attribution": "none",
                }
            )
    return pd.DataFrame(flat)


__all__ = [
    "SUBSTRATE",
    "NETGENT",
    "TELEMETRY",
    "ORCH",
    "RESULTS_ROOT",
    "SHAPING_TOLERANCE",
    "STRICT_SHAPING",
    "CAPTURE_OVERHEAD_S",
    "APP_REGISTRY",
    "BROWSER_INFRA_DOMAINS",
    "DISPLAY_NAMES",
    "app_domains",
    "app_plot_directions",
    "app_color",
    "display_name",
    "ExperimentConfig",
    "run_experiment",
    "run_direct",
    "run_intent",
    "run_and_display",
    "run_sweep",
    # capture reading + per-app attribution
    "iter_capture",
    "attribute_capture",
    "classify_host",
    "AppTraffic",
    "CaptureAttribution",
    "NetStats",
    "analyze_pcap_netstats",
    "classify_traffic",
    "extract_app_qoe",
    "classify_qoe",
    # plots — per app, never combined
    "generate_run_plots",
    "plot_app_throughput",
    "plot_app_qoe_summary",
    "plot_app_qoe_timeseries",
    "plot_qoe_vs_bandwidth",
    "plot_qoe_vs_latency",
    "plot_qoe_vs_concurrency",
    "plot_throughput_vs_bandwidth",
    "plot_aqm_comparison",
    "plot_equity_comparison",
    "plot_group_comparison",
    "replot_run",
    "app_verdict",
    "rebuild_dataset_index",
    "show_player_qoe",
    "shared_repo_status",
    "collect_player_qoe",
    "run_qoe_collectors",
    "ns1_netns_path",
    "stack_health",
    "show_history",
    "load_dataset",
]
