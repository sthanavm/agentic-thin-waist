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


def drive(
    app: str, condition: Condition, duration_s: int, out_dir: str
) -> AppRunResult:
    """One app run in `condition`, returning what the guard and labeller need."""
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
        force_max_quality=FORCE_MAX_QUALITY,
    )

    restore = netns.install_shape_hook(H, condition.loss_pct, condition.rtt_ms)
    try:
        with browser.CmdlineWatcher(timeout_s=duration_s + 120) as watcher:
            record = H.run_direct(cfg)
    except Exception as exc:  # noqa: BLE001 - a failed run is data, not a crash
        return AppRunResult(ok=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        restore()

    player_qoe = record.get("player_qoe") or {}
    return AppRunResult(
        ok=True,
        player_qoe=player_qoe,
        browser_cmdline=watcher.cmdline or "",
        attribution=record,  # the record carries link_interference
        raw_path=str(record.get("run_dir") or out_dir),
        error="",
    )
