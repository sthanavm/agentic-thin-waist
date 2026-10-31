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
# Slack between the capture window and the measured window. The capture timer
# starts when tcpdump does, but the app's join happens inside it, so the window
# must cover join + duration_s + teardown. Measured on a Meet run: the timer
# expired 5.4s before the collector's last sample, losing the tail of the call.
# Conferencing joins can need manual admission, so this is overridable per run.
CAPTURE_OVERHEAD_S = int(os.environ.get("PRAMANA_CAPTURE_OVERHEAD_S", "45"))

# How often every player signal is read. 1.0 s is the project default and what
# every committed result used; it is overridable only so a diagnostic can
# imitate a different sampler's rate. The B1 bar is defined against this value,
# so a run that changes it is not comparable with the release set.
SAMPLE_INTERVAL_S = float(os.environ.get("PRAMANA_SAMPLE_INTERVAL_S", "1.0"))


def _start_delays() -> dict:
    """Per-app hold before navigation, as {"vimeo": 60}.

    Read when the job is built, NOT at import: a driver that sets the variable
    after importing this module would otherwise be silently ignored, and the
    run would look normal while testing nothing.
    """
    try:
        return json.loads(os.environ.get("PRAMANA_START_DELAY_S") or "{}")
    except ValueError:
        return {}


# Skip the per-app capture split and plot rendering during the run, leaving them
# to `replot_run(run_dir)` afterwards. Measured at 465-574s per cell on a 2-vCPU
# host, that work is the dominant cost of a cell and needs no live meeting, so
# deferring it is what makes a 6-cell sweep fit one 40-minute window.
DEFER_ANALYSIS = os.environ.get("PRAMANA_DEFER_ANALYSIS") == "1"


# Throughput charts share ONE y-axis ceiling across every run, instead of each
# scaling to its own cap. A 3 Mbps run auto-scaled to ~3.75 and a 10 Mbps run to
# ~12.5 look identical on screen while showing a 3x difference in rate, so a
# reader comparing a bandwidth sweep side by side is misled by the axis. Fixed
# at 11 Mbps: above the 10 Mbps cap used here, with headroom for overshoot.
# Set PRAMANA_PLOT_YMAX=auto to restore per-run scaling.
_YMAX_ENV = os.environ.get("PRAMANA_PLOT_YMAX", "11")
PLOT_YMAX_MBPS: Optional[float] = (
    None if _YMAX_ENV.strip().lower() == "auto" else float(_YMAX_ENV)
)


# Companion ceiling for the upload charts (see plot_apps_throughput).
_YMAX_UP_ENV = os.environ.get("PRAMANA_PLOT_YMAX_UPLOAD", "2.5")
PLOT_YMAX_UPLOAD_MBPS: Optional[float] = (
    None if _YMAX_UP_ENV.strip().lower() == "auto" else float(_YMAX_UP_ENV)
)


def _throughput_ymax(cap: float, peak: Optional[float]) -> float:
    """Shared ceiling for every throughput axis, or per-run scaling if disabled.

    A run that somehow exceeds the fixed ceiling still gets a taller axis: the
    point is comparability, never hiding data above the frame.
    """
    measured = max(cap * 1.25, (peak or 0) * 1.15, 0.1)
    if PLOT_YMAX_MBPS is None:
        return measured
    return max(PLOT_YMAX_MBPS, (peak or 0) * 1.05)


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
    # Our own WebRTC peer (webrtc_peer_server.py). Reached by IP with no DNS
    # name, so `domains` is empty and the capture is attributed by connection
    # affinity instead - a solo run has exactly one app to claim it.
    "webrtc_peer": {
        "url": "",  # supplied per run via app_urls
        "runtime": "browser",
        "type": "call",
        "domains": [],
        "plot_directions": ["download", "upload"],
        "color": "#8E24AA",
    },
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
        # ~30 min clip: the old 62 s default ended mid-run and hid contention
        # effects behind end-of-video for any duration_s >= 60.
        "url": "https://player.vimeo.com/video/911411289?autoplay=1",
        "runtime": "browser",
        "type": "streaming",
        "domains": ["vimeocdn.com", "vimeo.com", "akamaized.net/vimeo"],
        "plot_directions": ["download"],
        "color": "#1AB7EA",
    },
    "twitch": {
        # Pinned channel: the bare homepage autoplays whatever is featured at
        # that moment, so runs silently compared different source content.
        # Live channels can go offline - verify before a measurement campaign.
        "url": "https://www.twitch.tv/relaxbeats",
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
        # Pinned title: the previous default (movies/100014) had been removed from
        # the catalogue and 302d to a 404 page, so no <video> was ever created.
        "url": "https://tubitv.com/movies/593796/another-cinderella-story",
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


# The hostname suffixes that carry an app's MEDIA, as opposed to its page,
# its thumbnails or its ads. Kept separate from `domains` because the
# pcap-vs-player check (R4) compares bytes counted here against bytes Chrome
# counted with the identical list in the collector's CDP ledger
# (`_CDP_HOSTS` in services/orchestration/scripts/selenium_video_qoe/
# collect.py). The two lists must not drift, so a test asserts they match.
MEDIA_HOST_SUFFIXES: dict[str, tuple[str, ...]] = {
    "youtube": ("googlevideo.com",),
    "vimeo": ("vimeocdn.com", "akamaized.net"),
    "twitch": ("ttvnw.net", "twitchcdn.net"),
    "tubi": ("tubitv.com", "adrise.tv", "cloudfront.net"),
}


def media_host_suffixes(app: str) -> tuple[str, ...]:
    """Hostname suffixes that carry `app`'s media segments."""
    return MEDIA_HOST_SUFFIXES.get(app, ())


def app_domains(app: str) -> list[str]:
    """Hostname suffixes that identify `app`'s traffic in a capture."""
    return list(APP_REGISTRY.get(app, {}).get("domains") or [])


def app_verdict_directions(app: str) -> list[str]:
    """Directions that DEFINE the app's served/starved verdict.

    Video apps → ``["download"]`` only. Upload for a video app is ACKs, which
    never reach 30% of the cap, so letting it vote would mark every healthy
    video run "starved". Conferencing apps → both, since a call is only as good
    as its worse direction.
    """
    dirs = APP_REGISTRY.get(app, {}).get("verdict_directions") or APP_REGISTRY.get(
        app, {}
    ).get("plot_directions")
    if dirs:
        return list(dirs)
    return (
        ["download", "upload"]
        if APP_REGISTRY.get(app, {}).get("type") == "call"
        else ["download"]
    )


def app_plot_directions(app: str) -> list[str]:
    """Directions to PLOT for `app` — always both, in separate files.

    Download and upload are never drawn on one axis (summing them inflates a
    10 Mbps stream to 11-12 Mbps) and never share a file with another app.
    Plotting upload is presentational only: `app_verdict_directions` still
    decides served/starved, so adding the upload plot cannot move a verdict.
    """
    return ["download", "upload"]


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
    # {app: {"join_url":..., "y4m":..., "name":..., "join_deadline_s":...}}
    # A bare string is still accepted and read as join_url.
    peers: Optional[dict[str, Any]] = None
    # How long to wait for a bot to confirm it is publishing. Admission is
    # manual on some platforms, so this is generous by default.
    peer_wait_s: int = 900
    # Opt-in: pin YouTube's rendition (setPlaybackQualityRange) instead of
    # leaving ABR on auto. Default False — every existing run is unaffected.
    # Forced runs get a `_forcedq` slug so they never mix with the auto runs.
    force_max_quality: bool = False
    force_quality_level: str = "hd2160"
    # Opt-in per-app viewport cap, as {app: "WxH"}. An adaptive player picks
    # its rendition to fit the element it draws into, so a smaller viewport
    # bounds the TOP of the ladder while leaving ABR free to adapt below it.
    # This is the honest way to keep a player off a rendition the host cannot
    # decode: Vimeo's `quality=720p` parameter FORCES one rendition and turns
    # ABR off entirely (measured: 960x720 for 116/116 samples, zero switches),
    # which would delete the adaptive behaviour the experiment exists to
    # measure. Capped runs get their own slug so they never mix with uncapped.
    app_window: Optional[dict[str, str]] = None
    # Opt-in per-app codec block, as {app: ["av01", ...]}. Hides a codec family
    # from the page so an adaptive player never selects it. This is the only
    # lever measured to bound a Vimeo ladder without disabling ABR: the
    # `quality` parameter forces one rendition (ABR off) and a smaller viewport
    # does not constrain Vimeo's choice at all. Blocked runs get their own slug.
    app_block_codecs: Optional[dict[str, list[str]]] = None
    # Let a vendor's own instrumentation (YouTube stats-for-nerds) flow into the
    # generic sample keys. Default False so the generic channel stays
    # independent of the reference it is validated against; the vendor values
    # are always recorded under `sfn_*` regardless.
    merge_vendor_stats: bool = False
    # Turn the uniform streaming probe off for this run. Used to measure the
    # probe's own cost: the same cell with hooks on and off, on a 2-vCPU host.
    disable_stream_probe: bool = False

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
        if self.app_window:
            # Same reasoning as _forcedq: a viewport-capped ladder is not
            # comparable with a free one, so the slug says so out loud.
            caps = "-".join(f"{a}{self.app_window[a]}" for a in sorted(self.app_window))
            slug += f"_vcap{caps}"
        if self.app_block_codecs:
            # A ladder measured without a codec family is not comparable with
            # one measured with it, so the slug carries the difference.
            marks = "-".join(
                f"{a}{'+'.join(sorted(self.app_block_codecs[a]))}"
                for a in sorted(self.app_block_codecs)
                if self.app_block_codecs[a]
            )
            if marks:
                slug += f"_noc{marks}"
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


def substrate_capture_start_on(
    interface: str, filename: str, duration_s: int, capture_filter: str = ""
) -> dict[str, Any]:
    """Start a capture on any interface, not just the shaped bottleneck.

    `substrate_capture_start` is deliberately fixed to the bottleneck, because
    that is where an app's own measurement must be taken. A probe needs the
    upstream leg as well, to prove it crossed both.
    """
    payload = {
        "interface": interface,
        "capture_filter": capture_filter,
        "filename": filename,
        "duration_seconds": duration_s,
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
                "sample_interval_seconds": SAMPLE_INTERVAL_S,
                "start_delay_s": float(_start_delays().get(app, 0) or 0),
                # needs_peer apps may require a human to admit the guest,
                # so they get the same generous budget as the bot peer.
                # Zoom is an HTML5-kind app that still needs a guest-join
                # click-through, so the flow is named explicitly rather than
                # inferred from `kind`.
                "join_flow": "zoom" if app == "zoom" else None,
                # Which Chrome to drive, straight from the registry.
                "driver_mode": getattr(spec, "driver_mode", "uc"),
                "join_timeout_seconds": (
                    int(cfg.peer_wait_s) if spec.needs_peer else 180
                ),
                "barrier_timeout_seconds": max(360, cfg.duration_s + 240),
                # Collector applies this to YouTube only; harmless for other apps.
                "force_max_quality": bool(cfg.force_max_quality),
                "force_quality_level": cfg.force_quality_level,
                # Viewport cap for this app, if one was configured.
                "window_size": (cfg.app_window or {}).get(app),
                # Conferencing apps must be measuring the REMOTE peer's main
                # tile, never the collector's own self-view, and the collector
                # cannot tell the difference from size alone. Gate it early so a
                # misaimed cell dies in seconds instead of consuming 180s of a
                # 40-minute meeting window.
                "require_remote_tile": bool(spec.needs_peer),
                # Codec families to hide from this app's page, if any.
                "block_codecs": list((cfg.app_block_codecs or {}).get(app) or []),
                # Off for validation runs: a vendor's own stats are the reference
                # the generic channel is compared against, so merging them into
                # the generic fields would make that comparison circular.
                "merge_vendor_stats": bool(cfg.merge_vendor_stats),
                # A/B control for measuring the probe's own overhead.
                "disable_stream_probe": bool(cfg.disable_stream_probe),
            }
        )
        display += 1
    return jobs, skipped


def _peer_spec(cfg: "ExperimentConfig", app: str) -> Optional[dict[str, Any]]:
    """Normalise cfg.peers[app] into a dict, accepting the legacy string form."""
    raw = (cfg.peers or {}).get(app)
    if not raw:
        return None
    if isinstance(raw, str):  # older callers passed just a room URL
        return {"join_url": raw}
    return dict(raw)


# ── persistent peers ────────────────────────────────────────────────────────
#
# A per-run bot has to be launched and, on Meet, manually admitted for every
# single experiment - which is where 3 of 4 Meet runs were lost. A persistent
# peer is admitted once and then stays in the room across runs, so a later run
# simply joins a meeting that already has video in it.
#
# Naming is the whole contract: a container called `pramana-peer-<app>` is
# treated as already-present media. start_bot_peers adopts it instead of
# launching, and stop_bot_peers leaves it alone.

PERSISTENT_PEER_PREFIX = "pramana-peer-"


def persistent_peer_name(app: str) -> str:
    return PERSISTENT_PEER_PREFIX + app


def find_persistent_peer(app: str) -> Optional[str]:
    """Container name of a running persistent peer for `app`, else None."""
    name = persistent_peer_name(app)
    rc, out = _run_cmd(
        [
            DOCKER_BIN,
            "ps",
            "--filter",
            "name=^" + name + "$",
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
        ],
        timeout=60,
    )
    if rc != 0 or name not in (out or ""):
        return None
    # Running is not the same as publishing. A peer whose meeting ended keeps
    # its container up and its last status line forever - measured here, one
    # reported "publishing" for an hour with bytes_sent stuck at 0, so a run
    # adopted a dead peer and failed with a confusing "never produced an
    # advancing inbound stream". Refuse anything the bot itself calls stalled.
    f = Path.home() / "pramana-assets" / ("peer-" + app) / (app + "_status.json")
    if f.is_file():
        try:
            st = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            st = {}
        if st.get("state") == "stalled":
            print(
                "    ! persistent peer '"
                + name
                + "' is STALLED (no outbound bytes) - not adopting it"
            )
            return None
    return name


def start_bot_peers(
    cfg: "ExperimentConfig", run_dir: Path
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Launch a publishing bot for every needs_peer app that has a peer configured.

    The bot runs on ordinary Docker networking, deliberately NOT inside ns1: its
    uplink must not share the shaped bottleneck with the app under test, or the
    run measures a two-endpoint call instead of one downlink.

    Returns (running, failures). Waiting for the join is separate so the caller
    can prompt an operator while it blocks.
    """
    reg, _ = _bind_shared()
    running: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    bots_dir = run_dir / "bots"
    bots_dir.mkdir(parents=True, exist_ok=True)
    for app in cfg.apps:
        spec = reg.get(app) if reg else None
        if not spec or not spec.needs_peer:
            continue
        peer = _peer_spec(cfg, app)
        if not peer:
            continue
        existing = find_persistent_peer(app)
        if existing:
            # Already in the room and publishing: adopt it, and mark it so the
            # teardown below does not evict media that later runs still need.
            print(
                "    - adopting persistent peer '"
                + existing
                + "' for "
                + app
                + " (no launch, no admission needed)"
            )
            running.append(
                {
                    "app": app,
                    "container": existing,
                    "name": app,
                    "room": (peer.get("join_url") or ""),
                    "persistent": True,
                }
            )
            continue
        room = peer.get("join_url") or ""
        # Same normalisation the app under test gets: a Zoom /j/ invite must
        # become the /wc/ web-client form or the bot lands on the launcher page,
        # whose "Join from browser" click is silently swallowed.
        if room and reg is not None and hasattr(reg, "normalize_join_url"):
            room = reg.normalize_join_url(app, room)
        if not room and reg is not None:
            room = reg.url_for(app, cfg.app_urls)
        if not room:
            failures[app] = "peer configured but no join_url and no app URL"
            continue
        bot_name = peer.get("name") or ("pramana-bot-" + app)
        container = "pramana-bot-{}-{}".format(
            app, cfg.experiment_id or uuid.uuid4().hex[:6]
        )
        status_path = bots_dir / (app + "_status.json")
        args = [
            DOCKER_BIN,
            "run",
            "-d",
            "--rm",
            "--name",
            container,
            "--privileged",
            "--shm-size=2g",
            "--dns",
            "8.8.8.8",
            "--env",
            "BOT_ROOM_URL=" + room,
            "--env",
            "BOT_NAME=" + bot_name,
            "--env",
            "BOT_APP=" + app,
            "--env",
            "BOT_STATUS=/out/" + app + "_status.json",
            "--env",
            "BOT_HOLD_SECONDS=" + str(int(cfg.duration_s) + 600),
            "--env",
            "BOT_JOIN_DEADLINE=" + str(int(peer.get("join_deadline_s", 900))),
            "--volume",
            str(bots_dir) + ":/out",
        ]
        if peer.get("y4m"):
            clip = Path(str(peer["y4m"])).expanduser().resolve()
            args += [
                "--volume",
                str(clip) + ":/tmp/bot_clip.y4m:ro",
                "--env",
                "BOT_CLIP=/tmp/bot_clip.y4m",
            ]
        args += ["--entrypoint", "python3", COLLECTOR_IMAGE, "bot_peer.py"]
        rc, out = _run_cmd(args, timeout=120)
        if rc != 0:
            failures[app] = "could not start bot container: " + out.strip()[:160]
            print("    ! " + app + ": " + failures[app])
            continue
        running.append(
            {"app": app, "container": container, "status": status_path, "room": room}
        )
        print("    - bot peer for '" + app + "' started (unshaped) -> " + container)
    return running, failures


def wait_for_bot_peers(
    bots: list[dict[str, Any]], timeout_s: float = 900.0
) -> dict[str, str]:
    """Block until every bot reports it is publishing; return {app: reason} failures.

    Admission can be manual, so this prints what the operator must do and keeps
    waiting rather than failing fast.
    """
    if not bots:
        return {}
    # A persistent peer was admitted and verified when it was launched, and it
    # writes its status into its own output directory, not this run's - polling
    # for it here would wait out the full timeout on a peer that is already fine.
    for b in bots:
        if b.get("persistent"):
            print(
                "    - peer '" + b["container"] + "' is persistent and already "
                "publishing; not waiting for admission"
            )
    pending = {b["app"]: b for b in bots if not b.get("persistent")}
    if not pending:
        return {}
    failures: dict[str, str] = {}
    deadline = time.time() + timeout_s
    prompted = False
    last = 0.0
    while pending and time.time() < deadline:
        for app, b in list(pending.items()):
            state = "starting"
            info: dict[str, Any] = {}
            try:
                info = json.loads(Path(b["status"]).read_text())
                state = info.get("state", "starting")
            except (OSError, ValueError):
                pass
            if state == "joined":
                print(
                    "    - bot peer '"
                    + app
                    + "' is publishing "
                    + str(info.get("publishing"))
                    + " - proceeding"
                )
                pending.pop(app)
            elif state == "failed":
                failures[app] = str(info.get("reason", "bot failed"))
                print("    ! bot peer '" + app + "' failed: " + failures[app])
                pending.pop(app)
            elif state == "knocking" and not prompted:
                prompted = True
                print("")
                print("=" * 66)
                print("  ACTION NEEDED: the bot peer is waiting to be admitted.")
                for x in pending.values():
                    print("    admit '" + x["app"] + "' in: " + x["room"])
                print("  The run continues automatically once it is let in.")
                print("=" * 66)
                print("")
        if pending and time.time() - last > 30:
            last = time.time()
            print(
                "    - still waiting for bot peer(s): "
                + str(sorted(pending))
                + " ("
                + str(int(deadline - time.time()))
                + "s left)"
            )
        if pending:
            time.sleep(3)
    for app in pending:
        failures[app] = "bot peer never confirmed publishing within {:.0f}s".format(
            timeout_s
        )
        print("    ! " + failures[app])
    return failures


def stop_bot_peers(bots: list[dict[str, Any]]) -> None:
    """Always called, including on failure: never leave a bot sitting in a room."""
    for b in bots:
        if b.get("persistent"):
            print(
                "    - persistent peer '" + b["container"] + "' left running "
                "(serves later runs too)"
            )
            continue
        rc, _ = _run_cmd([DOCKER_BIN, "stop", "-t", "5", b["container"]], timeout=60)
        note = "" if rc == 0 else " (stop non-zero; may already be gone)"
        print("    - bot peer '" + b["app"] + "' stopped" + note)


def run_qoe_collectors(
    cfg: ExperimentConfig,
    run_dir: Path,
    bots: Optional[list[dict[str, Any]]] = None,
    bot_failures: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
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

    # ── bot peers ────────────────────────────────────────────────────────────
    # needs_peer apps have no inbound media unless somebody publishes. Start the
    # bot BEFORE the app under test so there is already a stream to receive, and
    # outside ns1 so its uplink is not throttled by the cap being measured.
    if bots is None:  # standalone call: prepare them here instead
        bots, bot_failures = start_bot_peers(cfg, run_dir)
        if bots:
            bot_failures.update(
                wait_for_bot_peers(bots, timeout_s=float(cfg.peer_wait_s or 900))
            )
    bot_failures = dict(bot_failures or {})
    for app, why in bot_failures.items():
        skipped[app] = "peer bot unavailable: " + why
        jobs = [j for j in jobs if j["app"] != app]
    result["jobs"] = jobs
    result["bot_peers"] = [
        {"app": b["app"], "container": b["container"], "room": b["room"]} for b in bots
    ]
    if not jobs:
        stop_bot_peers(bots)
        for app, why in skipped.items():
            print("    - " + app + ": SKIPPED - " + why)
        return result

    qoe_dir = run_dir / "qoe"
    qoe_dir.mkdir(parents=True, exist_ok=True)
    # The container budget must cover the in-browser join wait as well, or a
    # needs_peer app that is still waiting to be admitted gets SIGKILLed by
    # the outer timeout and the run reports "no packets captured".
    join_budget = int(cfg.peer_wait_s) if bots else 0
    budget = int(cfg.duration_s + 420 + join_budget)
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
    # The alternative in-page sampler is selected by the environment, and the
    # collector reads that INSIDE the container, so the flag has to cross the
    # boundary. Without this the run looks normal and simply produces no
    # in-page log, which is indistinguishable from the sampler failing.
    # Limit OUR collector container only. Never the shared worker, never the
    # host. Set as a float count of cores, e.g. "4" or "2"; unset means no
    # limit. Checked afterwards against docker inspect (band C1), because a
    # limit that silently failed to apply would look like evidence about CPU.
    _cpus = os.environ.get("PRAMANA_COLLECTOR_CPUS")
    if _cpus:
        args += ["--cpus", str(float(_cpus))]
        print(f"    · collector limited to {float(_cpus):g} CPU(s)")

    for _k in (
        "PRAMANA_INPAGE_LOG",
        "PRAMANA_INPAGE_DEADLINE_S",
        "PRAMANA_MINIMAL_SAMPLER",
        "PRAMANA_SAMPLE_INTERVAL_S",
        "PRAMANA_TRACE",
        "PRAMANA_TRACE_INTERVAL_US",
    ):
        _v = os.environ.get(_k)
        if _v is not None:
            args += ["--env", f"{_k}={_v}"]
    # Dev override: run a host copy of collect.py inside the published image.
    # Lets a collector change be exercised without rebuilding a ~2GB image —
    # useful on a host that has no room to hold two copies of it at once.
    collector_src = os.environ.get("PRAMANA_COLLECTOR_SRC")
    if collector_src:
        src = Path(collector_src).expanduser().resolve()
        args += ["--volume", f"{src}:/app/collect.py:ro"]
        print(f"    · collector source overridden from {src}")
        # collect.py imports siblings (cdp_bytes), and mounting only collect.py
        # leaves those unimportable inside the image -- which fails the run at
        # import time, with the override silently to blame. Mount each sibling
        # the collector actually imports, so a source override behaves like a
        # rebuilt image.
        for sibling in ("cdp_bytes.py", "inpage_log.py"):
            sp = src.parent / sibling
            if sp.exists():
                args += ["--volume", f"{sp}:/app/{sibling}:ro"]
                print(f"    · mounted sibling {sibling}")
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
    try:
        rc, log = _run_cmd(args, timeout=budget)
    finally:
        # Same cleanup path as the capture: a failed run must not leave a bot
        # sitting in the room for the next one to trip over.
        stop_bot_peers(bots)
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
    cfg: ExperimentConfig,
    run_dir: Path,
    transfers: Optional[dict[str, dict]] = None,
    bots: Optional[list[dict[str, Any]]] = None,
    bot_failures: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Drive the apps, then reduce their samples with shared.qoe (the definitions)."""
    reg, qoelib = _bind_shared()
    if reg is None or qoelib is None:
        print(f"    ! {shared_repo_status()}")
        return {}
    run = run_qoe_collectors(cfg, run_dir, bots=bots, bot_failures=bot_failures)
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
        # Provenance of the measurement itself: which <video> was sampled, how
        # big it was rendered, and whether it was the remote peer or this
        # browser's own camera. Without it, "the peer was measured" is an
        # assumption - and it was the wrong assumption for a whole sweep, where
        # every sample read a 207x117 local self-view.
        # run["stats"][app] may be a JSONL PATH rather than a list - summarize()
        # accepts either, so load_samples() is what normalises it. Passing the
        # raw value straight in made the reducer iterate a string's characters,
        # find no dicts, and report "collector predates element census" for runs
        # whose samples all carried the census.
        per_app[app]["measured_element"] = _measured_element_summary(
            qoelib.load_samples(run["stats"].get(app, []))
        )
    return per_app


# Track-label markers for a LOCAL capture device. Chrome's synthetic camera
# reports "fake_device_N". The label is the only local marker that survives the
# page cloning the track. A plain substring test rather than a regex, because
# `re` is imported further down this module than this constant.
_LOCAL_LABEL_HINTS = ("fake_device",)


def _label_is_local(label: str) -> bool:
    low = (label or "").lower()
    return any(h in low for h in _LOCAL_LABEL_HINTS)


def _measured_element_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce the per-sample element census to one auditable block."""
    seen: dict[str, int] = {}
    origins: dict[str, int] = {}
    areas: list[int] = []
    local_samples = 0
    counted = 0
    labels: dict[str, int] = {}
    for s in samples:
        st = s.get("stats") if isinstance(s, dict) else None
        if not isinstance(st, dict):
            st = s if isinstance(s, dict) else None
        if not isinstance(st, dict):
            continue
        me = st.get("measured_element")
        if not isinstance(me, dict):
            continue
        counted += 1
        key = f"{me.get('stream')}@{me.get('box')}"
        seen[key] = seen.get(key, 0) + 1
        origins[str(me.get("origin"))] = origins.get(str(me.get("origin")), 0) + 1
        if isinstance(me.get("box_area"), (int, float)):
            areas.append(int(me["box_area"]))
        lab = str(me.get("label") or "")
        if lab:
            labels[lab] = labels.get(lab, 0) + 1
        # Count a sample as local on the track label as well as the id flag.
        # Zoom re-wraps the local camera before rendering its self-view, so the
        # clone's id is absent from the captured set and `measured_is_local`
        # reads False for what is demonstrably the collector's own camera - it
        # carries the fake device's label. Without the label test this reported
        # "zero local samples" for a sweep that measured the self-view in 24.
        if st.get("measured_is_local") is True or _label_is_local(lab):
            local_samples += 1
    if not counted:
        # Pre-census runs: say so rather than implying the element is unknown
        # because nothing was measured.
        return {"available": False, "reason": "collector predates element census"}
    dominant = max(seen.items(), key=lambda kv: kv[1])
    return {
        "available": True,
        "samples_with_census": counted,
        "dominant": dominant[0],
        "dominant_sample_share": round(dominant[1] / counted, 4),
        "origins": origins,
        "local_samples": local_samples,
        "measured_local_any": local_samples > 0,
        "box_area_min": min(areas) if areas else None,
        "box_area_max": max(areas) if areas else None,
        "distinct_elements": seen,
        "track_labels": labels,
    }


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
import hashlib
import hmac
import re
import socket
import shutil
import sqlite3
import subprocess
import struct
import tempfile
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
# The DOUBLE-r form on the same GGC edges is Chrome's component updater, not
# YouTube, so it must not fall through to an app bucket. Measured on both
# concurrent release runs: `rr5---sn-jxopj-n5oe.gvt1.com` delivered 973 kB in a
# 4-5 s burst at t=1..5 s, before the page had loaded, redirected from
# redirector.gvt1.com -- while YouTube's own media ran 14.3 MB from t=16..120 s
# on a sibling IP in the same /24. Treating the sn- form as YouTube because the
# names look alike would have added ~1 MB of browser traffic to every run.
_GVT_GGC_RE = re.compile(r"(^|\.)r{2,}[0-9]+-+sn-[a-z0-9-]+\.gvt1\.com$")


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


def _sni_from_client_hello(b: bytes, o: int = 0) -> Optional[str]:
    """SNI from a TLS ClientHello handshake message starting at ``b[o]``.

    Returns None unless the *whole* handshake message is present. A partial
    buffer would otherwise yield a silently truncated hostname, and because
    callers record names with `setdefault`, that truncated name would also
    block the correct one from ever being stored for the same IP.
    """
    try:
        if len(b) < o + 40 or b[o] != 0x01:
            return None
        msg_len = int.from_bytes(b[o + 1 : o + 4], "big")
        if len(b) < o + 4 + msg_len:
            return None
        o += 4 + 2 + 32  # hs hdr + version + random
        o += 1 + b[o]  # session id
        o += 2 + struct.unpack_from(">H", b, o)[0]  # cipher suites
        o += 1 + b[o]  # compression methods
        ext_len = struct.unpack_from(">H", b, o)[0]
        o += 2
        end = min(o + ext_len, len(b))
        while o + 4 <= end:
            etype, elen = struct.unpack_from(">HH", b, o)
            o += 4
            if etype == 0:  # server_name
                if o + 5 + struct.unpack_from(">H", b, o + 3)[0] > len(b):
                    return None
                nlen = struct.unpack_from(">H", b, o + 3)[0]
                host = b[o + 5 : o + 5 + nlen].decode("ascii", "replace")
                return host or None
            o += elen
    except (struct.error, IndexError):
        return None
    return None


def _harvest_sni(l4: bytes, dst: str, out: dict[str, str]) -> None:
    """Record dst-IP→SNI from a TLS ClientHello (TCP payload)."""
    if len(l4) < 20:
        return
    doff = (l4[12] >> 4) * 4
    b = l4[doff:]
    # TLS record: handshake(0x16), then ClientHello(0x01)
    if len(b) < 45 or b[0] != 0x16 or b[5] != 0x01:
        return
    host = _sni_from_client_hello(b, 5)
    if host:
        out.setdefault(dst, host)


# ── QUIC Initial → SNI ───────────────────────────────────────────────────────
#
# Why this is here at all. A media flow is named in a capture only by the SNI
# its TLS ClientHello sent in the clear, and YouTube's media rides QUIC. On a
# QUIC-only connection no TCP ClientHello is ever sent, so the endpoint stays
# unnamed and its bytes fall into `other`.
#
# Measured on the two concurrent release runs, which hit the SAME YouTube GGC
# server (198.189.66.13, rr2---sn-jxopj-n5oe.googlevideo.com) and disagreed
# only by luck:
#
#   6 Mbps run  Chrome also opened a throwaway TCP/443 connection to it
#               (7,973 bytes, 2 SYNs) whose ClientHello leaked the SNI, so
#               14.3 MB of QUIC media was attributed correctly.
#   3 Mbps run  zero TCP packets to that IP — Chrome went straight to QUIC —
#               and no DNS answer for the name was in the capture either
#               (warm resolver cache), so the same 14.3 MB landed in `other`.
#
# QUIC Initial packets are protected with keys derived from a FIXED, published
# salt and the client's own Destination Connection ID (RFC 9001 §5.2), so any
# observer can read them; this is exactly what a protocol analyser does. That
# makes the name available from the capture alone, deterministically, instead
# of depending on whether a TCP race happened to leave a ClientHello behind.
#
# AES is not in the standard library and adding a dependency to the release
# path was not worth it, so AES-128 is implemented here. It runs only on client
# Initial packets (235 of 60,673 in the 3 Mbps capture).

_QUIC_V1_INITIAL_SALT = bytes.fromhex("38762cf7f55934b34d179ae6a4c80cadccbb7f0a")


def _aes_xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x1B) & 0xFF if a & 0x100 else a


def _aes_build_sbox() -> bytes:
    exp, log, x = [0] * 512, [0] * 256, 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x ^= _aes_xtime(x)
    for i in range(255, 512):
        exp[i] = exp[i - 255]

    def rotl8(v: int, n: int) -> int:
        return ((v << n) | (v >> (8 - n))) & 0xFF

    box = bytearray(256)
    for a in range(256):
        inv = 0 if a == 0 else exp[255 - log[a]]
        box[a] = (
            inv ^ rotl8(inv, 1) ^ rotl8(inv, 2) ^ rotl8(inv, 3) ^ rotl8(inv, 4) ^ 0x63
        )
    return bytes(box)


_AES_SBOX = _aes_build_sbox()


def _aes_expand_key(key: bytes) -> list[list[int]]:
    rk = [list(key[i : i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        t = list(rk[i - 1])
        if i % 4 == 0:
            t = [_AES_SBOX[b] for b in (t[1:] + t[:1])]
            t[0] ^= rcon
            rcon = _aes_xtime(rcon)
        rk.append([rk[i - 4][j] ^ t[j] for j in range(4)])
    return rk


def _aes_encrypt_block(rk: list[list[int]], block: bytes) -> bytes:
    s = [[block[r + 4 * c] for c in range(4)] for r in range(4)]

    def add_round_key(rnd: int) -> None:
        for c in range(4):
            for r in range(4):
                s[r][c] ^= rk[4 * rnd + c][r]

    add_round_key(0)
    for rnd in range(1, 11):
        for r in range(4):
            for c in range(4):
                s[r][c] = _AES_SBOX[s[r][c]]
        for r in range(1, 4):
            s[r] = s[r][r:] + s[r][:r]
        if rnd != 10:
            for c in range(4):
                a = [s[r][c] for r in range(4)]
                s[0][c] = _aes_xtime(a[0]) ^ _aes_xtime(a[1]) ^ a[1] ^ a[2] ^ a[3]
                s[1][c] = a[0] ^ _aes_xtime(a[1]) ^ _aes_xtime(a[2]) ^ a[2] ^ a[3]
                s[2][c] = a[0] ^ a[1] ^ _aes_xtime(a[2]) ^ _aes_xtime(a[3]) ^ a[3]
                s[3][c] = _aes_xtime(a[0]) ^ a[0] ^ a[1] ^ a[2] ^ _aes_xtime(a[3])
        add_round_key(rnd)
    return bytes(s[i % 4][i // 4] for i in range(16))


def aes128_ecb_encrypt(key: bytes, block: bytes) -> bytes:
    """One-block AES-128 encryption (used for QUIC header protection)."""
    return _aes_encrypt_block(_aes_expand_key(key), block)


def aes128_ctr(key: bytes, counter_block: bytes, data: bytes) -> bytes:
    """AES-128-CTR. AES-GCM payload decryption with the counter at 2."""
    rk = _aes_expand_key(key)
    out = bytearray()
    ctr = int.from_bytes(counter_block, "big")
    for off in range(0, len(data), 16):
        ks = _aes_encrypt_block(rk, ctr.to_bytes(16, "big"))
        out += bytes(a ^ b for a, b in zip(data[off : off + 16], ks))
        ctr = (ctr + 1) & ((1 << 128) - 1)
    return bytes(out)


def _hkdf_expand_label(secret: bytes, label: str, length: int) -> bytes:
    """TLS 1.3 HKDF-Expand-Label (RFC 8446 §7.1), empty context."""
    info = (
        struct.pack(">H", length)
        + bytes([6 + len(label)])
        + b"tls13 "
        + label.encode()
        + b"\x00"
    )
    out, t, i = b"", b"", 1
    while len(out) < length:
        t = hmac.new(secret, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:length]


def quic_initial_client_keys(dcid: bytes) -> tuple[bytes, bytes, bytes]:
    """(key, iv, hp) for a QUIC v1 client Initial packet (RFC 9001 §5.2)."""
    initial = hmac.new(_QUIC_V1_INITIAL_SALT, dcid, hashlib.sha256).digest()
    client = _hkdf_expand_label(initial, "client in", 32)
    return (
        _hkdf_expand_label(client, "quic key", 16),
        _hkdf_expand_label(client, "quic iv", 12),
        _hkdf_expand_label(client, "quic hp", 16),
    )


def _quic_varint(b: bytes, o: int) -> tuple[Optional[int], int]:
    if o >= len(b):
        return None, o
    n = 1 << (b[o] >> 6)
    if o + n > len(b):
        return None, o
    v = b[o] & 0x3F
    for i in range(1, n):
        v = (v << 8) | b[o + i]
    return v, o + n


def quic_initial_crypto(payload: bytes) -> Optional[tuple[bytes, list]]:
    """(dcid, [(offset, data)]) of CRYPTO frames in a client Initial packet."""
    b = payload
    if len(b) < 64 or not (b[0] & 0x80):  # long header only
        return None
    if (b[0] & 0x30) >> 4 != 0:  # packet type Initial
        return None
    try:
        if struct.unpack_from(">I", b, 1)[0] != 1:  # QUIC v1
            return None
        o = 5
        dcil = b[o]
        o += 1
        dcid = bytes(b[o : o + dcil])
        o += dcil
        o += 1 + b[o]  # source connection id
        tlen, o = _quic_varint(b, o)
        if tlen is None:
            return None
        o += tlen  # token
        plen, o = _quic_varint(b, o)
        if plen is None or o + plen > len(b):
            return None
        pn_off = o
        key, iv, hp = quic_initial_client_keys(dcid)
        sample = bytes(b[pn_off + 4 : pn_off + 20])
        if len(sample) < 16:
            return None
        mask = aes128_ecb_encrypt(hp, sample)
        pn_len = ((b[0] ^ (mask[0] & 0x0F)) & 0x03) + 1
        pn = bytes(x ^ y for x, y in zip(b[pn_off : pn_off + pn_len], mask[1:]))
        ct = bytes(b[pn_off + pn_len : pn_off + plen])
        if len(ct) <= 16:  # nothing but the AEAD tag
            return None
        nonce = bytes(
            a ^ c for a, c in zip(iv, int.from_bytes(pn, "big").to_bytes(12, "big"))
        )
        pt = aes128_ctr(key, nonce + b"\x00\x00\x00\x02", ct[:-16])
        frames: list = []
        o = 0
        while o < len(pt):
            ft, o = _quic_varint(pt, o)
            if ft is None:
                break
            if ft == 0x00:  # PADDING
                while o < len(pt) and pt[o] == 0:
                    o += 1
                continue
            if ft == 0x01:  # PING
                continue
            if ft == 0x06:  # CRYPTO
                coff, o = _quic_varint(pt, o)
                clen, o = _quic_varint(pt, o)
                if coff is None or clen is None:
                    break
                frames.append((coff, bytes(pt[o : o + clen])))
                o += clen
                continue
            break  # any other frame type: stop rather than guess
        return (dcid, frames) if frames else None
    except (struct.error, IndexError, ValueError):
        return None


def _harvest_quic_sni(l4: bytes, dst: str, out: dict[str, str], state: dict) -> None:
    """Record dst-IP→SNI from a QUIC Initial ClientHello (UDP payload).

    Chrome's ClientHello does not fit in one Initial packet, so CRYPTO frames
    are reassembled per connection id before the hello is parsed.
    """
    if len(l4) < 8:
        return
    got = quic_initial_crypto(l4[8:])
    if got is None:
        return
    dcid, frames = got
    buf = state.setdefault(dcid, {})
    for off, data in frames:
        if data:
            buf[off] = data
    # Splice the contiguous run from offset 0; a gap means wait for more.
    whole, want = bytearray(), 0
    while want in buf:
        whole += buf[want]
        want += len(buf[want])
    host = _sni_from_client_hello(bytes(whole), 0) if whole else None
    if host:
        out.setdefault(dst, host)


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
    if _GVT_GGC_RE.search(h):
        return "browser_infra"
    for app in apps:
        if _host_matches(h, app_domains(app)):
            return app
    if _host_matches(h, BROWSER_INFRA_DOMAINS):
        return "browser_infra"
    return "other"


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
    # Epoch of the first captured packet. `times` is relative to it, while a
    # player series is relative to its own first sample; the QoE summary needs
    # both origins to put them on one x-axis, and it is handed this object
    # rather than the whole CaptureAttribution.
    capture_t0_epoch: Optional[float] = None

    def as_record(self) -> dict[str, Any]:
        """Record-safe dict (drops the full series to keep record.json small)."""
        d = asdict(self)
        d.pop("times", None)
        d.pop("mbps", None)
        d.pop("stall_bins", None)
        return d


# Buckets that represent "attribution could not place these bytes". Kept apart
# from app buckets and `browser_infra`, which are genuine classifications.
_UNATTRIBUTED_BUCKETS = frozenset({"other", "off_link", "intra_local"})


@dataclass
class CaptureAttribution:
    """The result of splitting one capture into per-app, per-direction traffic."""

    method: str = "none"  # local_alias_ip | remote_hostname | none
    note: str = ""  # why attribution produced nothing, if so
    bin_s: float = 1.0
    duration_s: float = 0.0
    nbins: int = 0
    local_ips: list[str] = field(default_factory=list)
    # Which rule decided `local_ips`, so a run record says whether locality was
    # ground truth or a guess: per_app_alias | declared_client_ips |
    # inferred_private_majority.
    local_ip_source: str = "none"
    # Addresses the caller declared as an app's far end (`app_endpoint_ips`).
    # Recorded so the split can be audited when the peer is RFC1918 and
    # therefore indistinguishable from the vantage by address alone.
    peer_ips: list[str] = field(default_factory=list)
    # probe name -> its endpoint IPs. A probe shares the link but is not an app:
    # it gets its own bucket, never a per-app pcap, and is never summed into the
    # shared-cap total, because it is not in `apps`.
    probe_ips: dict[str, list[str]] = field(default_factory=dict)
    per_app: dict[str, dict[str, AppTraffic]] = field(default_factory=dict)
    host_bytes: dict[str, int] = field(default_factory=dict)
    bucket_mb: dict[str, float] = field(default_factory=dict)
    # app -> the endpoint IPs whose packets belong to it. Disjoint across apps
    # (every IP classifies into exactly one bucket), which is what lets the
    # capture be split into per-app pcaps without double-counting a packet.
    app_ips: dict[str, list[str]] = field(default_factory=dict)
    ip_side: str = "remote"  # which end of the packet `app_ips` refers to
    attributed_fraction: float = 0.0  # share of bytes mapped to a named bucket
    total_mb: float = 0.0
    # Epoch of the first captured packet. Capture-derived series are relative
    # to this; player series are relative to their own first sample, so the two
    # only line up on a shared x-axis once this origin is known.
    t0_epoch: Optional[float] = None
    # Unnamed remote IPs adopted into an app bucket by connection affinity,
    # as ip -> "app:reason". Kept so every adoption is auditable after the fact.
    adopted_ips: dict[str, str] = field(default_factory=dict)
    # app -> direction -> bytes carried by hosts in MEDIA_HOST_SUFFIXES. This
    # is the only figure comparable with the player's own byte counters, since
    # those count media responses and nothing else. Bytes on an app endpoint
    # that was never named stay OUT, so a lost media flow shows up as a
    # shortfall here instead of being quietly absorbed.
    media_bytes: dict[str, dict[str, int]] = field(default_factory=dict)

    def traffic(self, app: str, direction: str) -> Optional[AppTraffic]:
        return (self.per_app.get(app) or {}).get(direction)


# Below ~6 KB/s a bin is carrying keepalives and ACKs, not content.
_IDLE_MBPS = 0.05


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

    # Idle = a bin that carried essentially nothing. This is an absolute
    # condition, not a fraction of the link: the old 5%-of-cap rule scored a
    # low-bitrate app on a fast link as permanently stalled. Measured case -- a
    # healthy Meet call runs a 0.505 Mbps median, and 5% of its 10 Mbps cap is
    # 0.5 Mbps, so 27 of 56 live seconds were called stalls while the capture
    # shows traffic in every one of them. Under-use of a fast link is real, but
    # it is what mean_over_cap and `starved` already say; it is not idleness.
    stall_thresh = _IDLE_MBPS
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


# ── unnamed-flow adoption ────────────────────────────────────────────────────
#
# Real-time media is routinely reached by IP with no DNS answer and no TLS SNI
# anywhere in the capture, because the address arrives inside already-encrypted
# signalling. Two measured examples from this repo's own runs:
#
#   * Google Meet's media relay 74.125.250.130 -- 7.31 MB, 34% of the capture,
#     carrying the entire call. Verified against the page's own
#     getStats(): pcap bytes from that peer track bytes_received to within
#     normal header/RTCP overhead, second for second.
#   * YouTube's QUIC media CDN 198.189.66.13 -- 18.68 MB, 72% of the capture.
#
# Dropping those into `other` does not merely mislabel bytes: it makes the app
# look idle for most of the run, so stall_seconds and delivered_fraction come
# out badly wrong on the plots. We therefore adopt an unnamed flow into an app
# bucket when exactly one app can claim it, and leave it in `other` whenever the
# evidence is ambiguous. Three signals, strongest first:
#
#   1. /24 shared with an IP already named for exactly one app. A CDN answers
#      from a block; YouTube's unnamed 198.189.66.13 sits beside the named
#      198.189.66.17 (r6---sn-...gvt1.com).
#   2. /16, same idea, weaker.
#   3. Timing: the flow runs concurrently with exactly one app's named traffic
#      and is *sustained* rather than a burst. This is what catches Meet, whose
#      relay shares no prefix with meet.google.com.
#
# Guards against swallowing Chrome's own background downloads (which can be tens
# of MB): a floor on bytes, and for the timing rule a requirement that the flow
# span a real fraction of the app's window -- a component update is a short
# early burst, a call or a video stream is not.

_ADOPT_MIN_BYTES = 262_144  # 256 KB: ignore stray probes and single exchanges
_ADOPT_MIN_OVERLAP = 0.5  # of the unnamed flow's own span
_ADOPT_MIN_SPAN_RATIO = 0.25  # of the app's named window: sustained, not a burst


def _prefix(ip: str, bits: int) -> Optional[str]:
    """Network prefix of an IPv4 address, or None for IPv6/unparseable."""
    if ":" in ip:
        return None
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    return ".".join(parts[: bits // 8])


def _sole(candidates: set) -> Optional[str]:
    """The single element of `candidates`, or None if it is not a singleton."""
    return next(iter(candidates)) if len(candidates) == 1 else None


def adopt_unnamed_ips(
    unnamed: dict[str, tuple[int, float, float]],
    named_app_ips: dict[str, set[str]],
    app_windows: dict[str, tuple[float, float]],
) -> dict[str, str]:
    """Map unnamed remote IP -> "app:reason" for flows attributable to one app.

    `unnamed` is ip -> (bytes, first_ts, last_ts); `named_app_ips` is app -> the
    IPs already attributed to it by DNS/SNI; `app_windows` is app -> (first,
    last) timestamps of that named traffic. IPs that stay ambiguous are simply
    absent from the result and remain in `other`.
    """
    by_pfx: dict[int, dict[str, set]] = {24: defaultdict(set), 16: defaultdict(set)}
    for app, ips in named_app_ips.items():
        for ip in ips:
            for bits in (24, 16):
                pfx = _prefix(ip, bits)
                if pfx:
                    by_pfx[bits][pfx].add(app)

    adopted: dict[str, str] = {}
    for ip, (nbytes, first, last) in unnamed.items():
        if nbytes < _ADOPT_MIN_BYTES:
            continue
        claimed = None
        for bits in (24, 16):
            pfx = _prefix(ip, bits)
            if pfx and (app := _sole(by_pfx[bits].get(pfx, set()))):
                claimed = (app, f"prefix/{bits}")
                break
        if claimed is None:
            span = max(last - first, 0.0)
            cands = set()
            for app, (a0, a1) in app_windows.items():
                overlap = min(last, a1) - max(first, a0)
                app_span = max(a1 - a0, 0.0)
                if span <= 0 or app_span <= 0:
                    continue
                # Concurrent with this app, and sustained rather than a burst.
                if (
                    overlap / span >= _ADOPT_MIN_OVERLAP
                    and span / app_span >= _ADOPT_MIN_SPAN_RATIO
                ):
                    cands.add(app)
            if app := _sole(cands):
                claimed = (app, "timing")
        if claimed:
            adopted[ip] = f"{claimed[0]}:{claimed[1]}"
    return adopted


def attribute_capture(
    pcap: Path,
    apps: Iterable[str],
    cap_mbps: float,
    bin_s: float = 1.0,
    local_ip_map: Optional[dict[str, str]] = None,
    app_endpoint_ips: Optional[dict[str, set[str]]] = None,
    client_ips: Optional[Iterable[str]] = None,
    probe_endpoint_ips: Optional[dict[str, set[str]]] = None,
) -> CaptureAttribution:
    """Split a shared capture into per-app download/upload throughput series.

    `local_ip_map` maps app → its namespace/alias IP. When supplied (substrate
    per-app marks), attribution is exact and by local IP. Otherwise the remote
    endpoint is identified from in-capture DNS answers + TLS SNI.

    Locality — which address is "us" — decides every packet's direction, so it
    is resolved in a fixed order of decreasing authority:

    1. `local_ip_map`      per-app alias IPs from the substrate (exact).
    2. `client_ips`        declared vantage addresses, e.g. ns1's veth1 IP.
    3. inference           private addresses carrying >=10% of packets.

    `probe_endpoint_ips` maps a *probe* name to its endpoints. A probe (IRTT, a
    NetVibe offerer) shares the link with the app under test but is not an app:
    it has no player, no QoE and no per-app pcap. Naming its endpoints keeps its
    traffic out of `other`, out of the app buckets, and -- the part that matters
    -- out of reach of the unnamed-flow adoption pass, which would otherwise
    hand a timing-overlapped RFC1918 flow straight to the browser app.

    Inference alone is wrong as soon as the far end is *also* RFC1918, which is
    the normal case for anything we run inside our own namespaces (the WebRTC
    peer server in ns2, an IRTT server, a NetVibe multiofferer). It claims both
    ends as local, so `remote` ends up pointing at a local address that has no
    hostname, and the flow lands in "other" — a measured 1.5% attributed. Hence
    rule 2, and hence `app_endpoint_ips` being subtracted from the inferred set:
    an address the caller names as an app's peer is the far side by definition.
    """
    apps = [a for a in apps]
    res = CaptureAttribution(bin_s=bin_s)
    if not pcap.exists() or pcap.stat().st_size < 24:
        return res

    ip_host: dict[str, str] = {}
    ip_from_alias = {ip: app for app, ip in (local_ip_map or {}).items() if ip}
    frames = 0
    # QUIC CRYPTO fragments, keyed by connection id, until a hello is whole.
    quic_state: dict = {}

    # ── pass 1: learn IP→hostname, local endpoints, and the time base ────────
    endpoint_pkts: dict[str, int] = defaultdict(int)
    # Per-endpoint bytes and activity window, so unnamed flows can be judged
    # once the local address is known (below). A remote IP only ever pairs with
    # the local one, so summing packets it appears in gives its flow total.
    endpoint_bytes: dict[str, int] = defaultdict(int)
    endpoint_span: dict[str, list[float]] = {}
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
        for _ep in (src, dst):
            endpoint_bytes[_ep] += _ol
            if ts:
                span = endpoint_span.get(_ep)
                if span is None:
                    endpoint_span[_ep] = [ts, ts]
                else:
                    span[1] = ts
        if proto == 17 and len(l4) >= 8:
            sport, _dport = struct.unpack_from(">HH", l4, 0)
            if sport == 53:
                _harvest_dns(l4, ip_host)
            elif _dport == 443:
                # QUIC: the only in-the-clear name a QUIC-only media flow ever
                # offers. Without this, YouTube's media endpoint is named only
                # when a stray TCP connection to it happens to be captured.
                _harvest_quic_sni(l4, dst, ip_host, quic_state)
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
    res.t0_epoch = round(t0, 3)
    res.nbins = max(int(math.ceil(res.duration_s / bin_s)), 1)

    # Addresses the caller names as some app's peer are the far side of the
    # measurement by definition, so they can never be the vantage point.
    _probe_ip_name: dict[str, str] = {
        ip: name
        for name, ips in (probe_endpoint_ips or {}).items()
        for ip in (ips or ())
        if ip
    }
    _declared_app_ips = {
        ip for ips in (app_endpoint_ips or {}).values() for ip in ips if ip
    }
    # A probe's far end is as much a peer as an app's: it is the other side of
    # the measurement, so it can never be the vantage point either.
    declared_peers = _declared_app_ips | set(_probe_ip_name)

    # Local endpoints: see the ordering in the docstring.
    if ip_from_alias:
        res.local_ips = sorted(ip_from_alias)
        res.method = "local_alias_ip"
        res.local_ip_source = "per_app_alias"
    elif client_ips:
        declared_client = {ip for ip in client_ips if ip}
        # An address declared as both the vantage and an app's peer makes every
        # direction arbitrary. Refuse rather than pick one silently.
        contradiction = sorted(declared_client & declared_peers)
        if contradiction:
            raise ValueError(
                f"address(es) {contradiction} are declared BOTH as the capture "
                f"vantage (client_ips) and as an app endpoint (app_endpoint_ips). "
                f"One of the two is wrong, and the choice silently decides the "
                f"direction of every packet, so attribution will not guess."
            )
        res.local_ips = sorted(declared_client)
        res.method = "remote_hostname"
        res.local_ip_source = "declared_client_ips"
    else:
        res.local_ips = sorted(
            ip
            for ip, c in endpoint_pkts.items()
            if _is_private_ip(ip)
            and c >= 0.10 * total_pkts
            and ip not in declared_peers
        )
        if not res.local_ips and endpoint_pkts:
            busiest = [ip for ip in endpoint_pkts if ip not in declared_peers]
            if busiest:
                res.local_ips = [max(busiest, key=lambda k: endpoint_pkts[k])]
        res.method = "remote_hostname"
        res.local_ip_source = "inferred_private_majority"
    local = set(res.local_ips)
    res.peer_ips = sorted(declared_peers)
    res.probe_ips = {
        name: sorted(ips)
        for name, ips in sorted((probe_endpoint_ips or {}).items())
        if ips
    }

    # ── pass 1b: adopt unnamed flows that only one app can plausibly own ─────
    # Only meaningful for remote-hostname attribution; with per-app alias IPs
    # every packet already carries its app identity and nothing is unnamed.
    adopted: dict[str, str] = {}
    if res.method == "remote_hostname":
        named_app_ips: dict[str, set[str]] = defaultdict(set)
        app_windows: dict[str, tuple[float, float]] = {}
        # A target reached by raw IP has no DNS answer and no SNI anywhere in
        # the capture, so there is nothing for the hostname pass to match and
        # nothing for the affinity pass to anchor on either. When the caller
        # knows the endpoint (it is in the run's own URL) that is ground truth,
        # not a heuristic, so seed it directly.
        for _app, _ips in (app_endpoint_ips or {}).items():
            for _ip in _ips:
                # Deliberately NOT skipped when `_ip` is private: a peer we run
                # ourselves inside ns2 is RFC1918, and dropping it here was the
                # second half of the 1.5%-attributed collapse. It cannot be in
                # `local` any more — declared peers are excluded above, and a
                # contradiction with `client_ips` raises.
                named_app_ips[_app].add(_ip)
                _sp = endpoint_span.get(_ip)
                if _sp:
                    _cur = app_windows.get(_app)
                    app_windows[_app] = (
                        (_sp[0], _sp[1])
                        if _cur is None
                        else (min(_cur[0], _sp[0]), max(_cur[1], _sp[1]))
                    )
        for ip, host in ip_host.items():
            if ip in local:
                continue
            bucket = classify_host(host, apps)
            if bucket in apps:
                named_app_ips[bucket].add(ip)
                span = endpoint_span.get(ip)
                if span:
                    cur = app_windows.get(bucket)
                    app_windows[bucket] = (
                        (span[0], span[1])
                        if cur is None
                        else (min(cur[0], span[0]), max(cur[1], span[1]))
                    )
        # A probe's endpoint is named, so it is not "unnamed" and must not be
        # offered to adoption. IRTT's UDP flow overlaps the browser's run
        # exactly and carries no DNS answer or SNI, which is precisely the
        # shape `adopt_unnamed_ips` claims by timing when only one app is in
        # the run -- it would have been handed to the browser app.
        # Declared endpoints -- an app's or a probe's -- are named, so they are
        # not "unnamed" and must not be offered to adoption. A declared app
        # endpoint with no in-capture hostname was otherwise "adopted" into the
        # app it had already been declared for, which put an entry in
        # `adopted_ips` that was not an adoption at all. That field exists so
        # every real adoption is auditable, so it has to mean only that.
        unnamed = {
            ip: (endpoint_bytes[ip], sp[0], sp[1])
            for ip, sp in endpoint_span.items()
            if ip not in local
            and ip not in ip_host
            and ip not in _probe_ip_name
            and ip not in _declared_app_ips
        }
        adopted = adopt_unnamed_ips(unnamed, named_app_ips, app_windows)
        res.adopted_ips = dict(sorted(adopted.items()))
        if adopted:
            mb = sum(endpoint_bytes[ip] for ip in adopted) / 1e6
            print(
                f"  + adopted {len(adopted)} unnamed flow(s), {mb:.2f} MB, "
                f"by connection affinity: "
                + ", ".join(f"{ip}->{v}" for ip, v in sorted(adopted.items())[:4])
            )

    _explicit_ip_app: dict[str, str] = {
        ip: app for app, ips in (app_endpoint_ips or {}).items() for ip in ips
    }

    # ── pass 2: bin bytes per (bucket, direction) ────────────────────────────
    buckets: dict[tuple[str, str], dict[int, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    pkt_counts: dict[tuple[str, str], int] = defaultdict(int)
    media_tally: dict[tuple[str, str], int] = defaultdict(int)
    bucket_hosts: dict[str, set[str]] = defaultdict(set)
    host_bytes: dict[str, int] = defaultdict(int)
    bucket_bytes: dict[str, int] = defaultdict(int)
    bucket_ips: dict[str, set[str]] = defaultdict(set)
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
        if src in local and dst in local:
            # Both ends are the vantage point. With a correct local set this
            # cannot happen; when it does, the old code took the `dst in local`
            # branch and called it a download with `remote` pointing at a local
            # address — unattributable, and counted twice over the two
            # directions. Tally it as its own bucket so it is visible instead.
            bucket_bytes["intra_local"] += ol
            continue
        if dst in local:
            direction, remote, localside = "download", src, dst
        elif src in local:
            direction, remote, localside = "upload", dst, src
        else:
            bucket_bytes["off_link"] += ol
            continue

        if remote in _probe_ip_name:
            # Checked before anything else, including the alias path: a probe's
            # identity is declared, not inferred, and it must not be able to
            # fall into an app bucket by any route.
            bucket = _probe_ip_name[remote]
        elif res.method == "local_alias_ip":
            bucket = ip_from_alias.get(localside, "other")
        else:
            host = ip_host.get(remote, "")
            if host:
                bucket = classify_host(host, apps)
                host_bytes[host] += ol
                if bucket in apps:
                    bucket_hosts[bucket].add(host)
            elif _explicit_ip_app.get(remote):
                bucket = _explicit_ip_app[remote]
            elif remote in adopted:
                bucket = adopted[remote].split(":", 1)[0]
            else:
                bucket = classify_host(host, apps)

        bucket_bytes[bucket] += ol
        if bucket in apps and res.method != "local_alias_ip":
            _h = ip_host.get(remote, "")
            if _h and _host_matches(_h, media_host_suffixes(bucket)):
                media_tally[(bucket, direction)] += ol
        if bucket in apps:
            # Remember which endpoint carried this app's bytes so the capture
            # can later be split into one pcap per app.
            bucket_ips[bucket].add(
                localside if res.method == "local_alias_ip" else remote
            )
            idx = min(int((ts - t0) / bin_s), res.nbins - 1) if ts else 0
            buckets[(bucket, direction)][idx] += ol
            pkt_counts[(bucket, direction)] += 1

    res.total_mb = round(total_bytes / 1e6, 3)
    res.bucket_mb = {
        k: round(v / 1e6, 3)
        for k, v in sorted(bucket_bytes.items(), key=lambda kv: -kv[1])
    }
    res.host_bytes = dict(sorted(host_bytes.items(), key=lambda kv: -kv[1])[:40])
    res.ip_side = "local" if res.method == "local_alias_ip" else "remote"
    res.app_ips = {a: sorted(bucket_ips.get(a, ())) for a in apps}
    res.media_bytes = {
        a: {d: media_tally.get((a, d), 0) for d in ("download", "upload")} for a in apps
    }
    # "attributed" means "assigned to a named traffic class". `other` is the
    # explicit unknown, and `off_link`/`intra_local` are packets attribution
    # could not place at all, so counting them as named would overstate the
    # very figure that is supposed to reveal this bug.
    named = sum(v for k, v in bucket_bytes.items() if k not in _UNATTRIBUTED_BUCKETS)
    res.attributed_fraction = round(named / total_bytes, 4) if total_bytes else 0.0
    if bucket_bytes.get("intra_local"):
        share = 100 * bucket_bytes["intra_local"] / total_bytes
        res.note = (
            f"{share:.1f}% of bytes travelled between two addresses both held "
            f"to be local ({sorted(local)}), so they could not be attributed. "
            f"Declare the capture vantage via client_ips, or the far end via "
            f"app_endpoint_ips, so one side is unambiguously remote."
        )
        print(f"  ! {res.note}")

    for app in apps:
        res.per_app[app] = {}
        for direction in ("download", "upload"):
            at = AppTraffic(app=app, direction=direction)
            at.capture_t0_epoch = res.t0_epoch
            at.packets = pkt_counts.get((app, direction), 0)
            at.hosts = sorted(bucket_hosts.get(app, ()))[:12]
            res.per_app[app][direction] = _finalise(
                at, cap_mbps, buckets.get((app, direction), {}), res.nbins, bin_s
            )
    return res


def split_capture_by_app(
    pcap: Path,
    attribution: "CaptureAttribution",
    apps: list[str],
    run_dir: Path,
) -> dict[str, str]:
    """Write one pcap per app, so a concurrent run ships two captures not one.

    Jaber asked for "two PCAPs" rather than one shared capture plus after-the-
    fact attribution. The split is done with tcpdump on the endpoint IPs that
    attribution assigned to each app, which keeps the output a real pcap that
    any tool can read.

    On the substrate build deployed here every browser app shares `ns1` and one
    source IP (`POST /shape/per_app_marks` is not implemented — it 404s), so the
    apps cannot be told apart by *source* address while capturing. They are
    separated by the *remote* endpoint instead, which is exact whenever the host
    is known from DNS/SNI in the same capture. When a worker that does implement
    per-app alias IPs is deployed, `attribution.method` becomes `local_alias_ip`
    and the very same code splits on the local address with no change.

    Solo runs are left alone: one app already means one capture, so the run's
    `capture.pcap` is simply reported as that app's pcap.
    """
    out: dict[str, str] = {}
    if not pcap.exists() or pcap.stat().st_size < 24:
        return out
    if len(apps) < 2:
        return {apps[0]: str(pcap)} if apps else {}
    if shutil.which("tcpdump") is None:
        print("  ! tcpdump unavailable — cannot write per-app pcaps")
        return out

    # `host X` matches X as either source or destination, so one filter keeps
    # both directions of that app's traffic. (`dst X` would silently drop the
    # app's uploads, which the upload plot then reports as an empty series.)
    side = "host"
    for app in apps:
        ips = attribution.app_ips.get(app) or []
        if not ips:
            print(f"    · {app}: no endpoints attributed — no pcap written")
            continue
        dest = run_dir / f"capture_{app}.pcap"
        # A filter file avoids the argv length limit: a busy CDN app can be
        # spread over hundreds of addresses.
        expr = " or ".join(f"{side} {ip}" for ip in ips)
        ffile = run_dir / f".filter_{app}.bpf"
        ffile.write_text(expr)
        try:
            proc = subprocess.run(
                ["tcpdump", "-r", str(pcap), "-w", str(dest), "-F", str(ffile)],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if proc.returncode != 0:
                print(f"    · {app}: tcpdump failed — {proc.stderr.strip()[:120]}")
                continue
            out[app] = str(dest)
            size_mb = dest.stat().st_size / 1e6
            print(
                f"    · {app}: capture_{app}.pcap  {size_mb:7.2f} MB  ({len(ips)} endpoints)"
            )
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"    · {app}: split failed — {type(exc).__name__}: {exc}")
        finally:
            ffile.unlink(missing_ok=True)
    return out


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
    # Set when the download direction could not be isolated, so a caller never
    # has to infer "no packets" from `avg_throughput_mbps is None`.
    note: str = ""


def analyze_pcap_netstats(
    pcap: Path, cap_mbps: float, client_ips: Optional[Iterable[str]] = None
) -> NetStats:
    """Compute throughput stats from the capture and verify the cap was respected.

    `client_ips` declares the capture vantage. Without it locality is inferred
    as "any private address", which claims both ends of a flow between two of
    our own namespaces and then counts every packet as a download — inflating
    measured throughput by roughly 2x against a download-only cap.
    """
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

    # Local side = the endpoint the capture sits behind. Declared when the
    # caller knows it; otherwise inferred as "private", falling back to the
    # busiest endpoint, as attribution does.
    note = ""
    if client_ips:
        local = {ip for ip in client_ips if ip}
    else:
        local = {ip for ip in endpoint_pkts if _is_private_ip(ip)}
        if not local and endpoint_pkts:
            local = {max(endpoint_pkts, key=lambda k: endpoint_pkts[k])}
    # `_src not in local` is what keeps a flow between two of our own
    # namespaces out of the download total. Without it both directions count as
    # download and the shaping check is measured against roughly twice the real
    # download rate.
    down = [pkt for pkt, _src, dst in ip_pkts if dst in local and _src not in local]
    intra = sum(1 for _pkt, _src, dst in ip_pkts if dst in local and _src in local)
    if intra and not down:
        note = (
            f"every IP packet ran between two addresses both held to be local "
            f"({sorted(local)}), so no download direction could be isolated "
            f"and no throughput is reported. Pass client_ips to name the "
            f"capture vantage."
        )
        print(f"  ! {note}")
    down_bytes = sum(p.orig_len for p in down)

    ns = NetStats(
        note=note,
        packets=len(packets),
        duration_s=round(duration, 2),
        total_mb=round(total_bytes / 1e6, 2),  # whole capture, both directions
        # `None`, not 0.0, when the download direction could not be isolated:
        # a reported 0.0 Mbps compares as "under the cap" and would mark the
        # shaping check verified on a capture nothing was measured from.
        avg_throughput_mbps=(
            round(down_bytes * 8 / max(duration, 0.001) / 1e6, 3)
            if duration and down
            else None
        ),
    )

    # per-second throughput series for peak / p95 / stalls — shaped direction
    # only. Falling back to every packet when `down` is empty would report a
    # peak and a p95 for a direction that was never identified.
    _, rates = _throughput_series(down, 1.0) if down else (None, [])
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
    # Plain line, no fill: overlapping traces stay readable when these plots
    # are compared side by side (Jaber's request).
    ax.plot(at.times, at.mbps, linewidth=1.2, color=color)
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
    ax.set_ylim(bottom=0, top=_throughput_ymax(cap, at.peak_throughput_mbps))
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
        # Real player metrics: measured progress is the arbiter. Name the signal
        # that actually produced it -- a conferencing app and a MediaStream
        # <video> have no media clock at all, so claiming "the clock advanced"
        # states the opposite of what these runs established.
        watched = pq.get("watched_seconds") or 0
        basis = str((pq.get("derivation") or {}).get("progress_basis") or "")
        moved = (
            "decoded frames advanced"
            if basis.startswith(("total_video_frames", "frames_decoded"))
            else "player clock advanced"
        )
        if pq.get("video_resolution_p") and watched > 0:
            rebuffers = pq.get("rebuffer_events") or 0
            frozen_ms = pq.get("rebuffer_duration_ms") or 0
            if rebuffers or frozen_ms:
                return (
                    "degraded",
                    f"{moved} but rebuffered {rebuffers}× "
                    f"({frozen_ms:.0f} ms frozen): playback was impaired",
                    "#EF6C00",
                )
            return (
                "served",
                f"{moved} {watched:.0f}s with no rebuffering: "
                "the app really streamed",
                "#2E7D32",
            )
        return (
            "no_playback",
            "player reported no advancing playback: the app did not play",
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


def plot_apps_throughput(
    attribution: "CaptureAttribution",
    cfg: ExperimentConfig,
    out: Path,
    direction: str,
) -> Optional[Path]:
    """All apps of one run, one direction, on ONE chart — one line per app.

    Nothing is summed: each app keeps its own real per-second series, its own
    colour and its own annotated peak / average / total, so the lines can be
    compared directly against the shared cap.
    """
    plt = _plt()
    if plt is None:
        return None
    series = []
    for app in cfg.apps:
        at = attribution.traffic(app, direction)
        if at is not None and at.times and at.avg_throughput_mbps is not None:
            series.append(at)
    if not series:
        return None

    cap = cfg.bandwidth_mbps or 0.0
    peak = max((a.peak_throughput_mbps or 0.0) for a in series)
    fig, ax = plt.subplots(figsize=(12, 4.6))
    for at in series:
        ax.plot(
            at.times,
            at.mbps,
            linewidth=1.2,
            color=app_color(at.app),
            label=(
                f"{display_name(at.app)} — avg {at.avg_throughput_mbps:.2f}, "
                f"peak {at.peak_throughput_mbps:.2f} Mbps, {at.total_mb:.1f} MB"
            ),
        )

    # The cap is the shared resource these lines compete for, so it belongs on
    # both charts. Upload for video is ACKs — two orders of magnitude under the
    # cap — and forcing the cap into view would flatten every line onto the
    # axis, so there the cap is marked on the axis edge and named in the legend
    # instead of dictating the scale.
    cap_in_view = cap <= peak * 1.6 or peak >= cap * 0.25
    if cap_in_view:
        top = _throughput_ymax(cap, peak)
        ax.axhline(
            cap, color="green", linewidth=1.3, alpha=0.85, label=f"cap {cap:g} Mbps"
        )
    else:
        # Upload keeps its own shared ceiling rather than the download one: a
        # Meet call uploads ~0.4 Mbps, so drawing it against 11 Mbps would put
        # all six runs flat on the axis - comparable, but unreadable.
        top = (
            max(PLOT_YMAX_UPLOAD_MBPS, peak * 1.05)
            if PLOT_YMAX_UPLOAD_MBPS is not None
            else max(peak * 1.30, 0.05)
        )
        ax.axhline(
            top,
            color="green",
            linewidth=1.3,
            alpha=0.55,
            linestyle=":",
            label=f"cap {cap:g} Mbps (above axis — peak is {100 * peak / cap:.1f}% of cap)",
        )

    ax.set_xlabel("Time (s)")
    ax.set_ylabel(f"{direction.title()} throughput (Mbps)")
    ax.set_title(
        f"Throughput ({direction}) — {' vs '.join(display_name(a.app) for a in series)}"
        f" — {_regime(cfg)}"
    )
    ax.set_xlim(0, max(max(a.times) for a in series))
    ax.set_ylim(bottom=0, top=top)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


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

    # These panels share one x-axis, but the two families of series are measured
    # from different origins: throughput from the first captured packet, player
    # series from the first sample -- which is taken only after the browser has
    # launched, navigated and joined. Measured gaps: 29.4s on a YouTube run,
    # 36.4s on a Zoom one. Plotted raw, a rebuffer lands half a minute away from
    # the throughput dip that caused it. Shift the player series onto capture
    # time so the panels can be read against each other.
    _cap_t0 = getattr(at, "capture_t0_epoch", None)
    _sess_t0 = pq.get("session_start_epoch")
    x_shift = 0.0
    if _cap_t0 is not None and _sess_t0 is not None:
        x_shift = max(0.0, float(_sess_t0) - float(_cap_t0))

    def _shift(pts):
        return [{"t": d["t"] + x_shift, "v": d["v"]} for d in pts]

    buf_series = _shift(series.get("buffer_ahead_secs") or [])
    res_series = _shift(series.get("resolution_p") or [])
    fps_series = _shift(series.get("fps") or [])
    drop_series = _shift(series.get("dropped_pct") or [])
    jit_series = _shift(series.get("jitter_ms") or [])
    rebuffer_spans = [
        (a + x_shift, b + x_shift) for a, b in (pq.get("rebuffer_spans") or [])
    ]

    # Window the whole figure on the measured session rather than the capture.
    # The capture opens before the app has joined and can outlast it: measured
    # here, three of six Meet runs put the entire call outside the capture's own
    # x-range, so their resolution panels came out completely blank while the
    # numbers underneath were fine. Showing minutes of pre-join dead air also
    # squeezes the part that matters into a sliver.
    sess_len = pq.get("session_seconds")
    x_lo, x_hi = 0.0, (max(at.times) if at.times else 1.0)
    if real and sess_len:
        pad = max(2.0, float(sess_len) * 0.03)
        x_lo = max(0.0, x_shift - pad)
        x_hi = x_shift + float(sess_len) + pad

    extra = [
        (fps_series, "fps", "#00838F"),
        (drop_series, "dropped (%)", "#AD1457"),
        (jit_series, "jitter (ms)", "#5E35B1"),
    ]
    extra = [(pts, lbl, col) for pts, lbl, col in extra if real and pts]

    panels = (
        1
        + (1 if (real and buf_series) else 0)
        + (1 if (real and res_series) else 0)
        + len(extra)
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
    ax.set_ylim(bottom=0, top=_throughput_ymax(cap, at.peak_throughput_mbps))
    ax.set_xlim(x_lo, x_hi)
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
        rt2 = rt + [x_hi]
        rv2 = rv + [rv[-1]]
        rx.step(rt2, rv2, where="post", linewidth=1.4, color="#2E7D32")
        rx.scatter(rt, rv, s=18, color="#2E7D32", zorder=3)
        rx.set_ylabel("resolution (p)")
        rx.set_xlabel("Time (s)")
        rx.grid(True, alpha=0.3)
        ys = sorted(set(rv))
        rx.set_yticks(ys)
        rx.set_ylim(min(ys) * 0.8, max(ys) * 1.2)

    # ── per-sample timelines: fps, dropped %, jitter ─────────────────────────
    # One number per metric at the foot of the chart says a call averaged 23 fps;
    # it cannot say whether that was steady or swung about. These are the same
    # measurements over time, from the same samples.
    for pts, label, colour in extra:
        px = axes[idx]
        idx += 1
        xs = [d["t"] for d in pts]
        ys2 = [d["v"] for d in pts]
        px.plot(xs, ys2, linewidth=1.2, color=colour)
        px.fill_between(xs, ys2, alpha=0.15, color=colour)
        if ys2:
            mean_v = sum(ys2) / len(ys2)
            px.axhline(
                mean_v,
                color=colour,
                linestyle="--",
                linewidth=0.9,
                alpha=0.75,
                label=f"mean {mean_v:.2f}",
            )
            px.legend(loc="upper right", fontsize=7)
        px.set_ylabel(label)
        px.grid(True, alpha=0.3)
        px.set_ylim(bottom=0)

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

    Per run: `throughput_download_all.png` and `throughput_upload_all.png` —
    plain line graphs carrying one line per app (never summed), each with its
    own avg/peak/total. Per app: one `qoe_summary_<app>.png`.
    """
    plots: dict[str, str] = {}
    if _plt() is None:
        print("  ! matplotlib unavailable — skipping plots")
        return plots

    # Throughput: ONE merged chart per direction for the whole run — every app's
    # own line on one axis, nothing summed. Produced for solo runs too (a single
    # app is simply a single line), so every run has the same two files and no
    # per-app throughput variants exist.
    for _d in app_plot_directions(cfg.apps[0] if cfg.apps else "youtube"):
        ap = plot_apps_throughput(
            attribution, cfg, run_dir / f"throughput_{_d}_all.png", _d
        )
        if ap:
            plots[f"throughput_{_d}_all"] = str(ap)
            print(f"    · {_d}: merged throughput chart saved")
        else:
            print(f"    · {_d}: no merged chart — no traffic attributed")

    # Per-app throughput, one chart per app per direction, alongside the merged
    # view above. A concurrent run needs both: the merged chart shows how the
    # apps share the link, the per-app charts let each be read on its own.
    for app in cfg.apps:
        for _d in app_plot_directions(app):
            at_app = attribution.traffic(app, _d)
            if at_app is None or not at_app.times:
                continue
            ap1 = plot_app_throughput(
                at_app, cfg, run_dir / f"throughput_{_d}_{app}.png"
            )
            if ap1:
                plots[f"throughput_{_d}_{app}"] = str(ap1)
                print(f"    · {app} {_d}: per-app throughput chart saved")

    for app in cfg.apps:
        # QoE summary is unchanged: one per app, built from the direction that
        # defines the verdict (download for video). Adding upload plots above
        # must not double or rename these.
        qdir = app_verdict_directions(app)[0]
        qat = attribution.traffic(app, qdir)
        if qat is not None and qat.times and qat.avg_throughput_mbps is not None:
            qs = plot_app_qoe_summary(
                qat,
                cfg,
                run_dir / f"qoe_summary_{app}.png",
                player_qoe=(player_qoe or {}).get(app),
                bin_s=attribution.bin_s,
            )
            if qs:
                plots[f"qoe_summary_{app}"] = str(qs)
                print(f"    · {app}: qoe_summary saved")
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

    # Same ground truth as the live path in run_direct: a re-render must not
    # quietly fall back to inferred locality and produce a different split from
    # the run it is re-rendering.
    at = attribute_capture(
        pcap,
        cfg.apps,
        cfg.bandwidth_mbps,
        bin_s=bin_s,
        app_endpoint_ips=merge_endpoint_ips(
            app_endpoint_ips_from_urls(cfg),
            app_endpoint_ips_from_webrtc_stats(run_dir, cfg.apps),
        ),
        client_ips=client_ips_for_capture(),
    )
    # Carry the record's real player QoE into the plots, so a re-render shows the
    # player-side panels rather than falling back to the network proxy view.
    plots = generate_run_plots(
        at, cfg, run_dir, player_qoe=(rec.get("player_qoe") or {})
    )
    rec.setdefault("artifacts", {})["plots"] = plots
    # This IS the deferred work, so the run is no longer deferred. Leaving the
    # flag set would report a finished run as missing its analysis.
    if rec.get("analysis_deferred"):
        rec["analysis_deferred"] = False
        rec["analysis_finalized_at"] = time.time()
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
            "verdict_directions": app_verdict_directions(a),
            "classification": app_verdict(
                (
                    at.traffic(a, app_verdict_directions(a)[0])
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


# ═════════════════════════════════════════════════════════════════════════════
#  IRTT — a UDP round-trip probe that shares the link with the app under test
#
#  It is a *probe*, not an app: no player, no QoE, no per-app pcap, and it is
#  never summed into the shared-cap total. It has the same start/stop lifecycle
#  as /capture and /replay, which is what lets it run concurrently with a
#  browser without touching run_direct's app sequencing.
# ═════════════════════════════════════════════════════════════════════════════
IRTT_ENABLED = os.environ.get("PRAMANA_IRTT", "0").strip() not in ("", "0", "false")
IRTT_PROBE_NAME = "irtt"
IRTT_SERVER_ADDR = os.environ.get("PRAMANA_IRTT_SERVER", "172.16.2.1")
IRTT_PORT = int(os.environ.get("PRAMANA_IRTT_PORT", "2112"))
IRTT_INTERVAL_MS = int(os.environ.get("PRAMANA_IRTT_INTERVAL_MS", "100"))
# The deployed worker image predates POST /irtt and answers 404. Rather than
# silently skipping the probe, the orchestrator can drive irtt itself by
# entering the worker's namespaces from the host. Opt-in, and always recorded
# in the run as `transport`, so no reader has to guess which path produced the
# numbers.
IRTT_HOST_FALLBACK = os.environ.get("PRAMANA_IRTT_HOST_FALLBACK", "0").strip() not in (
    "",
    "0",
    "false",
)
IRTT_BIN = os.environ.get("PRAMANA_IRTT_BIN", "irtt")
IRTT_WORKER_CONTAINER = os.environ.get("PRAMANA_IRTT_CONTAINER", "substrate-worker")


def _irtt_netns_path(namespace: str) -> str:
    """Host path to one of the worker's network namespaces.

    ns1/ns2 live inside the worker container, so they are not in the host's
    /run/netns. They are reachable through the container's root filesystem.
    """
    pid = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Pid}}", IRTT_WORKER_CONTAINER],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if pid.returncode != 0 or not pid.stdout.strip().isdigit():
        raise RuntimeError(
            f"could not read pid of container {IRTT_WORKER_CONTAINER!r}: "
            f"{pid.stderr.strip()[:200]}"
        )
    return f"/proc/{pid.stdout.strip()}/root/run/netns/{namespace}"


def _irtt_host_start(duration_s: int) -> dict[str, Any]:
    """Start irtt from the host, inside the worker's namespaces."""
    binary = shutil.which(IRTT_BIN) or IRTT_BIN
    bind = f"{IRTT_SERVER_ADDR}:{IRTT_PORT}"
    srv_ns = _irtt_netns_path("ns2")
    cli_ns = _irtt_netns_path("ns1")
    out = Path(tempfile.gettempdir()) / f"irtt_{uuid.uuid4().hex[:10]}.json"

    # `timeout` is a hard backstop, not the normal stop path. Without it an
    # interpreter that dies mid-run leaves a server holding port 2112, and the
    # next run fails to bind with an error that looks nothing like the cause.
    server = subprocess.Popen(
        [
            "sudo",
            "-n",
            "timeout",
            "-k",
            "5",
            str(duration_s + 120),
            "nsenter",
            f"--net={srv_ns}",
            binary,
            "server",
            "-b",
            bind,
            "-d",
            f"{duration_s + 60}s",
            "-i",
            "1ms",
            "--timeout=10s",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Readiness, not a sleep: `-n` validates the test against the server
    # without sending one. Starting the client too early fails with
    # "connection refused", which reads as a network fault rather than a race.
    deadline = time.time() + 10.0
    last = ""
    while time.time() < deadline:
        if server.poll() is not None:
            _, err = server.communicate()
            raise RuntimeError(
                f"irtt server exited immediately (rc={server.returncode}): "
                f"{err.decode(errors='replace')[:300]}"
            )
        probe = subprocess.run(
            [
                "sudo",
                "-n",
                "nsenter",
                f"--net={cli_ns}",
                binary,
                "client",
                "-n",
                "-Q",
                "-d",
                "1s",
                bind,
            ],
            capture_output=True,
            timeout=15,
        )
        if probe.returncode == 0:
            break
        last = probe.stderr.decode(errors="replace").strip()[:200]
        time.sleep(0.5)
    else:
        _irtt_kill(server)
        raise RuntimeError(f"irtt server at {bind} never accepted a client: {last}")

    client = subprocess.Popen(
        [
            "sudo",
            "-n",
            "nsenter",
            f"--net={cli_ns}",
            binary,
            "client",
            "-i",
            f"{IRTT_INTERVAL_MS}ms",
            "-d",
            f"{duration_s}s",
            "-Q",
            "-o",
            str(out),
            bind,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return {
        "transport": "host_nsenter",
        "server_proc": server,
        "client_proc": client,
        "out_path": str(out),
        "server_address": IRTT_SERVER_ADDR,
        "port": IRTT_PORT,
        "endpoint_ips": [IRTT_SERVER_ADDR],
    }


def _irtt_kill(proc: "subprocess.Popen") -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _irtt_host_stop(session: dict[str, Any]) -> dict[str, Any]:
    client, server = session["client_proc"], session["server_proc"]
    stderr = ""
    try:
        _, err = client.communicate(timeout=20)
        stderr = err.decode(errors="replace")[:500]
    except subprocess.TimeoutExpired:
        _irtt_kill(client)
    _irtt_kill(server)

    out = Path(session["out_path"])
    result, error = None, None
    try:
        if out.exists():
            result = json.loads(out.read_text())
        else:
            error = (
                f"irtt wrote no output (rc={client.returncode}): "
                f"{stderr.strip()[:300]}"
            )
    except (ValueError, OSError) as exc:
        error = f"irtt output unreadable: {type(exc).__name__}: {exc}"
    finally:
        # Written by root through sudo, so removing it needs the same.
        subprocess.run(
            ["sudo", "-n", "rm", "-f", str(out)], capture_output=True, timeout=30
        )
    return {
        "transport": "host_nsenter",
        "server_address": session["server_address"],
        "port": session["port"],
        "endpoint_ips": session["endpoint_ips"],
        "client_returncode": client.returncode,
        "result": result,
        "error": error,
    }


def substrate_irtt_start(duration_s: int) -> Optional[dict[str, Any]]:
    """Start the probe. Returns a session handle, or None if it could not start.

    Tries the worker endpoint first. A 404 means the deployed image predates
    POST /irtt; that is reported, and the host fallback is used only when it has
    been explicitly enabled.
    """
    payload = {
        "duration_seconds": int(duration_s),
        "interval_ms": IRTT_INTERVAL_MS,
        "server_namespace": "ns2",
        "client_namespace": "ns1",
        "server_address": IRTT_SERVER_ADDR,
        "port": IRTT_PORT,
    }
    try:
        r = requests.post(f"{SUBSTRATE}/irtt", json=payload, timeout=HTTP_TIMEOUT * 2)
        if r.status_code != 404:
            r.raise_for_status()
            data = r.json()
            data["transport"] = "worker_endpoint"
            print(
                f"    · IRTT started via worker endpoint "
                f"(id={data.get('irtt_id', '?')[:8]}, "
                f"{IRTT_SERVER_ADDR}:{IRTT_PORT}, every {IRTT_INTERVAL_MS} ms)"
            )
            return data
        print(
            "    ! worker has no POST /irtt (404) — its image predates this "
            "endpoint and needs rebuilding from services/substrate-worker"
        )
    except requests.RequestException as exc:
        print(f"    ! IRTT start via worker failed: {type(exc).__name__}: {exc}")
        return None

    if not IRTT_HOST_FALLBACK:
        print(
            "    ! IRTT skipped. Set PRAMANA_IRTT_HOST_FALLBACK=1 to drive it "
            "from the host instead, or deploy a worker that serves /irtt."
        )
        return None
    try:
        session = _irtt_host_start(int(duration_s))
        print(
            f"    · IRTT started via HOST FALLBACK (nsenter into "
            f"{IRTT_WORKER_CONTAINER}'s ns1/ns2), {IRTT_SERVER_ADDR}:{IRTT_PORT}, "
            f"every {IRTT_INTERVAL_MS} ms"
        )
        return session
    except Exception as exc:  # noqa: BLE001 - a probe must never fail the run
        print(f"    ! IRTT host fallback failed: {type(exc).__name__}: {exc}")
        return None


def substrate_irtt_stop(session: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Stop the probe and return its raw result, or None if there was none."""
    if not session:
        return None
    if session.get("transport") == "host_nsenter":
        try:
            return _irtt_host_stop(session)
        except Exception as exc:  # noqa: BLE001
            print(f"    ! IRTT stop (host) failed: {type(exc).__name__}: {exc}")
            return {"transport": "host_nsenter", "error": str(exc), "result": None}
    irtt_id = session.get("irtt_id")
    if not irtt_id:
        return None
    try:
        r = requests.delete(f"{SUBSTRATE}/irtt/{irtt_id}", timeout=HTTP_TIMEOUT * 4)
        r.raise_for_status()
        data = r.json()
        data["transport"] = "worker_endpoint"
        return data
    except requests.RequestException as exc:
        print(f"    ! IRTT stop failed: {type(exc).__name__}: {exc}")
        return {"transport": "worker_endpoint", "error": str(exc), "result": None}


def irtt_probe_endpoints(
    irtt_record: Optional[dict[str, Any]],
) -> dict[str, set[str]]:
    """{probe: {ip}} for attribution, from a finished probe's own report.

    Taken from what the probe reports rather than from configuration, so the
    addresses registered as ground truth are the ones actually used. An empty
    map when the probe did not run keeps attribution identical to a run without
    it.
    """
    if not irtt_record:
        return {}
    ips = {ip for ip in (irtt_record.get("endpoint_ips") or []) if ip}
    if not ips:
        return {}
    return {irtt_record.get("probe") or IRTT_PROBE_NAME: ips}


def _ns_to_ms(v: Any) -> Optional[float]:
    """irtt reports durations in nanoseconds; the record uses milliseconds."""
    try:
        return round(float(v) / 1e6, 4)
    except (TypeError, ValueError):
        return None


def summarize_irtt(stopped: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Reduce irtt's JSON to a record in the same shape as the rest.

    Durations are converted to milliseconds and named `_ms`, because every other
    latency in this project is in milliseconds and a bare number in nanoseconds
    beside them would be read as one.
    """
    if not stopped:
        return None
    out: dict[str, Any] = {
        "probe": IRTT_PROBE_NAME,
        "transport": stopped.get("transport"),
        "server_address": stopped.get("server_address"),
        "port": stopped.get("port"),
        "endpoint_ips": stopped.get("endpoint_ips") or [],
        "interval_ms": IRTT_INTERVAL_MS,
        "client_returncode": stopped.get("client_returncode"),
        "error": stopped.get("error"),
        "status": (
            "ok" if (stopped.get("result") and not stopped.get("error")) else "failed"
        ),
    }
    raw = stopped.get("result") or {}
    stats = raw.get("stats") or {}
    if not stats:
        return out

    rtt = stats.get("rtt") or {}
    out["round_trip_ms"] = {
        k: _ns_to_ms(rtt.get(k))
        for k in ("min", "mean", "median", "max", "stddev")
        if rtt.get(k) is not None
    }
    out["one_way_ms"] = {
        "send_mean": _ns_to_ms((stats.get("send_delay") or {}).get("mean")),
        "receive_mean": _ns_to_ms((stats.get("receive_delay") or {}).get("mean")),
    }
    out["jitter_ms"] = {
        "round_trip_mean": _ns_to_ms((stats.get("ipdv_round_trip") or {}).get("mean")),
        "send_mean": _ns_to_ms((stats.get("ipdv_send") or {}).get("mean")),
        "receive_mean": _ns_to_ms((stats.get("ipdv_receive") or {}).get("mean")),
    }
    out["packets"] = {
        "sent": stats.get("packets_sent"),
        "received": stats.get("packets_received"),
        "loss_percent": stats.get("packet_loss_percent"),
        "upstream_loss_percent": stats.get("upstream_loss_percent"),
        "downstream_loss_percent": stats.get("downstream_loss_percent"),
        "duplicates": stats.get("duplicates"),
        "late": stats.get("late_packets"),
    }
    out["bytes"] = {
        "sent": stats.get("bytes_sent"),
        "received": stats.get("bytes_received"),
    }
    out["duration_ms"] = _ns_to_ms(stats.get("duration"))
    # The probe's own send timing. If the sender missed its schedule the
    # latency numbers describe the sender, not the link, so this is reported
    # rather than left in the raw blob.
    out["timer"] = {
        "error_percent": stats.get("timer_err_percent"),
        "miss_percent": stats.get("timer_miss_percent"),
        "misses": stats.get("timer_misses"),
    }
    out["irtt_version"] = (raw.get("version") or {}).get("irtt")
    return out


# ═════════════════════════════════════════════════════════════════════════════
#  NetVibe — the lab's own continuous-QoE probe, run beside the app under test
#
#  Another probe, not an app: it reuses the `probe_endpoint_ips` machinery IRTT
#  introduced, so nothing about app attribution changes. Two things are specific
#  to it.
#
#  Direction convention is NetVibe's, which is the opposite of the intuitive
#  reading: the PROBE SERVER sends and the sensor echoes, so in
#  `pkg/analyzer/clock.go` the raw one-way values are
#  `[down] t2-t1, [up] t4-t3`. t1/t4 are the probe server's clock, t2/t3 the
#  sensor's.
#
#  Absolute one-way delay is NOT available. bench records t2/t3 "as the engine
#  carries them" -- the sensor's own time base, not the epoch -- so only the
#  per-direction *variation* above each direction's own floor is meaningful.
#  That variation is the queueing delay, which is what NetVibe itself reports;
#  the round trip is absolute because its two unknowns cancel.
# ═════════════════════════════════════════════════════════════════════════════
NETVIBE_ENABLED = os.environ.get("PRAMANA_NETVIBE", "0").strip() not in (
    "",
    "0",
    "false",
)
NETVIBE_PROBE_NAME = "netvibe"
# sun_path, including its NUL. A probe server binds a Unix socket under
# state_dir, so state_dir has to stay well clear of this.
_UNIX_SOCKET_PATH_MAX = 108
# ns2's veth3: bench's sensor API and its probe server's media both live here,
# so every packet of the probe crosses veth2 then veth4.
NETVIBE_SERVER_ADDR = os.environ.get("PRAMANA_NETVIBE_SERVER", "172.16.2.1")
NETVIBE_SIGNALING_PORT = int(os.environ.get("PRAMANA_NETVIBE_SIGNALING_PORT", "9999"))
NETVIBE_MEDIA_PORT = int(os.environ.get("PRAMANA_NETVIBE_MEDIA_PORT", "10443"))
NETVIBE_STUB_API_PORT = int(os.environ.get("PRAMANA_NETVIBE_STUB_API_PORT", "9898"))
NETVIBE_RATE = float(os.environ.get("PRAMANA_NETVIBE_RATE", "10"))
NETVIBE_SENSOR_ID = os.environ.get("PRAMANA_NETVIBE_SENSOR", "pramana-ns1")
# Escalation. Off by default; when on, the ladder is NetVibe's own and the
# trigger is deliberately easy to fire so a short run exercises it.
NETVIBE_ESCALATE = os.environ.get("PRAMANA_NETVIBE_ESCALATE", "0").strip() not in (
    "",
    "0",
    "false",
)
NETVIBE_ESCALATION_RATES = os.environ.get("PRAMANA_NETVIBE_ESCALATION_RATES", "20")
NETVIBE_TRIGGER_DELAY_MS = int(os.environ.get("PRAMANA_NETVIBE_TRIGGER_DELAY_MS", "5"))
NETVIBE_BIN_DIR = os.environ.get("PRAMANA_NETVIBE_BIN_DIR", "")
# Which NetVibe source the binaries were built from. bench stamps this itself
# from its own git metadata, but a binary built from a tree without `.git`
# stamps the literal "dev" -- measured on the verification run. The probe's
# send timing and record schema come from that source, so the run must not be
# left unable to say which one. The deployed path gets it from the image's
# pinned NETVIBE_COMMIT; this is for anything built by hand.
NETVIBE_COMMIT = os.environ.get("PRAMANA_NETVIBE_COMMIT", "")
NETVIBE_HOST_FALLBACK = os.environ.get(
    "PRAMANA_NETVIBE_HOST_FALLBACK", "0"
).strip() not in ("", "0", "false")
NETVIBE_WORKER_CONTAINER = os.environ.get(
    "PRAMANA_NETVIBE_CONTAINER", "substrate-worker"
)

# The sensor asks the API whether a speed test is due and, with no API to ask,
# decides to run one. An NDT7 test would saturate the very link being measured,
# so the probe is run against a stub that answers "not due". Suppressing it by
# hoping m-lab is unreachable is not a control.
_NETVIBE_STUB_API = r"""
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
        # GET /networks/{id}/speedtests/latest -- the sensor's due check.
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


def _netvibe_bin(name: str) -> str:
    """Path to a netvibe binary, or just its name to be found on PATH."""
    if NETVIBE_BIN_DIR:
        return str(Path(NETVIBE_BIN_DIR) / name)
    return shutil.which(name) or name


def netvibe_bench_config(
    out_sqlite: Path, state_dir: Path, in_namespace: bool
) -> dict[str, Any]:
    """The bench.yaml body for one run.

    `in_namespace` says whether bench itself is already inside ns2. When it is
    not -- the worker path -- the probe server is placed there with bench's own
    `netns` key, which is how upstream means it to be done. The host fallback
    runs bench inside ns2 instead, because `netns` resolves `/run/netns/NAME`
    against the caller's mount namespace and ns1/ns2 live inside the worker
    container's.
    """
    server: dict[str, Any] = {
        "label": "pramana",
        "media_port": NETVIBE_MEDIA_PORT,
        "public_ip": NETVIBE_SERVER_ADDR,
    }
    if not in_namespace:
        server["netns"] = "ns2"
    probe: dict[str, Any] = {
        "name": "pramana-fixed",
        "rate_per_second": NETVIBE_RATE,
        "spacing": "fixed",
        "max_gap_ms": 2000,
        "echo_timeout_ms": 5000,
    }
    if NETVIBE_ESCALATE:
        rungs = [
            float(r.strip()) for r in NETVIBE_ESCALATION_RATES.split(",") if r.strip()
        ]
        probe.update(
            {
                "name": "pramana-escalating",
                "escalation_rates": rungs,
                "trigger_delay_ms": NETVIBE_TRIGGER_DELAY_MS,
                "trigger_window_ms": 2000,
                "trigger_percentile": 90,
                "rung_hold_ms": 2000,
                "cooldown_ms": 5000,
                "max_escalated_ms": 600000,
            }
        )
    return {
        "out": str(out_sqlite),
        "listen": f"{NETVIBE_SERVER_ADDR}:{NETVIBE_SIGNALING_PORT}",
        "state_dir": str(state_dir),
        "public_ip": NETVIBE_SERVER_ADDR,
        "session_seconds": 86400,
        "probe_servers": [server],
        "probe": probe,
    }


def _netvibe_netns_path(namespace: str) -> str:
    """Host path to one of the worker's network namespaces."""
    pid = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Pid}}", NETVIBE_WORKER_CONTAINER],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if pid.returncode != 0 or not pid.stdout.strip().isdigit():
        raise RuntimeError(
            f"could not read pid of container {NETVIBE_WORKER_CONTAINER!r}: "
            f"{pid.stderr.strip()[:200]}"
        )
    return f"/proc/{pid.stdout.strip()}/root/run/netns/{namespace}"


def _netvibe_host_start(run_dir: Path, duration_s: int) -> dict[str, Any]:
    """Run bench in ns2 and the sensor in ns1, from the host."""
    import yaml  # bench reads YAML; only needed on this path

    work = run_dir / "netvibe"
    work.mkdir(parents=True, exist_ok=True)
    out_sqlite = work / "bench.sqlite"
    if out_sqlite.exists():
        raise RuntimeError(
            f"{out_sqlite} already exists; bench refuses to append to an old "
            f"run, so the previous run's file must be moved aside first"
        )
    # NOT under the run directory, and NOT created here.
    #
    # Not created, because bench makes it 0700 and refuses a directory another
    # user owns, and bench runs as root through sudo.
    #
    # Not under the run directory, because each probe server binds a Unix
    # socket at `<state_dir>/<label>.sock` and sun_path is 108 bytes. A run
    # directory named after its scenario pushes that over: measured, a real run
    # produced a 138-byte path and the probe server died with
    # `bind: invalid argument`, which surfaced only as a readiness timeout.
    state_dir = Path(tempfile.gettempdir()) / f"nv_{uuid.uuid4().hex[:8]}"
    sock_len = len(str(state_dir / "pramana.sock").encode()) + 1
    if sock_len > _UNIX_SOCKET_PATH_MAX:
        raise RuntimeError(
            f"bench's probe-server socket path would be {sock_len} bytes, over "
            f"the {_UNIX_SOCKET_PATH_MAX}-byte sun_path limit: "
            f"{state_dir / 'pramana.sock'}. Set TMPDIR to something shorter."
        )
    cfg = netvibe_bench_config(out_sqlite, state_dir, in_namespace=True)
    cfg_path = work / "bench.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))

    ns1 = _netvibe_netns_path("ns1")
    ns2 = _netvibe_netns_path("ns2")
    bench_bin = _netvibe_bin("netvibe-bench")
    cli_bin = _netvibe_bin("netvibe-cli")

    # The stub speed-test API, in ns2 beside bench so the sensor can reach it.
    stub_src = work / "stub_api.py"
    stub_src.write_text(_NETVIBE_STUB_API)
    stub = subprocess.Popen(
        [
            "sudo",
            "-n",
            "nsenter",
            f"--net={ns2}",
            sys.executable,
            str(stub_src),
            NETVIBE_SERVER_ADDR,
            str(NETVIBE_STUB_API_PORT),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    bench_log = open(work / "bench.log", "wb")
    bench = subprocess.Popen(
        [
            "sudo",
            "-n",
            "timeout",
            "-k",
            "5",
            str(duration_s + 180),
            "nsenter",
            f"--net={ns2}",
            bench_bin,
            "--config",
            str(cfg_path),
        ],
        stdout=bench_log,
        stderr=subprocess.STDOUT,
    )

    # Readiness from bench itself rather than a sleep: /ready reports which
    # probe servers are down, so a server that failed to bind is an explicit
    # failure instead of a sensor that cannot connect for unclear reasons.
    deadline = time.time() + 45.0
    ready, last = False, ""
    while time.time() < deadline:
        if bench.poll() is not None:
            _netvibe_kill(stub)
            raise RuntimeError(
                "netvibe-bench exited during startup; see " f"{work / 'bench.log'}"
            )
        probe = subprocess.run(
            [
                "sudo",
                "-n",
                "nsenter",
                f"--net={ns2}",
                "curl",
                "-s",
                "-m",
                "3",
                f"http://{NETVIBE_SERVER_ADDR}:{NETVIBE_SIGNALING_PORT}/ready",
            ],
            capture_output=True,
            timeout=15,
        )
        body = probe.stdout.decode(errors="replace").strip()
        if probe.returncode == 0 and '"ready":true' in body.replace(" ", ""):
            ready = True
            break
        last = body or probe.stderr.decode(errors="replace").strip()[:160]
        time.sleep(1.0)
    if not ready:
        _netvibe_kill(bench)
        _netvibe_kill(stub)
        raise RuntimeError(
            f"netvibe-bench did not report ready within 45s: {last[:200]}"
        )

    sensor_log = open(work / "sensor.log", "wb")
    sensor = subprocess.Popen(
        [
            "sudo",
            "-n",
            "timeout",
            "-k",
            "5",
            str(duration_s + 120),
            "nsenter",
            f"--net={ns1}",
            cli_bin,
            "test",
            "--signaling-host",
            f"http://{NETVIBE_SERVER_ADDR}",
            "--signaling-port",
            str(NETVIBE_SIGNALING_PORT),
            "--api-host",
            f"http://{NETVIBE_SERVER_ADDR}",
            "--api-port",
            str(NETVIBE_STUB_API_PORT),
            "--sensor-id",
            NETVIBE_SENSOR_ID,
            # Host candidates only: a STUN-derived candidate could put the media
            # on a path that does not cross the shaped link at all.
            "--no-stun",
        ],
        stdout=sensor_log,
        stderr=subprocess.STDOUT,
    )
    return {
        "transport": "host_nsenter",
        "bench_proc": bench,
        "sensor_proc": sensor,
        "stub_proc": stub,
        "logs": [bench_log, sensor_log],
        "out_sqlite": str(out_sqlite),
        "work_dir": str(work),
        "state_dir": str(state_dir),
        "server_address": NETVIBE_SERVER_ADDR,
        "media_port": NETVIBE_MEDIA_PORT,
        "endpoint_ips": [NETVIBE_SERVER_ADDR],
    }


def _netvibe_kill(proc: "subprocess.Popen") -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()


def _netvibe_host_stop(session: dict[str, Any]) -> dict[str, Any]:
    """Stop the sensor, then bench, then read the run's SQLite file.

    Order matters: bench writes `meta.stopped_at_ns` and flushes its servers'
    records on SIGTERM, and the file is only complete once it exits. Killing it
    first would lose the tail of the run.
    """
    _netvibe_kill(session.get("sensor_proc"))
    bench = session.get("bench_proc")
    if bench is not None and bench.poll() is None:
        bench.terminate()
        try:
            bench.wait(timeout=30)
        except subprocess.TimeoutExpired:
            bench.kill()
    _netvibe_kill(session.get("stub_proc"))
    for fh in session.get("logs") or []:
        try:
            fh.close()
        except Exception:  # noqa: BLE001
            pass
    # bench runs as root, so its file and state dir are root-owned. Hand the
    # file back so the rest of the pipeline (and the user) can read it.
    out = session.get("out_sqlite")
    if out:
        subprocess.run(
            [
                "sudo",
                "-n",
                "chown",
                "-R",
                f"{os.getuid()}:{os.getgid()}",
                session.get("work_dir") or out,
            ],
            capture_output=True,
            timeout=60,
        )
    # state_dir sits outside the run directory (see _netvibe_host_start), so
    # removing it is this function's job. Root-owned, hence sudo.
    state_dir = session.get("state_dir")
    if state_dir and str(state_dir).startswith(tempfile.gettempdir()):
        subprocess.run(
            ["sudo", "-n", "rm", "-rf", str(state_dir)],
            capture_output=True,
            timeout=60,
        )
    return {
        "transport": "host_nsenter",
        "out_sqlite": out,
        "work_dir": session.get("work_dir"),
        "server_address": session.get("server_address"),
        "media_port": session.get("media_port"),
        "endpoint_ips": session.get("endpoint_ips") or [],
    }


def substrate_netvibe_start(run_dir: Path, duration_s: int) -> Optional[dict[str, Any]]:
    """Start the NetVibe probe. Returns a session handle, or None.

    Tries the worker endpoint first; a 404 means the deployed image predates it.
    The host fallback is used only when explicitly enabled, and the path that
    produced the numbers is recorded on the run either way.
    """
    payload = {
        "duration_seconds": int(duration_s),
        "rate_per_second": NETVIBE_RATE,
        "server_address": NETVIBE_SERVER_ADDR,
        "signaling_port": NETVIBE_SIGNALING_PORT,
        "media_port": NETVIBE_MEDIA_PORT,
        "sensor_id": NETVIBE_SENSOR_ID,
        "escalate": NETVIBE_ESCALATE,
    }
    try:
        r = requests.post(
            f"{SUBSTRATE}/netvibe", json=payload, timeout=HTTP_TIMEOUT * 4
        )
        if r.status_code != 404:
            r.raise_for_status()
            data = r.json()
            data["transport"] = "worker_endpoint"
            print(
                f"    · NetVibe started via worker endpoint "
                f"(id={str(data.get('netvibe_id', '?'))[:8]}, "
                f"{NETVIBE_SERVER_ADDR}:{NETVIBE_MEDIA_PORT})"
            )
            return data
        print(
            "    ! worker has no POST /netvibe (404) — its image predates this "
            "endpoint and needs rebuilding from services/substrate-worker"
        )
    except requests.RequestException as exc:
        print(f"    ! NetVibe start via worker failed: {type(exc).__name__}: {exc}")
        return None

    if not NETVIBE_HOST_FALLBACK:
        print(
            "    ! NetVibe skipped. Set PRAMANA_NETVIBE_HOST_FALLBACK=1 to drive "
            "it from the host, or deploy a worker that serves /netvibe."
        )
        return None
    try:
        session = _netvibe_host_start(run_dir, int(duration_s))
        print(
            f"    · NetVibe started via HOST FALLBACK: bench in ns2 "
            f"({NETVIBE_SERVER_ADDR}:{NETVIBE_SIGNALING_PORT}, media "
            f"{NETVIBE_MEDIA_PORT}), sensor in ns1, {NETVIBE_RATE:g}/s"
            + (", escalation on" if NETVIBE_ESCALATE else "")
        )
        return session
    except Exception as exc:  # noqa: BLE001 - a probe must never fail the run
        print(f"    ! NetVibe host fallback failed: {type(exc).__name__}: {exc}")
        return None


def substrate_netvibe_stop(
    session: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Stop the probe and return where its records landed."""
    if not session:
        return None
    if session.get("transport") == "host_nsenter":
        try:
            return _netvibe_host_stop(session)
        except Exception as exc:  # noqa: BLE001
            print(f"    ! NetVibe stop (host) failed: {type(exc).__name__}: {exc}")
            return {"transport": "host_nsenter", "error": str(exc)}
    netvibe_id = session.get("netvibe_id")
    if not netvibe_id:
        return None
    try:
        r = requests.delete(
            f"{SUBSTRATE}/netvibe/{netvibe_id}", timeout=HTTP_TIMEOUT * 6
        )
        r.raise_for_status()
        data = r.json()
        data["transport"] = "worker_endpoint"
        return data
    except requests.RequestException as exc:
        print(f"    ! NetVibe stop failed: {type(exc).__name__}: {exc}")
        return {"transport": "worker_endpoint", "error": str(exc)}


def _quantiles_ms(values_us: list[float]) -> dict[str, Optional[float]]:
    """min/mean/median/p95/max of microsecond values, reported in ms."""
    if not values_us:
        return {k: None for k in ("min", "mean", "median", "p95", "max")}
    s = sorted(values_us)
    n = len(s)
    return {
        "min": round(s[0] / 1000.0, 4),
        "mean": round(sum(s) / n / 1000.0, 4),
        "median": round(s[n // 2] / 1000.0, 4),
        "p95": round(s[min(n - 1, int(0.95 * n))] / 1000.0, 4),
        "max": round(s[-1] / 1000.0, 4),
    }


def summarize_netvibe(stopped: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Reduce bench's SQLite run into a record, per direction.

    Direction names are NetVibe's own (`pkg/analyzer/clock.go`): the probe
    SERVER sends and the sensor echoes, so `t2 - t1` is the DOWNstream leg
    (ns2 -> ns1) and `t4 - t3` is the UPstream leg (ns1 -> ns2).

    What is reported per direction is QUEUEING -- each leg's delay above its own
    floor over the run -- not absolute one-way delay. bench stores t2/t3 on the
    sensor's own time base rather than the epoch, so the absolute legs are not
    recoverable from the file; their difference is, which is why the round trip
    is reported absolutely. NetVibe's analyzer does the same thing properly,
    with a lower-envelope fit per clock segment; this is the first-order version
    of it and is labelled as such.
    """
    if not stopped:
        return None
    out: dict[str, Any] = {
        "probe": NETVIBE_PROBE_NAME,
        "transport": stopped.get("transport"),
        "server_address": stopped.get("server_address"),
        "media_port": stopped.get("media_port"),
        "endpoint_ips": stopped.get("endpoint_ips") or [],
        "rate_per_second_configured": NETVIBE_RATE,
        "escalation_configured": NETVIBE_ESCALATE,
        "error": stopped.get("error"),
        "status": "failed",
        "sqlite": stopped.get("out_sqlite"),
        "delay_basis": (
            "per-direction queueing above each leg's own floor; absolute "
            "one-way delay is not recoverable because t2/t3 are on the "
            "sensor's time base. Round trip is absolute."
        ),
    }
    path = stopped.get("out_sqlite")
    if not path or not Path(path).exists():
        out["error"] = out["error"] or f"no bench SQLite file at {path!r}"
        return out

    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        out["error"] = f"bench file unreadable: {exc}"
        return out
    try:
        rows = con.execute(
            "select t1_ns, t2_us, t3_us, t4_ns, rate_per_second "
            "from probe_records where status = 'ok'"
        ).fetchall()
        out["records"] = {
            "total": con.execute("select count(*) from probe_records").fetchone()[0],
            "ok": len(rows),
        }
        out["records"]["by_status"] = {
            str(k): int(v)
            for k, v in con.execute(
                "select status, count(*) from probe_records group by 1"
            ).fetchall()
        }
        sessions = con.execute(
            "select probes_sent, ok, lost_down, lost_up, lost_unknown, duplicates, "
            "late_echoes, end_reason from probe_sessions"
        ).fetchall()
        out["sessions"] = [
            {
                "probes_sent": s[0],
                "ok": s[1],
                "lost_down": s[2],
                "lost_up": s[3],
                "lost_unknown": s[4],
                "duplicates": s[5],
                "late_echoes": s[6],
                "end_reason": s[7],
            }
            for s in sessions
        ]
        out["rate_changes"] = [
            {"t_ns": r[0], "rate_per_second": r[1], "rung": r[2], "reason": str(r[3])}
            for r in con.execute(
                "select t_ns, rate_per_second, rung, reason "
                "from probe_rate_changes order by t_ns"
            ).fetchall()
        ]
        try:
            stamped = (
                con.execute(
                    "select value from meta where key = 'netvibe_commit'"
                ).fetchone()
                or [None]
            )[0]
        except sqlite3.Error:
            stamped = None
        out["netvibe_commit"] = stamped
        # "dev" means the binary was built from a tree with no git metadata, so
        # the file cannot say what it came from. Fall back to what the operator
        # declared rather than leaving the run unattributable.
        if (not stamped or stamped == "dev") and NETVIBE_COMMIT:
            out["netvibe_commit"] = NETVIBE_COMMIT
            out["netvibe_commit_source"] = "PRAMANA_NETVIBE_COMMIT"
        elif stamped and stamped != "dev":
            out["netvibe_commit_source"] = "stamped by bench"
        else:
            out["netvibe_commit_source"] = "unknown"
    except sqlite3.Error as exc:
        out["error"] = f"bench file query failed: {exc}"
        return out
    finally:
        con.close()

    if not rows:
        out["error"] = out["error"] or "bench recorded no answered probes"
        return out

    # NetVibe's own direction convention; see the docstring.
    down_us = [t2 - t1 / 1000.0 for t1, t2, _t3, _t4, _r in rows]
    up_us = [t4 / 1000.0 - t3 for _t1, _t2, t3, t4, _r in rows]
    rtt_us = [(t4 - t1) / 1000.0 - (t3 - t2) for t1, t2, t3, t4, _r in rows]
    down_floor, up_floor = min(down_us), min(up_us)
    out["queueing_ms"] = {
        "downstream_ns2_to_ns1": _quantiles_ms([v - down_floor for v in down_us]),
        "upstream_ns1_to_ns2": _quantiles_ms([v - up_floor for v in up_us]),
    }
    out["round_trip_ms"] = _quantiles_ms(rtt_us)
    rates = sorted({float(r) for *_x, r in rows if r is not None})
    out["rates_observed"] = rates
    out["escalated"] = len(rates) > 1 or bool(out.get("rate_changes"))
    out["status"] = "ok"
    return out


def netvibe_probe_endpoints(
    netvibe_record: Optional[dict[str, Any]],
) -> dict[str, set[str]]:
    """{probe: {ip}} for attribution, from what the probe itself reported."""
    if not netvibe_record:
        return {}
    ips = {ip for ip in (netvibe_record.get("endpoint_ips") or []) if ip}
    if not ips:
        return {}
    return {netvibe_record.get("probe") or NETVIBE_PROBE_NAME: ips}


# ── the shaped-path guard ────────────────────────────────────────────────────
# The worker's root namespace carries a /32 host route to ns1 over the
# management link (`172.16.1.1 via 10.200.0.2 dev mgmt0`) that is MORE specific
# than the veth2 connected route. Measured, the probe does cross veth2 and
# veth4 in both directions and mgmt0 carries none of it -- but nothing in the
# topology guarantees that, and if it ever flipped, the downstream leg would
# bypass both the shaper and the capture while still producing plausible
# numbers. That is the one failure we cannot have go unnoticed, so it is
# checked against the captures rather than assumed.
def probe_path_evidence(
    pcaps: dict[str, Path], probe_ips: Iterable[str]
) -> dict[str, Any]:
    """Per-interface, per-direction probe packet counts from the captures."""
    wanted = {ip for ip in probe_ips if ip}
    out: dict[str, Any] = {}
    for iface, pcap in (pcaps or {}).items():
        seen = {"to_probe": 0, "from_probe": 0, "other": 0}
        if pcap and Path(pcap).exists():
            for _ts, _ol, lt, data in iter_capture(str(pcap)):
                fam, l3 = _l3(lt, data)
                if fam is None:
                    continue
                parsed = _parse_ip(fam, l3)
                if parsed is None:
                    continue
                src, dst, _proto, _l4 = parsed
                if dst in wanted:
                    seen["to_probe"] += 1
                elif src in wanted:
                    seen["from_probe"] += 1
                else:
                    seen["other"] += 1
        out[iface] = seen
    return out


def probe_path_strict() -> bool:
    """Whether a failed path check should invalidate the run. On by default."""
    return os.environ.get("PRAMANA_PROBE_PATH_STRICT", "1").strip() not in (
        "",
        "0",
        "false",
    )


def check_probe_path(
    pcaps: dict[str, Path],
    probe_ips: Iterable[str],
    required_ifaces: Iterable[str] = ("veth2", "veth4"),
    enforce: bool = True,
) -> dict[str, Any]:
    """Fail loudly unless the probe crossed every required interface, both ways.

    `enforce=False` collects and prints the same verdict without raising, so a
    caller can write the run's artifacts first and abort afterwards -- the
    captures are the evidence for diagnosing the failure, so losing them to the
    abort would be perverse. The recorded `strict` field is the configured
    policy either way, never the caller's choice of when to raise.
    """
    strict = probe_path_strict()
    evidence = probe_path_evidence(pcaps, probe_ips)
    problems: list[str] = []
    for iface in required_ifaces:
        seen = evidence.get(iface)
        if seen is None:
            problems.append(f"{iface}: no capture was taken")
            continue
        if not seen["to_probe"] and not seen["from_probe"]:
            problems.append(f"{iface}: no probe packets at all")
            continue
        if not seen["to_probe"]:
            problems.append(f"{iface}: nothing toward the probe (upstream missing)")
        if not seen["from_probe"]:
            problems.append(
                f"{iface}: nothing from the probe (downstream missing — the "
                f"mgmt0 host route may be winning)"
            )
    result = {
        "required_interfaces": list(required_ifaces),
        "probe_ips": sorted({ip for ip in probe_ips if ip}),
        "evidence": evidence,
        "ok": not problems,
        "problems": problems,
        "strict": bool(strict),
    }
    if problems:
        message = (
            "probe traffic did not cross the shaped path as required: "
            + "; ".join(problems)
            + ". A probe that bypasses veth2/veth4 is measuring a different "
            "link from the one under test."
        )
        print(f"  ! {message}")
        if strict and enforce:
            raise RuntimeError(message)
    else:
        parts = ", ".join(
            f"{i}: {evidence[i]['to_probe']}↑/{evidence[i]['from_probe']}↓"
            for i in required_ifaces
            if i in evidence
        )
        print(f"    · probe path verified ({parts})")
    return result


def app_endpoint_ips_from_url_map(
    app_urls: Optional[dict[str, str]],
) -> dict[str, set[str]]:
    """{app: {ip}} for any app whose URL names a literal IP rather than a host.

    Used for targets we run ourselves (the WebRTC peer server), which are
    reached by address and therefore leave no DNS or SNI trace to attribute by.

    Takes a plain mapping rather than an ExperimentConfig so a release rebuild,
    which only has the serialised config out of record.json, can derive exactly
    the same ground truth as the live run.
    """
    from urllib.parse import urlparse

    out: dict[str, set[str]] = {}
    for app, url in (app_urls or {}).items():
        host = urlparse(url or "").hostname or ""
        try:
            ipaddress.ip_address(host)
        except ValueError:
            continue
        out.setdefault(app, set()).add(host)
    return out


def app_endpoint_ips_from_urls(cfg: "ExperimentConfig") -> dict[str, set[str]]:
    """{app: {ip}} from an ExperimentConfig's URLs."""
    return app_endpoint_ips_from_url_map(cfg.app_urls)


# The application client's address inside ns1. This is the substrate-worker
# topology convention documented in services/substrate-worker/README.md
# (veth1 172.16.1.1/30 ↔ veth2 172.16.1.2/30) and already relied on by
# services/analysis/pcap_analysis.py:APP_CLIENT_IP_DEFAULT. Not imported from
# there because that module pulls in scapy, which this one deliberately avoids
# in favour of its own streaming pcap reader.
#
# Override for a different topology with PRAMANA_CLIENT_IPS="ip[,ip...]".
#
# FOLLOW-UP: this duplicates services/analysis/pcap_analysis.py's
# APP_CLIENT_IP_DEFAULT. Worth collapsing into one shared topology constant,
# which means first moving it somewhere neither module has to import scapy to
# reach. Left as two copies deliberately rather than importing scapy here.
CLIENT_IPS_DEFAULT = ("172.16.1.1",)


def client_ips_for_capture() -> list[str]:
    """Addresses the capture vantage sits behind, as ground truth for direction.

    Capture happens on `veth2`, the root↔ns1 leg, so the client side is ns1.
    Declaring it removes the guesswork that breaks down the moment the far end
    is also RFC1918 — see `attribute_capture`.
    """
    raw = os.environ.get("PRAMANA_CLIENT_IPS", "")
    if raw.strip():
        out: list[str] = []
        for part in raw.split(","):
            ip = part.strip()
            if not ip:
                continue
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                raise ValueError(
                    f"PRAMANA_CLIENT_IPS contains {ip!r}, which is not an IP "
                    f"address. Direction attribution depends on this being "
                    f"exact, so it is not silently ignored."
                )
            out.append(ip)
        return out
    return list(CLIENT_IPS_DEFAULT)


def app_endpoint_ips_from_webrtc_stats(
    run_dir: Path, apps: Iterable[str]
) -> dict[str, set[str]]:
    """{app: {ip}} from the ICE pair the call actually used.

    A room URL names a web server, not the media path. WebRTC negotiates its
    own transport address, and for a peer inside our namespaces that address is
    RFC1918 and unrelated to whatever the URL resolved to — so attributing by
    the URL's address misses the media entirely. The collector reports the
    selected candidate pair per sample; this reads the negotiated remote
    address back out.

    Addresses that are not literal IPs are skipped and counted: Chrome
    publishes host candidates as mDNS `<uuid>.local` names unless the page
    holds a media permission, and a name cannot be matched against a capture.
    """
    out: dict[str, set[str]] = {}
    for app in apps:
        f = run_dir / "qoe" / f"{app}_stats.jsonl"
        if not f.exists() or f.stat().st_size == 0:
            continue
        found: set[str] = set()
        mdns = 0
        unparsable = 0
        for line in f.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                sample = json.loads(line)
            except (ValueError, TypeError):
                unparsable += 1
                continue
            stats = sample.get("stats")
            if not isinstance(stats, dict):
                continue
            addr = stats.get("selected_remote_address")
            if not addr or not isinstance(addr, str):
                continue
            try:
                ipaddress.ip_address(addr)
            except ValueError:
                if addr.endswith(".local"):
                    mdns += 1
                else:
                    unparsable += 1
                continue
            found.add(addr)
        if mdns:
            print(
                f"    ! {app}: ICE reported an mDNS candidate name on {mdns} "
                f"sample(s) instead of an address, so those cannot be matched "
                f"against the capture"
            )
        if unparsable:
            print(
                f"    ! {app}: {unparsable} stats sample(s) carried an "
                f"unreadable selected-candidate address"
            )
        if found:
            out[app] = found
            print(
                f"    · {app}: ICE negotiated {sorted(found)} "
                f"(used as attribution ground truth)"
            )
    return out


def merge_endpoint_ips(
    *sources: Optional[dict[str, set[str]]],
) -> dict[str, set[str]]:
    """Union of several {app: {ip}} maps, skipping None."""
    out: dict[str, set[str]] = {}
    for source in sources:
        for app, ips in (source or {}).items():
            if ips:
                out.setdefault(app, set()).update(ips)
    return out


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

    # Bots join before the capture opens. Admission can take minutes (and may be
    # manual), and every second of it would otherwise be captured as idle link
    # time, diluting the average throughput of the call we actually want.
    prepared_bots, prepared_bot_failures = start_bot_peers(cfg, run_dir)
    if prepared_bots:
        prepared_bot_failures.update(
            wait_for_bot_peers(prepared_bots, timeout_s=float(cfg.peer_wait_s or 900))
        )

    # The capture timer starts here, but the app under test still has to launch,
    # navigate and - for a conferencing app - be admitted by a human, all inside
    # this window. Measured on a six-run Meet sweep: admission took 210s and
    # 287s on two runs while the window was 180s, so the capture expired before
    # the call began and those pcaps recorded only the idle setup period. When a
    # peer is configured, the window has to carry that wait too.
    _join_budget = int(cfg.peer_wait_s) if any(_peer_spec(cfg, a) for a in apps) else 0
    cap_duration = (
        cfg.duration_s + CAPTURE_OVERHEAD_S + _join_budget + 15 * (len(apps) - 1)
    )
    print(f"  • capture on {DOWNSTREAM_IFACE} (auto-stop {cap_duration}s) ...")
    cap = substrate_capture_start(cfg.slug, duration_s=cap_duration)
    capture_id = cap["capture_id"]

    # A second capture on the upstream leg. Jaber's NetVibe spec asks for a
    # pcap per interface, and it is also the evidence for the shaped-path
    # guard: a probe that crossed only one of the two was not on the path
    # under test. Only taken when a probe needs it, so an ordinary run still
    # ships exactly one capture.
    upstream_capture_id = None
    if NETVIBE_ENABLED:
        try:
            up_cap = substrate_capture_start_on(
                UPSTREAM_IFACE, f"{cfg.slug}_upstream", duration_s=cap_duration
            )
            upstream_capture_id = up_cap["capture_id"]
            print(f"  • capture on {UPSTREAM_IFACE} (probe path evidence) ...")
        except Exception as exc:  # noqa: BLE001
            print(
                f"  ! second capture on {UPSTREAM_IFACE} failed "
                f"({type(exc).__name__}: {exc}); the shaped-path guard cannot "
                f"run without it"
            )

    # The probe starts inside the capture window and runs alongside the app,
    # not after it. It is deliberately started AFTER the capture so every probe
    # packet is in the pcap, and stopped BEFORE the download so none is in
    # flight while the capture is being finalised.
    irtt_session = None
    if IRTT_ENABLED:
        print("  • IRTT probe (ns1 → ns2, concurrent with the app) ...")
        irtt_session = substrate_irtt_start(cfg.duration_s)

    netvibe_session = None
    if NETVIBE_ENABLED:
        print("  • NetVibe probe (sensor in ns1, probe server in ns2) ...")
        netvibe_session = substrate_netvibe_start(run_dir, cfg.duration_s)

    run_results: dict[str, Any] = {}
    reg, _qlib = _bind_shared()
    browser_apps = [a for a in apps if reg and reg.get(a).has_player_qoe]
    shell_apps = [a for a in apps if a not in browser_apps]
    player_qoe: dict[str, Any] = {}

    # ── real player QoE: drive actual Chrome inside the shaped namespace ─────
    if COLLECT_ENABLED and browser_apps and reg is not None:
        print(f"  • real QoE collector for {browser_apps} ...")
        player_qoe = collect_player_qoe(
            cfg,
            run_dir,
            bots=prepared_bots,
            bot_failures=prepared_bot_failures,
        )
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

    netvibe_stopped = substrate_netvibe_stop(netvibe_session)
    netvibe_record = summarize_netvibe(netvibe_stopped)
    if netvibe_record:
        if netvibe_record.get("status") == "ok":
            _q = netvibe_record.get("queueing_ms") or {}
            _dn = _q.get("downstream_ns2_to_ns1") or {}
            _up = _q.get("upstream_ns1_to_ns2") or {}
            _rt = netvibe_record.get("round_trip_ms") or {}
            _rc = netvibe_record.get("records") or {}
            print(
                f"    · NetVibe: {_rc.get('ok')}/{_rc.get('total')} answered | "
                f"queueing down mean {_dn.get('mean')} / p95 {_dn.get('p95')} ms"
                f"  up mean {_up.get('mean')} / p95 {_up.get('p95')} ms | "
                f"rtt mean {_rt.get('mean')} ms | rates "
                f"{netvibe_record.get('rates_observed')}"
                f"  [{netvibe_record.get('transport')}]"
            )
        else:
            print(f"    ! NetVibe produced no result: {netvibe_record.get('error')}")

    irtt_stopped = substrate_irtt_stop(irtt_session)
    irtt_record = summarize_irtt(irtt_stopped)
    if irtt_record:
        if irtt_record.get("status") == "ok":
            _rt = irtt_record.get("round_trip_ms") or {}
            _pk = irtt_record.get("packets") or {}
            print(
                f"    · IRTT: rtt mean {_rt.get('mean')} ms "
                f"(min {_rt.get('min')} / max {_rt.get('max')}), "
                f"{_pk.get('received')}/{_pk.get('sent')} packets, "
                f"loss {_pk.get('loss_percent')}%  [{irtt_record.get('transport')}]"
            )
        else:
            print(f"    ! IRTT produced no result: {irtt_record.get('error')}")

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
    # ── the upstream leg's pcap, and the shaped-path guard ──────────────────
    upstream_pcap = None
    probe_path_result = None
    if upstream_capture_id:
        upstream_pcap = run_dir / f"capture_{UPSTREAM_IFACE}.pcap"
        substrate_capture_wait(upstream_capture_id, timeout_s=cap_duration + 60)
        if substrate_capture_download(upstream_capture_id, upstream_pcap):
            substrate_capture_delete(upstream_capture_id)
            print(
                f"  • {UPSTREAM_IFACE} pcap saved → {upstream_pcap} "
                f"({upstream_pcap.stat().st_size / 1e6:.1f} MB)"
            )
        else:
            print(
                f"  ! {UPSTREAM_IFACE} pcap download failed; keeping the "
                f"worker-side capture {upstream_capture_id}"
            )
            upstream_pcap = None

    _probe_ips = sorted(
        {
            ip
            for ips in merge_endpoint_ips(
                irtt_probe_endpoints(irtt_record),
                netvibe_probe_endpoints(netvibe_record),
            ).values()
            for ip in ips
        }
    )
    if _probe_ips and got:
        # Checked before anything is derived from the capture: if the probe did
        # not cross the shaped path, every number computed from it describes a
        # link that was not under test.
        probe_path_result = check_probe_path(
            {DOWNSTREAM_IFACE: local_pcap, UPSTREAM_IFACE: upstream_pcap},
            _probe_ips,
            enforce=False,
        )

    if local_ip_map:
        substrate_teardown_per_app_marks()

    # ── link-level stats (shaping fidelity) ──────────────────────────────────
    client_ips = client_ips_for_capture()
    net = (
        analyze_pcap_netstats(local_pcap, cfg.bandwidth_mbps, client_ips=client_ips)
        if got
        else NetStats()
    )
    if net.avg_throughput_mbps is not None:
        verdict = "OK" if net.shaping_verified else "OVER CAP"
        print(
            f"  • shaping check: avg {net.avg_throughput_mbps:.2f} / cap "
            f"{cfg.bandwidth_mbps:g} Mbps ({net.shaping_error_pct:+.1f}%) → {verdict}"
        )
    else:
        print("  • shaping check: no packets captured")

    # ── per-app split (never a combined view) ────────────────────────────────
    # Splitting the capture and rendering the plots takes ~9 minutes on a 2-vCPU
    # host (measured: 465-574s across three cells), and run_direct does it
    # serially between cells. Inside a 40-minute free Zoom meeting that is most
    # of the window spent on work that needs no meeting at all - it capped a
    # sweep at 4 cells. Deferring it leaves setup + call (~4.7 min) as the only
    # meeting-time cost, and `replot_run(run_dir)` does the identical work
    # afterwards: it re-reads record.json and capture.pcap and rebuilds both the
    # plots and per_app_stats, so nothing is lost by postponing it.
    if DEFER_ANALYSIS:
        print("  • per-app split + plots DEFERRED (PRAMANA_DEFER_ANALYSIS=1)")
        print("    finish with: replot_run(run_dir)")
        attribution = CaptureAttribution(note="deferred: run replot_run(run_dir)")
    else:
        print("  • splitting capture per app ...")
        # The URL's address and the address ICE actually chose are different
        # questions; take both, preferring neither, since an app may be reached
        # by literal IP *and* negotiate a separate media path.
        endpoint_ips = merge_endpoint_ips(
            app_endpoint_ips_from_urls(cfg),
            app_endpoint_ips_from_webrtc_stats(run_dir, apps),
        )
        attribution = attribute_capture(
            local_pcap,
            apps,
            cfg.bandwidth_mbps,
            local_ip_map=local_ip_map,
            app_endpoint_ips=endpoint_ips,
            client_ips=None if local_ip_map else client_ips,
            probe_endpoint_ips=merge_endpoint_ips(
                irtt_probe_endpoints(irtt_record),
                netvibe_probe_endpoints(netvibe_record),
            ),
        )
    if attribution.method != "none":
        print(
            f"    method={attribution.method}  local={attribution.local_ips}  "
            f"({attribution.local_ip_source})  "
            f"{100 * attribution.attributed_fraction:.1f}% of bytes attributed"
        )
        for bucket, mb in list(attribution.bucket_mb.items())[:6]:
            print(f"      {bucket:16s} {mb:9.2f} MB")

    # One pcap per app, so a concurrent run ships a capture per application
    # rather than one shared file plus attribution metadata.
    if concurrent:
        print("  • writing one pcap per app ...")
    pcap_per_app = (
        split_capture_by_app(local_pcap, attribution, list(apps), run_dir)
        if got
        else {}
    )

    per_app: dict[str, Any] = {}
    per_app_stats: dict[str, Any] = {}
    for a in apps:
        dirs = app_verdict_directions(a)
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
        stats["verdict_directions"] = dirs
        stats["plotted_directions"] = app_plot_directions(a)
        stats["classification"] = verdict
        stats["player_qoe_status"] = pq_a.get("status")
        stats["attribution_method"] = attribution.method
        stats["pcap"] = pcap_per_app.get(a, "")
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
    if DEFER_ANALYSIS:
        plots = {}
    else:
        print("  • generating per-app plots ...")
        try:
            plots = generate_run_plots(attribution, cfg, run_dir, player_qoe=player_qoe)
        except Exception as exc:  # a plotting bug must never lose the evidence
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
        # Per-app viewport caps in force for this run, as {app: "WxH"}. A cap
        # bounds the TOP of that app's ABR ladder (the player sizes its
        # rendition to the element it draws into), so a capped run must never be
        # pooled with or compared against an uncapped one. Empty means every
        # app ran at the default viewport with a free ladder. The run slug
        # carries a matching `_vcap...` marker.
        "app_window_caps": dict(cfg.app_window or {}),
        "app_ladder_pinned": sorted((cfg.app_window or {}).keys()),
        # The rate every player signal was read at. A buffer or bitrate series
        # cannot be compared across apps or runs without it.
        "sampling_interval_s": SAMPLE_INTERVAL_S,
        "merge_vendor_stats": bool(cfg.merge_vendor_stats),
        # Codec families hidden per app for this run. A run with a codec family
        # removed must never be pooled with one that had it available: the
        # bitrate-per-resolution differs, so both the ladder and the offered
        # load differ. The run slug carries a matching `_noc...` marker.
        "app_blocked_codecs": {
            a: sorted(v) for a, v in (cfg.app_block_codecs or {}).items() if v
        },
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
                "browser apps in one namespace, so there is only one local IP). "
                "Media endpoints reached by IP carry no DNS answer or SNI; such "
                "flows are adopted into an app bucket only when one app can "
                "claim them by shared prefix or concurrent timing, and are left "
                "in `other` when ambiguous - see adopted_unnamed_ips"
            ),
            "attributed_byte_fraction": attribution.attributed_fraction,
            "adopted_unnamed_ips": attribution.adopted_ips,
            "local_ip_source": attribution.local_ip_source,
            "declared_peer_ips": attribution.peer_ips,
            "probe_endpoint_ips": attribution.probe_ips,
        },
        "network_stats": asdict(net),
        # The probe's own record, beside the QoE record rather than inside it:
        # it measures the link, not the application, and it is not an app.
        "irtt": irtt_record,
        "netvibe": netvibe_record,
        "upstream_pcap": str(upstream_pcap) if upstream_pcap else None,
        # Evidence that the probe really crossed the shaped path, not the
        # management link. Null when no probe needed checking.
        "probe_path": probe_path_result,
        "per_app_stats": per_app_stats,
        # True when the capture split and plots were postponed to keep a live
        # meeting window free. per_app_stats and the plots are EMPTY in that
        # case and must not be read as "no traffic" - run replot_run(run_dir) to
        # fill them from the capture that is already on disk.
        "analysis_deferred": bool(DEFER_ANALYSIS),
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
            # `pcap` stays the combined capture (raw evidence, and the only
            # file a solo run needs). `pcap_per_app` is the per-application
            # split: for a concurrent run that is one capture per app.
            "pcap": str(local_pcap) if got else "",
            "pcap_saved": bool(got),
            "pcap_per_app": pcap_per_app,
            "pcap_split_method": attribution.method if concurrent else "solo",
            "plots": plots,
            "dir": str(run_dir),
        },
    }

    # Write the record (and the pcap and plots above) BEFORE any strict-shaping
    # abort: a run that failed verification is still raw evidence worth keeping.
    # A probe that bypassed the shaped path invalidates the run, but the
    # artifacts are written first: the same reasoning as the shaping abort
    # below, since the captures are the evidence for the diagnosis.
    if (
        probe_path_result
        and not probe_path_result.get("ok")
        and probe_path_result.get("strict")
    ):
        record["probe_path_verification_failed"] = True
        _write_record(record, run_dir)
        print(f"  ✓ saved → {run_dir} (flagged: probe path verification failed)")
        raise RuntimeError(
            "Probe path verification FAILED for "
            + cfg.slug
            + ": "
            + "; ".join(probe_path_result.get("problems") or [])
            + f". Artifacts were still saved to {run_dir}."
        )

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
                ns = analyze_pcap_netstats(
                    p, cap_mbps or 0, client_ips=client_ips_for_capture()
                )
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
    "app_verdict_directions",
    "plot_apps_throughput",
    "split_capture_by_app",
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
    # locality + endpoint ground truth (see attribute_capture's docstring)
    "client_ips_for_capture",
    "app_endpoint_ips_from_url_map",
    "app_endpoint_ips_from_urls",
    "app_endpoint_ips_from_webrtc_stats",
    "merge_endpoint_ips",
    # probes that share the link with the app (not apps themselves)
    "substrate_irtt_start",
    "substrate_irtt_stop",
    "summarize_irtt",
    "irtt_probe_endpoints",
    "substrate_netvibe_start",
    "substrate_netvibe_stop",
    "summarize_netvibe",
    "netvibe_probe_endpoints",
    "netvibe_bench_config",
    "substrate_capture_start_on",
    "probe_path_evidence",
    "check_probe_path",
    "probe_path_strict",
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
