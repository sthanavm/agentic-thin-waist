"""The real streaming adapter: drives one app run through the pramana collector.

This is the only module that knows about `pramana_helpers`. It wires three
things the CPUC layer needs and the platform does not provide:

  1. LOSS. `substrate_shape()` has no loss field, so `netns.install_shape_hook`
     wraps it and applies loss over both directions after every shape call.
  2. THE BROWSER COMMAND LINE. `collect.py` never records it, so a watcher
     reads it from /proc during the window (the collector runs `--pid host`).
  3. A STRICT INTERFERENCE VERDICT. run_direct records the per-bin judgement;
     here it is fatal rather than advisory.

Nothing in pramana_helpers or collect.py is modified.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Optional

from . import browser, deps, netns
from .conditions import FORCE_MAX_QUALITY, Condition
from .runner import AppRunResult

# The collector image must contain the uniform stream probe. Without it the
# sampler emits only the vendor "stats for nerds" fields and
# startup_delay_ms / rebuffer_* come back None, so the run produces a capture
# and a resolution but cannot be labelled -- which is exactly how the second
# pilot failed. video-qoe-collector:latest (built 2026-09-26) has ZERO
# occurrences of STREAM_PROBE_JS; :driver-verify has it, and also carries
# Chrome 156 with both chromedriver names baked so no driver is fetched over
# the shaped link mid-measurement.
# :latest, NOT :driver-verify. Both bake a collect.py containing
# STREAM_PROBE_JS, but on :driver-verify the probe emitted nothing at run time:
# that run produced 62 stat keys with no rvfc_* fields at all, where a
# successful archived run produced 139 including first_presentation_ms. The two
# images differ in seleniumbase (4.56.5 vs 4.54.12), and every archived run
# that carries a startup figure -- 195 of 205 for YouTube -- was taken on
# 4.54.12. So the collector source is MOUNTED over :latest, which is how the
# archive was collected, rather than trusting a newer image.
REQUIRED_COLLECTOR_IMAGE = os.environ.get(
    "PRAMANA_COLLECTOR_IMAGE", "video-qoe-collector:latest"
)
# The mounted source is what must carry the probe: it overrides whatever the
# image baked, and a missing mount silently falls back to the image's copy.
COLLECTOR_SRC = os.environ.get(
    "PRAMANA_COLLECTOR_SRC",
    "/home/student/cpuc/services/orchestration/scripts/selenium_video_qoe/collect.py",
)
PROBE_MARKER = "STREAM_PROBE_JS"

# Per-app viewport, as {app: "WxH"} or {app: "fullscreen"}. None for every
# normal run, so the main sweep's geometry is untouched. The 1080p-viewport
# supplementary set sets it to fullscreen: the window is already 1920x1080,
# but YouTube's watch layout leaves the player element 1331x749, and an
# adaptive player sizes its rendition to that element.
APP_WINDOW: dict | None = None


class CollectorImageUnusable(RuntimeError):
    pass


def assert_collector_has_probe(
    image: str = REQUIRED_COLLECTOR_IMAGE, src: str = COLLECTOR_SRC
) -> None:
    """Fail before the first run if the collector cannot produce the metrics.

    Checks the MOUNTED source rather than the image, because the mount
    overrides the image's own collect.py -- and checks the file exists at all,
    since a missing mount falls back to the baked copy, which is the exact
    configuration that produced 62 stat keys and no startup figure.
    """
    if not os.path.isfile(src):
        raise CollectorImageUnusable(
            f"collector source {src!r} does not exist, so the run would fall "
            f"back to the image's baked collect.py"
        )
    text = open(src).read()
    for marker in (PROBE_MARKER, "rvfc_supported", "first_presentation_ms"):
        if marker not in text:
            raise CollectorImageUnusable(
                f"collector source {src!r} has no {marker!r}, so startup delay "
                f"and rebuffering cannot be measured"
            )


def _helpers():
    # Read at pramana_helpers import time, so it has to be set before the
    # import rather than after it.
    os.environ.setdefault("PRAMANA_COLLECTOR_IMAGE", REQUIRED_COLLECTOR_IMAGE)
    os.environ.setdefault("PRAMANA_COLLECTOR_SRC", COLLECTOR_SRC)
    deps.install()
    import pramana_helpers  # noqa: PLC0415

    return pramana_helpers


# Reasons a completed run's numbers cannot be trusted. Each of these fired on a
# real run that `drive` reported as ok=True, which is the whole point: the
# record already knew the run was bad and nothing looked.
#
#   * empty capture      -- a 372-byte pcap, network_stats.packets == 0
#   * app moved no bytes -- per_app_stats[app].download.classification
#                           == "no_traffic", total_mb == 0.0
#   * collector failed   -- the collector exited 1 and the record carried the
#                           code, but `ok` was a constant
#
# This is the fourth instance of the same class in this harness (tshark
# returning 0 because the binary was absent; browser_infra read from per_app,
# which only holds declared apps, so it reported 0.00; the Meet demand sidecar
# emptying when its path was hardcoded off-box). The shape is always a success
# value that is not derived from the evidence, so these are derived from it.
def record_failures(record: dict, app: str) -> list[str]:
    """Why this run is unusable, or [] if it is usable. Order is stable."""
    out: list[str] = []

    ns = record.get("network_stats") or {}
    packets, total_mb = ns.get("packets"), ns.get("total_mb")
    if not packets or not total_mb:
        out.append(
            f"capture is empty (packets={packets!r}, total_mb={total_mb!r}); "
            f"nothing was measured, so every figure from this run is vacuous"
        )

    dl = ((record.get("per_app_stats") or {}).get(app) or {}).get("download") or {}
    app_mb, klass = dl.get("total_mb"), dl.get("classification")
    if not app_mb or klass == "no_traffic":
        out.append(
            f"{app} moved no bytes (total_mb={app_mb!r}, classification={klass!r}); "
            f"the app under test never ran, whatever else was on the link"
        )

    # Absent is NOT a pass. A missing exit status means the pramana tree
    # predates the field, so the check cannot be performed -- and a check that
    # silently does nothing is how the three earlier instances survived.
    pq = (record.get("player_qoe") or {}).get(app) or {}
    if "collector_returncode" not in pq:
        out.append(
            "collector exit status missing from the record; this pramana tree "
            "predates collector_returncode, so collector failure cannot be "
            "ruled out for this run"
        )
    elif pq["collector_returncode"] not in (0, None):
        out.append(f"collector exited {pq['collector_returncode']!r}")

    return out


def drive(
    app: str,
    condition: Condition,
    duration_s: int,
    out_dir: str,
    app_url: Optional[str] = None,
    ctp_profile: str = "",
    ctp_dir: str = "",
) -> AppRunResult:
    """One app run in `condition`, returning what the guard and labeller need.

    `app_url` is optional: omitted, `run_direct` falls back to
    `pramana_helpers.APP_REGISTRY[app]["url"]`. Passed explicitly (e.g. by
    vimeo.py) so every row's record carries exactly what was played rather
    than relying on a registry default a reader of the CSV cannot see.

    `ctp_profile` (optional): replay this CTP profile's download side for the
    duration of the run. `ctp_dir` must be the directory holding
    `<ctp_profile>.pcap` on the HOST (used only for the pre-flight existence
    check -- see netns.start_ctp_replay for why the replay itself reads a
    different, container-side path). Registers the replay's destination IP
    as a named probe on `cfg` so `link_interference` does not flag this
    traffic as unexplained background contention.
    """
    H = _helpers()
    netns.start_iperf3_server()

    cfg = H.ExperimentConfig(
        apps=[app],
        bandwidth_mbps=condition.rate_down_mbps,
        upload_mbps=condition.rate_up_mbps,
        latency_ms=condition.latency_for_platform(),
        loss_pct=condition.loss_pct,
        aqm=condition.qdisc,
        cca="cubic",
        duration_s=duration_s,
        trial=condition.trial_for(app),
        app_urls=({app: app_url} if app_url else {}),
        force_max_quality=FORCE_MAX_QUALITY,
        app_window=APP_WINDOW,
        extra_probe_endpoint_ips=(
            {"ctp": {netns.CTP_PNAT_IP}} if ctp_profile else None
        ),
    )

    restore = netns.install_shape_hook(H, condition.loss_pct, condition.rtt_ms)
    try:
        if ctp_profile:
            # Margin over duration_s: this is a fire-and-forget start, not
            # synchronized to run_direct's first sample (see
            # netns.start_ctp_replay's docstring), so it must still be
            # running through run_direct's own setup latency.
            netns.start_ctp_replay(ctp_profile, ctp_dir, seconds=duration_s + 30)
        with browser.CmdlineWatcher(timeout_s=duration_s + 120) as watcher:
            record = H.run_direct(cfg)
    except Exception as exc:  # noqa: BLE001 - a failed run is data, not a crash
        return AppRunResult(ok=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        restore()

    player_qoe = record.get("player_qoe") or {}
    # `ok` is derived from the record, never asserted. A run that captured
    # nothing, or whose app moved nothing, or whose collector died, is not a
    # successful run no matter how cleanly run_direct returned.
    failures = record_failures(record, app)
    return AppRunResult(
        ok=not failures,
        player_qoe=player_qoe,
        browser_cmdline=watcher.cmdline or "",
        attribution=record,  # the record carries link_interference
        raw_path=str(record.get("run_dir") or out_dir),
        error="; ".join(failures),
    )
