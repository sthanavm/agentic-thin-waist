"""Tests for the CPUC Gate 1 harness.

These exist to pin the parts of the study that must not move -- the matrix,
the reference run, the pre-registered QoE labels -- and the parts that are
easy to get subtly wrong: two-way loss arithmetic, label precedence, phase
order, and the two fatal guards.
"""

import csv
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cpuc.conditions as C  # noqa: E402
import cpuc.guard as G  # noqa: E402
import cpuc.drive_pramana as D  # noqa: E402
import cpuc.ndt as N  # noqa: E402
import cpuc.qoe_labels as Q  # noqa: E402
import cpuc.runner as R  # noqa: E402
import cpuc.shaping as S  # noqa: E402


# ── conditions / matrix ─────────────────────────────────────────────────────
def test_iqb_streaming_row_matches_fig10():
    assert C.IQB_VIDEO_STREAMING["download_min_mbps"] == 25.0
    assert C.IQB_VIDEO_STREAMING["upload_min_mbps"] == 10.0
    assert C.IQB_VIDEO_STREAMING["latency_min_ms"] == 100.0
    assert C.IQB_VIDEO_STREAMING["latency_high_ms"] == 50.0
    assert C.IQB_VIDEO_STREAMING["loss_min_pct"] == 1.0
    assert C.IQB_VIDEO_STREAMING["loss_high_pct"] == 0.1


def test_reference_run_is_frozen_at_100mbps_20ms():
    assert (C.REFERENCE.rate_down_mbps, C.REFERENCE.rtt_ms) == (100.0, 20.0)
    assert C.REFERENCE.loss_pct == 0.0
    with pytest.raises(Exception):
        C.REFERENCE.rtt_ms = 50.0  # frozen dataclass


def test_stage1_streaming_is_eight_conditions_and_72_runs():
    conds = C.stage1_streaming_conditions()
    assert len(conds) == 8  # 2 rates x 2 RTTs x 2 loss modes
    plans = C.stage1_streaming_plan()
    assert len(plans) == 72  # x 3 apps x 3 reps
    assert {p.app for p in plans} == {"youtube", "twitch", "vimeo"}


def test_loss_follows_the_rate_corner():
    conds = C.stage1_streaming_conditions()
    losses = {(c.rate_down_mbps, c.loss_pct) for c in conds if c.loss_mode == "iqb"}
    assert (25.0, 1.0) in losses  # min rate -> IQB min loss
    assert (100.0, 0.1) in losses  # high rate -> IQB high loss


def test_open_question_changes_whether_reference_is_a_corner():
    """Where the 50 vs 20 ms question actually bites."""
    assert C.reference_is_a_stage1_corner(high_rtt_ms=20.0) is True
    assert C.reference_is_a_stage1_corner(high_rtt_ms=50.0) is False
    assert C.STREAMING_HIGH_RTT_MS_UNRESOLVED is True


# ── loss arithmetic ─────────────────────────────────────────────────────────
def test_round_trip_loss_is_not_just_doubled():
    assert S.expected_round_trip_loss(1.0) == pytest.approx(1.99, abs=1e-9)
    assert S.expected_round_trip_loss(0.1) == pytest.approx(0.1999, abs=1e-6)
    assert S.expected_round_trip_loss(0.0) == 0.0


def test_netem_args_carry_delay_and_loss_together():
    assert S._netem_args(10.0, 1.0) == "delay 10.0ms loss 1.0%"
    assert S._netem_args(10.0, 0.0) == "delay 10.0ms"
    assert S._netem_args(0.0, 0.5) == "loss 0.5%"
    assert S._netem_args(0.0, 0.0) == ""


def test_low_loss_needs_far_more_packets_than_a_default_ping():
    assert S.loss_check_packets_needed(1.0) < S.loss_check_packets_needed(0.1)
    assert S.loss_check_packets_needed(0.1) > 1000


# ── calibration gate ────────────────────────────────────────────────────────
def _iperf(mbps):
    return json.dumps({"end": {"sum_received": {"bits_per_second": mbps * 1e6}}})


def _ping(rtt, loss, sent):
    return (
        f"{sent} packets transmitted, {sent} received, {loss}% packet loss\n"
        f"rtt min/avg/max/mdev = 1.0/{rtt}/9.0/0.1 ms\n"
    )


def _iperf_udp(lost_pct, packets, sent_mbps=0.0):
    """iperf3 -u --json: `sum` carries the SENT rate plus what did not arrive."""
    return json.dumps(
        {
            "end": {
                "sum": {
                    "lost_percent": lost_pct,
                    "packets": packets,
                    "bits_per_second": sent_mbps * 1e6,
                }
            }
        }
    )


def _fake_runner(down, up, rtt, loss, sent, udp_loss=None, udp_packets=40000):
    """udp_loss: the ONE-WAY loss each direction's iperf3 -u probe reports."""

    def runner(cmd, check=True, timeout=None):
        if "iperf3" in cmd and " -u " in cmd:
            return types.SimpleNamespace(
                stdout=_iperf_udp(
                    udp_loss,
                    udp_packets,
                    # the rate probe is UDP too on a loss condition, sent above
                    # the target, so the receiver should see target less loss
                    sent_mbps=(down if "-R" in cmd else up)
                    / (1.0 - (udp_loss or 0.0) / 100.0),
                ),
                stderr="",
                returncode=0,
            )
        if "iperf3" in cmd:
            return types.SimpleNamespace(
                stdout=_iperf(down if "-R" in cmd else up), stderr="", returncode=0
            )
        return types.SimpleNamespace(
            stdout=_ping(rtt, loss, sent), stderr="", returncode=0
        )

    return runner


def test_calibration_passes_inside_five_percent():
    r = S.calibrate(
        100,
        10,
        20,
        0.0,
        ns_prefix=lambda ns: "",
        runner=_fake_runner(98, 9.8, 20.4, 0.0, 100),
    )
    assert r.ok, r.failures


def test_calibration_skips_when_rate_is_off_by_more_than_five_percent():
    r = S.calibrate(
        100,
        10,
        20,
        0.0,
        ns_prefix=lambda ns: "",
        runner=_fake_runner(80, 9.8, 20.0, 0.0, 100),
    )
    assert not r.ok
    assert any("down rate" in f for f in r.failures)


def test_calibration_skips_when_rtt_is_off_by_more_than_five_percent():
    r = S.calibrate(
        100,
        10,
        20,
        0.0,
        ns_prefix=lambda ns: "",
        runner=_fake_runner(100, 10, 23.0, 0.0, 100),
    )
    assert not r.ok
    assert any("RTT" in f for f in r.failures)


def test_loss_verified_per_direction_against_the_configured_one_way_rate():
    ok = S.calibrate(
        100,
        10,
        20,
        1.0,
        ns_prefix=lambda ns: "",
        runner=_fake_runner(100, 10, 20, 0.0, 100, udp_loss=1.0),
    )
    assert ok.ok and ok.loss_verified is True, ok.loss_note
    assert ok.loss_probe == "iperf3_udp"
    assert ok.measured_loss_down_pct == 1.0 and ok.measured_loss_up_pct == 1.0


def test_loss_applied_in_only_one_direction_is_caught():
    """The failure the gate exists for: netem on egress but not ingress.

    Both directions report the same figure here, so instead the test checks
    that a ZERO reading against a 1% configuration fails -- which is what a
    direction with no netem would report.
    """
    bad = S.calibrate(
        100,
        10,
        20,
        1.0,
        ns_prefix=lambda ns: "",
        runner=_fake_runner(100, 10, 20, 0.0, 100, udp_loss=0.0),
    )
    assert not bad.ok and bad.loss_verified is False
    assert "configured 1.0000%" in bad.loss_note


def test_low_loss_is_still_resolvable_with_a_udp_probe():
    """0.1% one-way: ping would need ~22000 packets; UDP resolves it."""
    good = S.calibrate(
        100,
        10,
        20,
        0.1,
        ns_prefix=lambda ns: "",
        runner=_fake_runner(100, 10, 20, 0.0, 100, udp_loss=0.1),
    )
    assert good.ok and good.loss_verified is True, good.loss_note
    bad = S.calibrate(
        100,
        10,
        20,
        0.1,
        ns_prefix=lambda ns: "",
        runner=_fake_runner(100, 10, 20, 0.0, 100, udp_loss=0.0),
    )
    assert not bad.ok and bad.loss_verified is False


def test_a_one_way_vs_round_trip_mixup_would_be_caught_by_the_packet_budget():
    """The ping fallback's budget must separate 1.0% from 1.99%."""
    n = S.loss_check_packets_needed(1.0)
    import math

    se = math.sqrt(
        S.expected_round_trip_loss(1.0) * (100 - S.expected_round_trip_loss(1.0)) / n
    )
    assert 3 * se < abs(1.0 - S.expected_round_trip_loss(1.0))


# ── ndt7 parsing ────────────────────────────────────────────────────────────
NDT_SUMMARY = {
    "ServerFQDN": "ndt-mlab1.lax.measurement-lab.org",
    "ServerIP": "10.0.0.1",
    "ClientIP": "172.16.1.1",
    "Download": {
        "UUID": "d",
        "Throughput": {"Value": 94.21, "Unit": "Mbit/s"},
        "Latency": {"Value": 20.4, "Unit": "ms"},
        "Retransmission": {"Value": 0.0031, "Unit": "/"},
    },
    "Upload": {
        "UUID": "u",
        "Throughput": {"Value": 9.68, "Unit": "Mbit/s"},
        "Latency": {"Value": 21.1, "Unit": "ms"},
        "Retransmission": {"Value": 0.0, "Unit": "/"},
    },
}


def test_ndt_json_summary_is_parsed():
    text = (
        json.dumps({"Key": "measurement", "Value": {"AppInfo": {}}})
        + "\n"
        + json.dumps(NDT_SUMMARY)
        + "\n"
    )
    r = N.parse_ndt_json(text)
    assert r.ok
    assert r.download_mbps == pytest.approx(94.21)
    assert r.upload_mbps == pytest.approx(9.68)
    assert r.download_minrtt_ms == pytest.approx(20.4)
    assert r.download_retransmission == pytest.approx(0.0031)


def test_ndt_summary_wrapped_in_a_key_value_event_is_parsed():
    text = json.dumps({"Key": "summary", "Value": NDT_SUMMARY})
    assert N.parse_ndt_json(text).ok


def test_ndt_without_a_summary_is_not_ok():
    assert not N.parse_ndt_json("not json\n{}\n").ok


def test_ndt_loss_is_labelled_as_retransmission_not_loss():
    row = N.parse_ndt_json(json.dumps(NDT_SUMMARY)).as_row()
    assert row["ndt_loss"] == row["ndt_retransmission"]
    assert "NOT a packet-loss rate" in row["ndt_loss_basis"]


# ── QoE labels ──────────────────────────────────────────────────────────────
def _m(startup=1.0, res=1080, ratio=0.0, events=0, dur=0.0, sess=60.0, played=True):
    return Q.StreamingMetrics(
        startup_delay_s=startup,
        resolution_p=res,
        rebuffer_ratio=ratio,
        rebuffer_events=events,
        rebuffer_duration_ms=dur,
        session_seconds=sess,
        played=played,
    )


def test_high_requires_all_three():
    assert Q.label_streaming(_m(), 1080)[0] == "high"


def test_resolution_ninety_percent_boundary_is_exact():
    """0.9 * 1080 = 972, so 972p is High and 971p is not."""
    assert Q.label_streaming(_m(res=972), 1080)[0] == "high"
    assert Q.label_streaming(_m(res=971), 1080)[0] == "acceptable"
    assert Q.label_streaming(_m(res=900), 1080)[0] == "acceptable"


def test_unusable_beats_high_on_startup():
    """Perfect resolution and no rebuffering, but 12 s startup."""
    label, reason = Q.label_streaming(_m(startup=12.0), 1080)
    assert label == "unusable"
    assert "startup" in reason


def test_unusable_on_rebuffer_ratio_over_five_percent():
    assert Q.label_streaming(_m(ratio=0.06, events=2, dur=3600), 1080)[0] == "unusable"


def test_no_playback_is_unusable():
    assert Q.label_streaming(_m(played=False), 1080)[0] == "unusable"


def test_any_rebuffering_drops_high_to_acceptable():
    assert Q.label_streaming(_m(ratio=0.01, events=1, dur=600), 1080)[0] == "acceptable"


def test_missing_metric_is_invalid_not_a_guess():
    """A metric the player did not produce makes the run invalid. A missing
    REFERENCE is different -- that defers the label, see the deferred test."""
    assert Q.label_streaming(_m(startup=None), 1080)[0] == "invalid"
    assert Q.label_streaming(_m(res=None), 1080)[0] == "invalid"
    assert Q.label_streaming(_m(ratio=None), 1080)[0] == "invalid"


def test_thresholds_are_fingerprinted():
    assert len(Q.THRESHOLD_FINGERPRINT) == 12


def test_extract_reads_the_uniform_schema():
    pq = {
        "player_qoe_available": True,
        "video_resolution_p": 720,
        "resolutions_observed": [480, 720],
        "session_seconds": 60.0,
        "uniform": {
            "startup_delay_ms": 2400.0,
            "rebuffer_events": 1,
            "rebuffer_duration_ms": 1200.0,
            "startup_basis": "b",
            "rebuffer_basis": "c",
        },
    }
    m = Q.extract_streaming_metrics(pq)
    assert m.startup_delay_s == pytest.approx(2.4)
    assert m.resolution_p == 720
    assert m.rebuffer_ratio == pytest.approx(0.02)
    assert m.played is True


# ── the guards ──────────────────────────────────────────────────────────────
GOOD_CMDLINE = (
    "/opt/google/chrome/chrome --disable-component-update "
    "--disable-background-networking --disable-domain-reliability "
    "--no-first-run --no-default-browser-check "
    "--disable-features=OptimizationHints,OptimizationGuideModelDownloading,"
    "OptimizationGuideModelPushNotifications,OptimizationTargetPrediction "
    "--host-resolver-rules=MAP optimizationguide-pa.googleapis.com 127.0.0.1,"
    "MAP edgedl.me.gvt1.com 127.0.0.1,MAP update.googleapis.com 127.0.0.1"
)


def test_suppression_passes_on_the_real_flag_set():
    G.assert_suppression_flags(GOOD_CMDLINE)


@pytest.mark.parametrize(
    "drop",
    [
        "--disable-component-update",
        "--disable-background-networking",
        "OptimizationGuideModelDownloading",
        "edgedl.me.gvt1.com",
    ],
)
def test_suppression_fails_if_any_piece_is_missing(drop):
    with pytest.raises(G.SuppressionNotActive):
        G.assert_suppression_flags(GOOD_CMDLINE.replace(drop, "xxx"))


def test_suppression_fails_when_no_cmdline_was_captured():
    with pytest.raises(G.SuppressionNotActive):
        G.assert_suppression_flags("")


class _Li:
    def __init__(self, ok, checked, offenders=(), warnings=()):
        self.ok, self.checked = ok, checked
        self.offenders, self.warnings = list(offenders), list(warnings)
        self.app_window_s = [1.0, 59.0]
        self.note = ""


def _patch_li(monkeypatch, li):
    fake = types.SimpleNamespace(link_interference=lambda *a, **k: li)
    monkeypatch.setitem(sys.modules, "pramana_helpers", fake)


def test_contention_inside_the_window_raises(monkeypatch):
    _patch_li(monkeypatch, _Li(False, True, offenders=["browser_infra/download ..."]))
    with pytest.raises(G.BackgroundContention):
        G.assert_no_background_contention(object(), ["youtube"], 25.0)


def test_clean_capture_passes(monkeypatch):
    _patch_li(monkeypatch, _Li(True, True))
    v = G.assert_no_background_contention(object(), ["youtube"], 25.0)
    assert v.ok and v.checked


def test_nothing_to_judge_is_not_a_pass(monkeypatch):
    _patch_li(monkeypatch, _Li(True, False))
    with pytest.raises(G.BackgroundContention):
        G.assert_no_background_contention(object(), ["youtube"], 25.0)


def test_guard_unavailable_when_link_interference_is_absent(monkeypatch):
    monkeypatch.setitem(sys.modules, "pramana_helpers", types.SimpleNamespace())
    with pytest.raises(G.GuardUnavailable) as e:
        G.assert_no_background_contention(object(), ["youtube"], 25.0)
    assert "collector-driver-prefetch" in str(e.value)


# ── runner orchestration ────────────────────────────────────────────────────
class _Adapter:
    name = "streaming"
    category = "streaming"

    def __init__(self, events, ok=True, cmdline=GOOD_CMDLINE):
        self.events, self.ok, self.cmdline = events, ok, cmdline

    def run_app(self, app, condition, duration_s, out_dir):
        self.events.append(("app", app, duration_s))
        return R.AppRunResult(
            ok=self.ok,
            player_qoe={app: {}},
            browser_cmdline=self.cmdline,
            attribution=object(),
            error="" if self.ok else "boom",
        )

    def label(self, result, app, reference):
        return "high", "ok", {"qoe_startup_delay_s": 1.0}


def _cond():
    return C.stage1_streaming_conditions()[0]


def _wire(monkeypatch, events, calib_ok=True, li=None):
    monkeypatch.setattr(
        R, "apply_condition", lambda *a, **k: events.append(("shape",)) or {}
    )
    monkeypatch.setattr(
        R,
        "calibrate",
        lambda *a, **k: S.CalibrationResult(
            ok=calib_ok, skip_reason="" if calib_ok else "rate off"
        ),
    )
    _patch_li(monkeypatch, li or _Li(True, True))

    def fake_ndt(server, raw_path, **kw):
        events.append(("ndt", server))
        return N.NdtResult(ok=True, download_mbps=94.2, upload_mbps=9.7)

    monkeypatch.setattr(R.ndt_mod, "run_ndt", fake_ndt)


def test_app_and_ndt_are_separate_rows(monkeypatch, tmp_path):
    ev = []
    _wire(monkeypatch, ev)
    csv_path = str(tmp_path / "cpuc_runs.csv")
    rows = R.execute_condition_rep(
        _cond(),
        1,
        ("youtube", "twitch", "vimeo"),
        _Adapter(ev),
        out_root=str(tmp_path / "runs"),
        csv_path=csv_path,
        ndt_server="srv:4443",
        reference_metrics={
            a: {"resolution_p": 1080} for a in ("youtube", "twitch", "vimeo")
        },
        sleep=lambda s: ev.append(("sleep", s)),
    )
    # 3 app rows + exactly ONE ndt row -- not one per app.
    assert [r["run_type"] for r in rows] == ["app", "app", "app", "ndt"]
    assert sum(1 for r in rows if r["run_type"] == "ndt") == 1
    assert [r["app"] for r in rows] == ["youtube", "twitch", "vimeo", ""]
    # All four rows share the group that links them to the same speed test.
    assert len({r["group_id"] for r in rows}) == 1
    with open(csv_path) as fh:
        assert len(list(csv.DictReader(fh))) == 4


def test_the_full_matrix_is_72_app_rows_and_24_ndt_rows():
    assert len(C.stage1_streaming_plan()) == 72
    assert len(C.stage1_streaming_ndt_plan()) == 24
    assert {p.run_type for p in C.stage1_streaming_plan()} == {"app"}
    assert {p.run_type for p in C.stage1_streaming_ndt_plan()} == {"ndt"}
    # NDT rows are not app-specific.
    assert {p.app for p in C.stage1_streaming_ndt_plan()} == {""}


def test_ndt_waits_the_gap_and_runs_after_every_app(monkeypatch, tmp_path):
    ev = []
    _wire(monkeypatch, ev)
    R.execute_condition_rep(
        _cond(),
        1,
        ("youtube", "twitch"),
        _Adapter(ev),
        out_root=str(tmp_path / "r"),
        csv_path=str(tmp_path / "c.csv"),
        ndt_server="s",
        reference_metrics={},
        sleep=lambda s: ev.append(("sleep", s)),
    )
    kinds = [e[0] for e in ev]
    assert kinds.index("ndt") > max(i for i, k in enumerate(kinds) if k == "app")
    assert ("sleep", R.GAP_BEFORE_NDT_S) in ev


def test_app_row_carries_no_ndt_columns(monkeypatch, tmp_path):
    ev = []
    _wire(monkeypatch, ev)
    row = R.execute_app_run(
        C.RunPlan("youtube", _cond(), 1, "app"),
        _Adapter(ev),
        out_root=str(tmp_path / "r"),
        csv_path=str(tmp_path / "c.csv"),
        reference_metrics={"youtube": {"resolution_p": 1080}},
    )
    assert row["run_type"] == "app"
    assert "ndt_down" not in row
    assert "ndt" not in [e[0] for e in ev]


def test_failed_calibration_skips_the_app(monkeypatch, tmp_path):
    ev = []
    _wire(monkeypatch, ev, calib_ok=False)
    row = R.execute_app_run(
        C.RunPlan("youtube", _cond(), 1, "app"),
        _Adapter(ev),
        out_root=str(tmp_path / "r"),
        csv_path=str(tmp_path / "c.csv"),
    )
    assert "app" not in [e[0] for e in ev]
    assert row["valid"] is False and "calibration gate" in row["invalid_reason"]


def test_contended_run_is_invalid(monkeypatch, tmp_path):
    ev = []
    _wire(monkeypatch, ev, li=_Li(False, True, ["infra"]))
    row = R.execute_app_run(
        C.RunPlan("youtube", _cond(), 1, "app"),
        _Adapter(ev),
        out_root=str(tmp_path / "r"),
        csv_path=str(tmp_path / "c.csv"),
    )
    assert row["valid"] is False and "BackgroundContention" in row["invalid_reason"]


def test_unsuppressed_browser_is_invalid(monkeypatch, tmp_path):
    ev = []
    _wire(monkeypatch, ev)
    row = R.execute_app_run(
        C.RunPlan("youtube", _cond(), 1, "app"),
        _Adapter(ev, cmdline="/chrome"),
        out_root=str(tmp_path / "r"),
        csv_path=str(tmp_path / "c.csv"),
    )
    assert "SuppressionNotActive" in row["invalid_reason"]


def test_rows_append_and_never_overwrite(monkeypatch, tmp_path):
    ev = []
    _wire(monkeypatch, ev)
    csv_path = str(tmp_path / "c.csv")
    for _ in range(2):
        R.execute_app_run(
            C.RunPlan("youtube", _cond(), 1, "app"),
            _Adapter(ev),
            out_root=str(tmp_path / "r"),
            csv_path=csv_path,
            reference_metrics={"youtube": {"resolution_p": 1080}},
        )
    with open(csv_path) as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2 and len({r["run_id"] for r in rows}) == 2


def test_youtube_stays_on_auto_quality(monkeypatch, tmp_path):
    """Decision: forcing quality disables the ABR this study measures."""
    assert C.FORCE_MAX_QUALITY is False
    ev = []
    _wire(monkeypatch, ev)
    row = R.execute_app_run(
        C.RunPlan("youtube", _cond(), 1, "app"),
        _Adapter(ev),
        out_root=str(tmp_path / "r"),
        csv_path=str(tmp_path / "c.csv"),
        reference_metrics={"youtube": {"resolution_p": 1080}},
    )
    assert row["force_max_quality"] is False


def test_vimeo_replaced_puffer():
    assert C.STREAMING_APPS_PLAN == ("youtube", "twitch", "vimeo")
    assert "puffer" not in C.STREAMING_APPS_PLAN


def test_conferencing_adapter_refuses_rather_than_faking(monkeypatch, tmp_path):
    with pytest.raises(NotImplementedError) as e:
        R.ConferencingAdapter().run_app("meet", C.REFERENCE, 60, "/tmp/x")
    assert "Sthanav" in str(e.value)


# ── namespace strictness and browser cmdline capture ────────────────────────
def test_ns_prefix_refuses_to_degrade_to_the_host_namespace(monkeypatch):
    """A silent fallback would probe the unshaped host link and pass the gate."""
    import cpuc.netns as NS

    monkeypatch.setattr(
        NS,
        "worker_pid",
        lambda *a, **k: (_ for _ in ()).throw(NS.NamespaceError("down")),
    )
    with pytest.raises(NS.NamespaceError):
        S._ns("ns1")


def test_browser_cmdline_picks_the_process_with_the_flags(monkeypatch, tmp_path):
    import cpuc.browser as B

    procs = {
        "100": "/opt/google/chrome/chrome --type=renderer",  # helper, no flags
        "101": "/opt/google/chrome/chrome --remote-debugging-port=9222 "
        "--disable-component-update --disable-background-networking",
        "102": "/usr/bin/python3 collect.py",  # not a browser
    }
    monkeypatch.setattr(
        B,
        "glob",
        type("g", (), {"glob": staticmethod(lambda p: [f"/proc/{k}" for k in procs])}),
    )
    monkeypatch.setattr(B, "_read_cmdline", lambda pid: procs.get(pid))
    found = B.find_browser_cmdline()
    assert found == procs["101"]


def test_browser_cmdline_absent_means_the_guard_fails(monkeypatch):
    import cpuc.browser as B

    monkeypatch.setattr(B, "glob", type("g", (), {"glob": staticmethod(lambda p: [])}))
    assert B.find_browser_cmdline() is None
    with pytest.raises(G.SuppressionNotActive):
        G.assert_suppression_flags(B.find_browser_cmdline() or "")


def test_condition_is_applied_once_before_the_app(monkeypatch, tmp_path):
    """Regression: the first pilot failed because the runner shaped with host
    `tc`, where veth2/veth4 do not exist -- they are in the worker container's
    namespace. The condition must go through apply_condition (worker API plus
    nsenter loss), exactly once, before the app starts."""
    ev = []
    _wire(monkeypatch, ev)
    R.execute_app_run(
        C.RunPlan("youtube", _cond(), 1, "app"),
        _Adapter(ev),
        out_root=str(tmp_path / "r"),
        csv_path=str(tmp_path / "c.csv"),
        reference_metrics={"youtube": {"resolution_p": 1080}},
    )
    kinds = [e[0] for e in ev]
    assert kinds.count("shape") == 1
    assert kinds.index("shape") < kinds.index("app")


def test_shaping_helpers_are_not_used_on_the_app_path():
    """`shaping()` and `latency_and_loss()` drive host tc and must not be on
    the run path for this topology; apply_condition replaced them."""
    import inspect

    src = inspect.getsource(R.execute_app_run) + inspect.getsource(R.execute_ndt_run)
    assert "shaping(" not in src
    assert "latency_and_loss(" not in src


def test_record_dict_attribution_uses_the_record_verdict(monkeypatch, tmp_path):
    """Regression: the adapter returns run_direct's record (a dict), and the
    object-based guard raised AttributeError on it mid-pilot. A dict must be
    enforced through verdict_from_record instead."""
    ev = []
    _wire(monkeypatch, ev)

    class _RecAdapter(_Adapter):
        def run_app(self, app, condition, duration_s, out_dir):
            self.events.append(("app", app, duration_s))
            return R.AppRunResult(
                ok=True,
                player_qoe={app: {}},
                browser_cmdline=GOOD_CMDLINE,
                attribution={  # a record, not a CaptureAttribution
                    "link_interference": {
                        "ok": True,
                        "checked": True,
                        "offenders": [],
                        "warnings": [],
                        "app_window_s": [1.0, 59.0],
                    }
                },
            )

    row = R.execute_app_run(
        C.RunPlan("youtube", _cond(), 1, "app"),
        _RecAdapter(ev),
        out_root=str(tmp_path / "r"),
        csv_path=str(tmp_path / "c.csv"),
        reference_metrics={"youtube": {"resolution_p": 1080}},
    )
    assert row["guard_ok"] is True
    assert row["valid"] is True


def test_record_without_interference_block_is_not_a_pass():
    v = G.verdict_from_record({})
    assert v.ok is False and v.checked is False
    with pytest.raises(G.BackgroundContention):
        G.enforce_record_verdict({})


def test_missing_reference_defers_the_label_rather_than_invalidating(
    monkeypatch, tmp_path
):
    """Stage 1 runs the 100 ms conditions first and the reference is a 20 ms
    condition, so most rows are measured before their reference exists. Those
    are good measurements with a deferred label, not invalid runs."""
    label, reason = Q.label_streaming(_m(), None)
    assert label == "deferred" and "analysis" in reason

    ev = []
    _wire(monkeypatch, ev)

    class _Defer(_Adapter):
        def label(self, result, app, reference):
            m = Q.StreamingMetrics(
                startup_delay_s=2.9,
                resolution_p=720,
                rebuffer_ratio=0.0,
                rebuffer_events=0,
                rebuffer_duration_ms=0.0,
                session_seconds=59.5,
                played=True,
            )
            lab, rsn = Q.label_streaming(
                m, (reference or {}).get(app, {}).get("resolution_p")
            )
            return lab, rsn, m.as_row()

    row = R.execute_app_run(
        C.RunPlan("youtube", _cond(), 1, "app"),
        _Defer(ev),
        out_root=str(tmp_path / "r"),
        csv_path=str(tmp_path / "c.csv"),
        reference_metrics={},  # no reference yet
    )
    assert row["qoe_label"] == "deferred"
    assert row["valid"] is True
    assert row["qoe_startup_delay_s"] == 2.9


ZERO_SUMMARY = {
    "ServerFQDN": "",
    "ServerIP": "",
    "ClientIP": "",
    "Download": {
        "UUID": "",
        "Throughput": {"Value": 0, "Unit": ""},
        "Latency": {"Value": 0, "Unit": ""},
        "Retransmission": {"Value": 0, "Unit": ""},
    },
    "Upload": {
        "UUID": "",
        "Throughput": {"Value": 0, "Unit": ""},
        "Latency": {"Value": 0, "Unit": ""},
        "Retransmission": {"Value": 0, "Unit": ""},
    },
}


def test_all_zero_ndt_summary_is_a_failure_not_a_measurement():
    """Regression: the pilot wrote ndt_down=0.0 as valid. ndt7-client emits a
    well-formed zero summary when the test never ran, and 0 Mb/s in the dataset
    would read as a link failing every IQB threshold."""
    text = (
        json.dumps(
            {
                "Key": "error",
                "Value": {
                    "Test": "download",
                    "Failure": "lookup locate.measurementlab.net: connection refused",
                },
            }
        )
        + "\n"
        + json.dumps(ZERO_SUMMARY)
    )
    r = N.parse_ndt_json(text)
    assert r.ok is False
    assert "all zeros" in r.error
    assert "locate.measurementlab.net" in r.error


def test_ndt_failure_events_are_extracted():
    text = json.dumps({"Key": "error", "Value": {"Test": "upload", "Failure": "boom"}})
    assert N._failures(text) == ["upload: boom"]


def test_ndt_runs_inside_the_container_namespace_not_via_nsenter():
    """nsenter keeps the host resolver, which cannot resolve from ns1."""
    pre = N._ns_prefix("ns1")
    assert pre.startswith("docker exec ")
    assert "ip netns exec ns1" in pre
    assert "nsenter" not in pre


# ── the silent-success class ────────────────────────────────────────────────
# Every fixture below is the shape of a REAL record. The "bad" one is
# youtube_100mbps_20ms_..._c26c6ccd65, whose pcap was 372 bytes and whose
# collector exited 1, and which drive() nonetheless reported as ok=True. The
# "good" one is youtube_100mbps_20ms_..._30a34212f8 from the published sweep.
def _good_record():
    return {
        "network_stats": {"packets": 18963, "total_mb": 22.7},
        "per_app_stats": {
            "youtube": {
                "download": {
                    "total_mb": 20.064,
                    "packets": 13493,
                    "classification": "starved",
                }
            }
        },
        "player_qoe": {"youtube": {"status": "ok", "collector_returncode": 0}},
    }


def test_a_real_run_is_still_usable():
    # Guards the other direction: the checks must not reject good runs. A
    # "starved" classification is a finding about the link, not a failure.
    assert D.record_failures(_good_record(), "youtube") == []


def test_empty_capture_is_not_a_successful_run():
    rec = _good_record()
    rec["network_stats"] = {"packets": 0, "total_mb": 0.0}
    reasons = D.record_failures(rec, "youtube")
    assert any("capture is empty" in r for r in reasons), reasons


def test_app_that_moved_no_bytes_is_not_a_successful_run():
    rec = _good_record()
    rec["per_app_stats"]["youtube"]["download"] = {
        "total_mb": 0.0,
        "packets": 0,
        "classification": "no_traffic",
    }
    reasons = D.record_failures(rec, "youtube")
    assert any("moved no bytes" in r for r in reasons), reasons


def test_nonzero_collector_exit_is_not_a_successful_run():
    rec = _good_record()
    rec["player_qoe"]["youtube"]["collector_returncode"] = 1
    reasons = D.record_failures(rec, "youtube")
    assert any("collector exited 1" in r for r in reasons), reasons


def test_missing_collector_exit_status_is_not_a_pass():
    # A check that cannot run must say so. Treating absent as "fine" is how
    # the three earlier instances of this class stayed hidden.
    rec = _good_record()
    del rec["player_qoe"]["youtube"]["collector_returncode"]
    reasons = D.record_failures(rec, "youtube")
    assert any("exit status missing" in r for r in reasons), reasons


def test_collector_failure_is_fatal_even_when_the_app_moved_bytes():
    # DELIBERATE, and it had a consequence worth knowing: the low-capacity
    # Meet runs behind those rows have real traffic AND a non-zero collector
    # exit, so they become unusable here. That re-derivation has since been
    # done from the captures (report_meet.INBOUND_MEDIA_BY_CAPTURE), and it
    # overturned the published claim: remote video WAS arriving at three of
    # the five points. "No remote video arrived" can only be read off the
    # capture, never inferred from the collector having died.
    rec = _good_record()
    rec["per_app_stats"]["youtube"]["download"] = {
        "total_mb": 3.348,
        "packets": 10748,
        "classification": "served",
    }
    rec["player_qoe"]["youtube"] = {"status": "skipped", "collector_returncode": 1}
    assert D.record_failures(rec, "youtube") != []


def test_the_372_byte_run_fails_on_every_count():
    # The exact run that started this: it should have been impossible to
    # report as ok, by three independent routes.
    rec = {
        "network_stats": {"packets": 0, "total_mb": 0.0},
        "per_app_stats": {
            "youtube": {
                "download": {
                    "total_mb": 0.0,
                    "packets": 0,
                    "classification": "no_traffic",
                }
            }
        },
        "player_qoe": {"youtube": {"status": "skipped", "collector_returncode": 1}},
    }
    assert len(D.record_failures(rec, "youtube")) == 3


def test_failure_order_is_stable():
    rec = {"network_stats": {}, "per_app_stats": {}, "player_qoe": {}}
    assert D.record_failures(rec, "youtube") == D.record_failures(rec, "youtube")


def _stub_drive_env(monkeypatch, record):
    """Make drive() runnable in-process, with run_direct returning `record`."""

    class _Watcher:
        cmdline = "/usr/bin/chrome --flag"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Cfg:
        def __init__(self, **kw):
            pass

    helpers = types.SimpleNamespace(
        ExperimentConfig=_Cfg, run_direct=lambda cfg: record
    )
    monkeypatch.setattr(D, "_helpers", lambda: helpers)
    monkeypatch.setattr(D.netns, "start_iperf3_server", lambda: None)
    monkeypatch.setattr(D.netns, "install_shape_hook", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(D.browser, "CmdlineWatcher", lambda **k: _Watcher())


def test_drive_reports_a_bad_record_as_not_ok(monkeypatch):
    # The wiring, not just the predicate. Without this, reverting `ok` to a
    # constant True passes every other test in this file -- which is exactly
    # how the original bug survived.
    bad = {
        "network_stats": {"packets": 0, "total_mb": 0.0},
        "per_app_stats": {
            "youtube": {"download": {"total_mb": 0.0, "classification": "no_traffic"}}
        },
        "player_qoe": {"youtube": {"collector_returncode": 1}},
    }
    _stub_drive_env(monkeypatch, bad)
    res = D.drive("youtube", C.REFERENCE, 60, "/tmp/unused")
    assert res.ok is False
    assert "capture is empty" in res.error
    assert "collector exited 1" in res.error


def test_drive_reports_a_good_record_as_ok(monkeypatch):
    _stub_drive_env(monkeypatch, _good_record())
    res = D.drive("youtube", C.REFERENCE, 60, "/tmp/unused")
    assert res.ok is True
    assert res.error == ""


# ── report labels: what the capture says, not what the collector did ────────
#
# These pin the correction of 2026-11-08. Three Meet rows were published as
# "no remote media", and the finding quoted them as "no remote video arrived
# at all", on the strength of the collector having exited non-zero. The
# captures show 2.2-3.2 MB of inbound media from Meet's own endpoints in both
# attempts at each point. A label must not outrun its evidence.
def _meet_row(rate, rtt, reason="NoRemoteInboundMedia: meet: no remote inbound video"):
    return {
        "rate_mbps_symmetric": rate,
        "rtt_configured_ms": rtt,
        "is_reference": False,
        "qoe_label": "collector produced no samples",
        "invalid_reason": reason,
        "resolution_p": None,
        "fps": None,
        "freeze_ratio": None,
    }


def _rm():
    pytest.importorskip("matplotlib")
    import cpuc.report_meet as RM

    return RM


def test_a_collector_failure_claims_nothing_about_remote_media():
    RM = _rm()
    # The label a run earns from the collector's own output says only that no
    # samples came back. It must not mention remote media, because the
    # collector cannot see the wire: a dead collector and a silent peer look
    # identical to it. That was the whole mistake.
    assert "remote" not in RM.LABEL_NO_SAMPLES


def test_a_point_with_no_capture_evidence_keeps_the_unknown_label():
    RM = _rm()
    # 6 Mb/s was never in the re-derived set. Absent evidence must leave the
    # label where it was rather than filling in either verdict.
    out = RM.label_from_capture(_meet_row(6.0, 20.0))
    assert out["qoe_label"] == RM.LABEL_NO_SAMPLES
    assert "inbound_media_mb_min" not in out
    assert "invalid_run" not in out


def test_capture_evidence_relabels_the_three_rows_that_carried_video():
    RM = _rm()
    for rate, rtt in ((1.0, 50.0), (0.5, 20.0), (0.5, 50.0)):
        out = RM.label_from_capture(_meet_row(rate, rtt))
        assert out["qoe_label"] == RM.LABEL_UNMEASURED, (rate, rtt)
        # The evidence travels with the label, both attempts of it.
        assert out["attempts"] == 2
        assert out["inbound_media_mb_min"] >= 2.0
        assert out["inbound_media_packets_min"] >= 1000
        assert out.get("resolution_p") is None  # still unmeasured, not graded


def test_an_empty_capture_is_an_invalid_run_not_a_result():
    RM = _rm()
    for rate in (1.0, 0.5):
        out = RM.label_from_capture(_meet_row(rate, 100.0))
        assert out["qoe_label"] == RM.LABEL_INVALID
        assert out["invalid_run"] is True
        assert "no inbound packets" in out["invalid_run_reason"]


def test_an_unmeasured_row_is_not_counted_as_a_measured_failure():
    RM = _rm()
    table = [
        {
            "rate_mbps_symmetric": 100.0,
            "rtt_configured_ms": 50.0,
            "qoe_label": "high",
            "is_reference": True,
        },
        {
            "rate_mbps_symmetric": 3.0,
            "rtt_configured_ms": 50.0,
            "qoe_label": "high",
            "is_reference": False,
        },
        {
            "rate_mbps_symmetric": 1.0,
            "rtt_configured_ms": 50.0,
            "qoe_label": RM.LABEL_UNMEASURED,
            "is_reference": False,
        },
        {
            "rate_mbps_symmetric": 100.0,
            "rtt_configured_ms": 20.0,
            "qoe_label": "high",
            "is_reference": True,
        },
        {
            "rate_mbps_symmetric": 1.0,
            "rtt_configured_ms": 20.0,
            "qoe_label": "unusable",
            "is_reference": False,
        },
    ]
    text = RM.latency_sentences(table)
    # 20 ms has a measured failure and may say so; 50 ms does not.
    assert "at 20 ms" in text and "unusable by 1 Mb/s" in text
    assert "at 50 ms it held reference quality to 3 Mb/s (quality at 1 Mb/s" in text
    assert "failed by" not in text


def test_the_finding_does_not_say_no_video_arrived():
    RM = _rm()
    table = [
        {
            "rate_mbps_symmetric": 100.0,
            "is_reference": True,
            "resolution_p": 720,
            "fps": 24.0,
            "freeze_ratio": 0.0,
            "qoe_label": "high",
            "meet_demand_mbps": 3.74,
            "iqb_verdict": "meets minimum only",
        },
        {
            "rate_mbps_symmetric": 1.0,
            "is_reference": False,
            "resolution_p": 360,
            "fps": 19.9,
            "freeze_ratio": 0.1795,
            "qoe_label": "unusable",
            "meet_demand_mbps": 0.51,
            "iqb_verdict": "fails minimum",
        },
        dict(
            _meet_row(0.5, 20.0),
            qoe_label=RM.LABEL_UNMEASURED,
            inbound_media_mb_min=2.96,
            inbound_media_mb_max=3.20,
            meet_demand_mbps=None,
            iqb_verdict="fails minimum",
        ),
    ]
    text = RM.sentences(RM.find_breakpoint(table), table)
    # The old sentence shape -- "At <rate> Mb/s no remote video arrived at
    # all" -- must be gone. The phrase survives only inside the correction,
    # quoted as what was wrong, which is the record we want kept.
    assert "Mb/s no remote video arrived" not in text
    assert 'as "no remote video arrived at all", which was inferred' in text
    assert "UNMEASURED" in text
    assert "3.0-3.2 MB" in text
    assert "lowest capacity with measured quality is 1 Mb/s" in text


# ── streaming: a missing metric is not a good one ───────────────────────────
def test_a_row_missing_rebuffer_or_startup_is_not_graded_good():
    pytest.importorskip("matplotlib")
    import cpuc.report as RP

    def row(rate, **kw):
        base = {
            "rate_down_mbps": rate,
            "is_reference": False,
            "resolution_p": 720,
            "rebuffer_events": 0,
            "startup_s": 1.0,
            "iqb_verdict_download_only": "fails minimum",
            "iqb_verdict": "fails minimum",
        }
        base.update(kw)
        return base

    ref = row(100.0, is_reference=True)
    table = [ref, row(25.0), row(6.25, rebuffer_events=None), row(3.0, startup_s=None)]
    f = RP.find_degradation(table)
    # Under `or 0` both holes graded as flawless and 3 Mb/s became the
    # headline. They must be neither good nor degraded, and must be named.
    assert f["lowest_good_mbps"] == 25.0
    assert f["ungradeable_mbps"] == [3.0, 6.25]
    assert 6.25 not in f["points_good_below_iqb"]
