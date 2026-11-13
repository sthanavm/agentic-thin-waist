"""One CPUC run, end to end, and the cpuc_runs.csv writer.

A run is, per the plan doc and in this order:

    calibrate -> app for 60 s -> wait 30 s -> NDT -> one row

The app and the NDT test never overlap. That is the point of the 30 s gap, and
it is enforced structurally here (the phases are sequential statements, not
tasks) rather than by a comment asking the next person not to parallelise them.

PLUG-IN SEAM. `AppAdapter` is the whole interface another category has to
implement. Streaming (`StreamingAdapter`) is the only one validated here --
conferencing is Sthanav's and the NDT-only path is Mayank's, and both get a
registry slot and a stub that fails with what is missing rather than a
half-working default. The runner itself knows nothing about browsers, WebRTC or
ndt7 beyond these calls, so neither of them has to touch it.

WHAT THIS DOES NOT DO: collect data. `execute_app_run` and `execute_ndt_run`
each run one unit of work when handed real adapters, and `execute_condition_rep`
runs one condition's worth; nothing in this module schedules a matrix, and no
condition is applied unless a caller asks for it. Gate 1 is the harness only.
"""

from __future__ import annotations

import csv
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional, Protocol

from . import guard as guard_mod
from . import netns
from . import ndt as ndt_mod
from . import qoe_labels
from .conditions import FORCE_MAX_QUALITY, Condition, REFERENCE, RunPlan
from .shaping import CalibrationResult, calibrate, latency_and_loss, shaping

APP_WINDOW_S = 60  # plan doc: "Run the app (60 s window)"
GAP_BEFORE_NDT_S = 30  # plan doc: "wait 30 s, then run NDT. Never at the same time."


@dataclass
class AppRunResult:
    """What an adapter must hand back after driving its app."""

    ok: bool
    player_qoe: dict[str, Any] = field(default_factory=dict)
    # The REAL command line of the launched browser, for the suppression
    # assertion. An adapter that cannot produce one fails the guard, which is
    # the intended outcome: an unasserted run is not valid.
    browser_cmdline: str = ""
    # Whatever the capture attribution produced, passed to the interference
    # guard untouched.
    attribution: Any = None
    raw_path: str = ""
    error: str = ""


class AppAdapter(Protocol):
    """The entire contract for plugging a category into this harness."""

    name: str
    category: str  # "streaming" | "conferencing"

    def run_app(
        self, app: str, condition: Condition, duration_s: int, out_dir: str
    ) -> AppRunResult: ...

    def label(
        self, result: AppRunResult, app: str, reference: dict[str, Any]
    ) -> tuple[str, str, dict]: ...


class StreamingAdapter:
    """YouTube / Twitch / Puffer (or Vimeo) via the pramana collector.

    `drive` is injected so the orchestration can be tested without a browser,
    a shaped link or vm4. In production it is the collector call that returns
    player QoE, the browser command line and the capture attribution.
    """

    name = "streaming"
    category = "streaming"

    def __init__(self, drive: Callable[..., AppRunResult]):
        self._drive = drive

    def run_app(
        self, app: str, condition: Condition, duration_s: int, out_dir: str
    ) -> AppRunResult:
        return self._drive(
            app=app, condition=condition, duration_s=duration_s, out_dir=out_dir
        )

    def label(
        self, result: AppRunResult, app: str, reference: dict[str, Any]
    ) -> tuple[str, str, dict]:
        per_app = result.player_qoe.get(app, result.player_qoe)
        metrics = qoe_labels.extract_streaming_metrics(per_app or {})
        ref_res = (reference or {}).get(app, {}).get("resolution_p")
        label, reason = qoe_labels.label_streaming(metrics, ref_res)
        return label, reason, metrics.as_row()


class ConferencingAdapter:
    """Sthanav's half. Deliberately not implemented here.

    The seam is real -- the runner will drive this exactly as it drives
    streaming -- but the WebRTC getStats sampling, the peer setup and the
    conferencing QoE labels are his to write, and a stub that returned
    plausible zeros would be worse than one that refuses.
    """

    name = "conferencing"
    category = "conferencing"

    def run_app(
        self, app: str, condition: Condition, duration_s: int, out_dir: str
    ) -> AppRunResult:
        raise NotImplementedError(
            "conferencing adapter is Sthanav's scope. Implement run_app() to "
            "return AppRunResult(player_qoe=<getStats-derived>, "
            "browser_cmdline=<real cmdline>, attribution=<capture split>) and "
            "label() against qoe_labels.CONFERENCING_* thresholds."
        )

    def label(
        self, result: AppRunResult, app: str, reference: dict[str, Any]
    ) -> tuple[str, str, dict]:
        raise NotImplementedError("conferencing labels are Sthanav's scope")


ADAPTERS: dict[str, Any] = {}


def register_adapter(adapter: Any) -> None:
    ADAPTERS[adapter.category] = adapter


# ── cpuc_runs.csv ───────────────────────────────────────────────────────────
# Column order is the plan doc's schema first, then everything needed to audit
# a row. Writing a header once and appending thereafter; never overwriting.
BASE_COLUMNS = [
    "run_id",
    "group_id",
    "app",
    "run_type",
    "condition",
    "rate_down",
    "rate_up",
    "rtt",
    "loss_mode",
    "loss_pct",
    "rep",
    "is_reference",
    "ctp_profile",
    "valid",
    "invalid_reason",
    "qoe_label",
    "qoe_reason",
    "ndt_down",
    "ndt_up",
    "ndt_minrtt",
    "ndt_minrtt_upload",
    "ndt_loss",
    "ndt_retransmission",
    "ndt_retransmission_upload",
    "ndt_loss_basis",
    "ndt_server",
    "ndt_ok",
    "ndt_error",
    "ndt_raw_path",
    "raw_path",
    "started_utc",
    "high_rtt_source",
    "threshold_fingerprint",
    "force_max_quality",
]


def write_row(csv_path: str, row: dict) -> None:
    """Append one row, creating the header once. Never overwrites a file."""
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    cols = list(BASE_COLUMNS)
    cols += [k for k in sorted(row) if k not in cols]
    exists = os.path.exists(csv_path)
    if exists:
        with open(csv_path, newline="") as fh:
            header = next(csv.reader(fh), [])
        if header:
            # Keep the established header; park anything new in a JSON column
            # rather than rewriting a file that already has rows in it.
            extra = {k: v for k, v in row.items() if k not in header}
            row = {k: v for k, v in row.items() if k in header}
            if extra and "extra_json" in header:
                row["extra_json"] = json.dumps(extra, sort_keys=True, default=str)
            cols = header
    with open(csv_path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerow(row)


def _base_row(
    plan: RunPlan,
    run_id: str,
    group_id: str,
    out_dir: str,
    high_rtt_source: str,
    started,
) -> dict:
    cond = plan.condition
    return {
        "run_id": run_id,
        "group_id": group_id,
        "app": plan.app,
        "run_type": plan.run_type,
        "condition": cond.slug(),
        "rate_down": cond.rate_down_mbps,
        "rate_up": cond.rate_up_mbps,
        "rtt": cond.rtt_ms,
        "loss_mode": cond.loss_mode,
        "loss_pct": cond.loss_pct,
        "rep": plan.rep,
        "is_reference": plan.is_reference,
        "raw_path": out_dir,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", started),
        "high_rtt_source": high_rtt_source,
        "threshold_fingerprint": qoe_labels.THRESHOLD_FINGERPRINT,
        "force_max_quality": FORCE_MAX_QUALITY,
        "valid": False,
        "invalid_reason": "",
        "qoe_label": "",
        "qoe_reason": "",
    }


def group_id_for(condition: Condition, rep: int) -> str:
    """Links an app row to the NDT row taken in the same condition and rep.

    The NDT run is per (condition, rep), not per app, so three app rows share
    one NDT row. Without this column the analysis cannot pair the QoE label
    with the speed test IQB's verdict comes from.
    """
    return f"{condition.slug()}_rep{rep}"


def apply_condition(condition: Condition) -> dict:
    """Put the condition on the link, via the worker API plus host-side loss.

    Rate and latency go through the worker's /shape endpoint -- the same call
    run_direct makes, so an NDT run and an app run are shaped identically.
    Loss is applied afterwards by nsenter, because /shape has no loss field.

    NOT done with host `tc`: veth2/veth4 live in the substrate-worker
    container's own namespace, not the host's, so shaping them from the host
    fails outright. That is what broke the first pilot.
    """
    from . import deps, netns  # noqa: PLC0415

    deps.install()
    import pramana_helpers as H  # noqa: PLC0415

    cfg = H.ExperimentConfig(
        apps=[],
        bandwidth_mbps=condition.rate_down_mbps,
        upload_mbps=condition.rate_up_mbps,
        latency_ms=condition.latency_for_platform(),
        loss_pct=condition.loss_pct,
        aqm=condition.qdisc,
        cca="cubic",
        duration_s=1,
        trial=0,
    )
    out = H.substrate_shape(cfg, verify=False)
    applied = {}
    if condition.loss_pct > 0:
        applied = netns.apply_loss(condition.loss_pct, condition.rtt_ms)
    else:
        netns.clear_loss(condition.rtt_ms)
    return {"shape": out, "loss": applied}


def execute_app_run(
    plan: RunPlan,
    adapter: Any,
    out_root: str,
    csv_path: str,
    reference_metrics: Optional[dict[str, Any]] = None,
    high_rtt_source: str = "plan_doc_20ms",
    app_window_s: int = APP_WINDOW_S,
    apply_network: bool = False,
    now=None,
) -> dict:
    """One APP run: calibrate, drive the app for 60 s, guard it, label it.

    Writes one row with run_type="app". No NDT here -- that is its own row,
    taken once per (condition, rep) by `execute_ndt_run`.
    """
    assert plan.run_type == "app", f"execute_app_run got run_type={plan.run_type!r}"
    cond = plan.condition
    group = group_id_for(cond, plan.rep)
    run_id = f"app_{plan.app}_{group}_{uuid.uuid4().hex[:8]}"
    out_dir = os.path.join(out_root, run_id)
    row = _base_row(
        plan, run_id, group, out_dir, high_rtt_source, (now or time.gmtime)()
    )

    # The condition is applied by run_direct (rate/latency) plus the loss hook
    # installed inside the adapter, so it is NOT pre-applied here. Calibration
    # therefore has to happen with the condition already on the link, which is
    # why it is done against the shaper the adapter sets up: apply it, gate on
    # it, then hand over.
    apply_condition(cond)
    calib = calibrate(
        cond.rate_down_mbps, cond.rate_up_mbps, cond.rtt_ms, cond.loss_pct
    )
    row.update(calib.as_row())
    if not calib.ok:
        row["invalid_reason"] = f"calibration gate: {calib.skip_reason}"
        write_row(csv_path, row)
        return row

    try:
        app_res = adapter.run_app(plan.app, cond, app_window_s, out_dir)
    except Exception as exc:  # noqa: BLE001
        row["invalid_reason"] = f"app run raised {type(exc).__name__}: {exc}"
        write_row(csv_path, row)
        return row
    if not app_res.ok:
        row["invalid_reason"] = f"app run failed: {app_res.error}"
        write_row(csv_path, row)
        return row

    # Both halves of the background-traffic guard are fatal.
    try:
        guard_mod.assert_suppression_flags(app_res.browser_cmdline)
        # The adapter hands back run_direct's record, which already carries the
        # per-bin link_interference verdict, so enforce on that rather than
        # re-deriving it from the pcap -- two derivations of the same judgement
        # could disagree about the same capture. A CaptureAttribution object is
        # still accepted, for inspecting a capture outside a run.
        if isinstance(app_res.attribution, dict):
            verdict = guard_mod.enforce_record_verdict(app_res.attribution)
        else:
            verdict = guard_mod.assert_no_background_contention(
                app_res.attribution, [plan.app], cond.rate_down_mbps, strict=True
            )
        row.update(verdict.as_row())
    except (
        guard_mod.SuppressionNotActive,
        guard_mod.BackgroundContention,
        guard_mod.GuardUnavailable,
    ) as exc:
        row["invalid_reason"] = f"{type(exc).__name__}: {exc}"
        row["guard_ok"] = False
        write_row(csv_path, row)
        return row

    label, reason, metric_row = adapter.label(
        app_res, plan.app, reference_metrics or {}
    )
    row["qoe_label"] = label
    row["qoe_reason"] = reason
    row.update(metric_row)
    # Validity is about the MEASUREMENT, not about whether a label could be
    # assigned yet: a run taken before its reference exists is good data with a
    # deferred label. Only a genuinely unmeasurable run is invalid.
    row["valid"] = label != "invalid"
    if label == "deferred":
        row["valid"] = True
    if not row["valid"]:
        row["invalid_reason"] = f"qoe_label={label}: {reason}"
    write_row(csv_path, row)
    return row


def execute_ndt_run(
    plan: RunPlan,
    out_root: str,
    csv_path: str,
    ndt_server: str,
    high_rtt_source: str = "plan_doc_20ms",
    gap_s: int = GAP_BEFORE_NDT_S,
    apply_network: bool = True,
    sleep=time.sleep,
    now=None,
) -> dict:
    """One NDT run in the same condition, taken AFTER the app runs.

    The gap is waited here, not in the app run, because the app and the speed
    test must never overlap and this is the only place that ordering is
    expressible: the caller runs the apps, then this.
    """
    assert plan.run_type == "ndt", f"execute_ndt_run got run_type={plan.run_type!r}"
    cond = plan.condition
    group = group_id_for(cond, plan.rep)
    run_id = f"ndt_{group}_{uuid.uuid4().hex[:8]}"
    out_dir = os.path.join(out_root, run_id)
    row = _base_row(
        plan, run_id, group, out_dir, high_rtt_source, (now or time.gmtime)()
    )

    if apply_network:
        apply_condition(cond)

    calib = calibrate(
        cond.rate_down_mbps, cond.rate_up_mbps, cond.rtt_ms, cond.loss_pct
    )
    row.update(calib.as_row())
    if not calib.ok:
        row["invalid_reason"] = f"calibration gate: {calib.skip_reason}"
        write_row(csv_path, row)
        return row

    sleep(gap_s)
    ndt_res = ndt_mod.run_ndt(
        server=ndt_server, raw_path=os.path.join(out_dir, "ndt7.json")
    )
    row.update(ndt_res.as_row())
    row["valid"] = bool(ndt_res.ok)
    if not row["valid"]:
        row["invalid_reason"] = f"ndt: {ndt_res.error}"
    write_row(csv_path, row)
    return row


def execute_condition_rep(
    condition: Condition,
    rep: int,
    apps: tuple[str, ...],
    adapter: Any,
    out_root: str,
    csv_path: str,
    ndt_server: str,
    reference_metrics: Optional[dict[str, Any]] = None,
    high_rtt_source: str = "plan_doc_20ms",
    is_reference: bool = False,
    sleep=time.sleep,
) -> list[dict]:
    """Every app in one condition and repetition, then ONE NDT run.

    This is the unit the plan doc describes and the unit the 192 total counts:
    N app rows plus one NDT row per (condition, rep). The condition is applied
    once and recalibrated per run, so a shaper that drifted between the app and
    the speed test shows up instead of being assumed away.
    """
    rows: list[dict] = []
    for app in apps:
        rows.append(
            execute_app_run(
                RunPlan(app, condition, rep, "app", is_reference),
                adapter,
                out_root,
                csv_path,
                reference_metrics=reference_metrics,
                high_rtt_source=high_rtt_source,
            )
        )
    rows.append(
        execute_ndt_run(
            RunPlan("", condition, rep, "ndt", is_reference),
            out_root,
            csv_path,
            ndt_server,
            high_rtt_source=high_rtt_source,
            sleep=sleep,
        )
    )
    return rows


def verify_shaper_from_record(
    record: dict, cap_mbps: float, tolerance: float = 0.10
) -> dict:
    """Did the link actually hold the configured rate? Read from the capture.

    `shaping_verified` from run_direct means "not over cap". Being far UNDER
    the cap is not itself a fault here -- that is the whole experiment at low
    rates, where the player simply does not ask for more -- so the verdict
    recorded is "not over cap", and the achieved figure is kept beside it so
    the plot and the table can show what the link really did.
    """
    ns = record.get("network_stats") or {}
    avg = ns.get("avg_throughput_mbps")
    peak = ns.get("peak_throughput_mbps")
    over = None
    if peak is not None and cap_mbps > 0:
        over = peak > cap_mbps * (1.0 + tolerance)
    return {
        "shaper_avg_mbps": avg,
        "shaper_peak_mbps": peak,
        "shaper_cap_mbps": cap_mbps,
        "shaper_error_pct": ns.get("shaping_error_pct"),
        "shaper_verified_not_over_cap": (not over) if over is not None else None,
        "shaper_basis": "achieved throughput from the run's own pcap (no iperf3)",
    }


def run_ndt_point(plan, out_root, csv_path, attempt):
    cond = plan.condition
    group = group_id_for(cond, plan.rep)
    run_id = f"ndt_{group}_a{attempt}_{os.urandom(3).hex()}"
    out_dir = os.path.join(out_root, run_id)
    row = _base_row(plan, run_id, group, out_dir, "sweep_20ms", time.gmtime())
    row["suspect"] = False
    row["suspect_reason"] = ""
    row["attempt"] = attempt

    apply_condition(cond)
    netns.link_idle_wait()
    row.update({f"tc_{k}": v for k, v in tc_drop_stats().items()})
    # The app and the speed test must never overlap; the app for this point has
    # already finished, and this wait keeps the same separation Stage 1 used.
    time.sleep(GAP_BEFORE_NDT_S)
    res = ndt_mod.run_ndt(server=None, raw_path=os.path.join(out_dir, "ndt7.json"))
    row.update(res.as_row())
    row["valid"] = bool(res.ok)
    if not row["valid"]:
        row["invalid_reason"] = f"ndt: {res.error}"
    write_row(csv_path, row)
    return row


def tc_drop_stats() -> dict:
    """netem/qdisc counters, to confirm no loss was induced on this sweep.

    The sweep configures no loss, so this is a check that none is present --
    a leftover netem from an earlier loss condition would otherwise be
    invisible and would corrupt every point.
    """
    out = {}
    for ns, iface in (
        (netns.SERVER_NS, netns.SERVER_IFACE),
        (netns.CLIENT_NS, netns.CLIENT_IFACE),
    ):
        try:
            r = netns.ns_exec(ns, f"tc -s qdisc show dev {iface}", check=False)
            txt = r.stdout.strip()
        except Exception as exc:  # noqa: BLE001
            txt = f"<{type(exc).__name__}>"
        out[f"{ns}:{iface}"] = txt.replace("\n", " ")[:300]
        out[f"{ns}:{iface}:has_loss"] = " loss " in txt
    return out
