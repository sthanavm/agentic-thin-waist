"""The collector's exit status must reach the record.

Without this field a caller cannot tell a dead collector from an app with
nothing to show: `status` is "ok" whenever samples exist, so a collector that
wrote samples and then died at teardown looks clean. cpuc's drive() treated a
run with a 372-byte pcap as successful for exactly this reason.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pramana_helpers as H  # noqa: E402


def test_collector_returncode_is_recorded_per_app(monkeypatch):
    cfg = H.ExperimentConfig(apps=["youtube"], bandwidth_mbps=10.0)

    class _Spec:
        has_player_qoe = True

    reg = type("R", (), {"get": staticmethod(lambda a: _Spec())})()
    qoelib = type(
        "Q",
        (),
        {
            "summarize": staticmethod(lambda *a, **k: {"status": "ok"}),
            "load_samples": staticmethod(lambda v: []),
        },
    )()
    monkeypatch.setattr(H, "_bind_shared", lambda: (reg, qoelib))
    monkeypatch.setattr(
        H,
        "run_qoe_collectors",
        lambda *a, **k: {"stats": {"youtube": []}, "skipped": {}, "returncode": 7},
    )
    monkeypatch.setattr(H, "_measured_element_summary", lambda s: {})

    out = H.collect_player_qoe(cfg, H.Path("/tmp"))
    assert out["youtube"]["collector_returncode"] == 7


def test_a_clean_collector_records_zero(monkeypatch):
    cfg = H.ExperimentConfig(apps=["youtube"], bandwidth_mbps=10.0)

    class _Spec:
        has_player_qoe = True

    reg = type("R", (), {"get": staticmethod(lambda a: _Spec())})()
    qoelib = type(
        "Q",
        (),
        {
            "summarize": staticmethod(lambda *a, **k: {"status": "ok"}),
            "load_samples": staticmethod(lambda v: []),
        },
    )()
    monkeypatch.setattr(H, "_bind_shared", lambda: (reg, qoelib))
    monkeypatch.setattr(
        H,
        "run_qoe_collectors",
        lambda *a, **k: {"stats": {"youtube": []}, "skipped": {}, "returncode": 0},
    )
    monkeypatch.setattr(H, "_measured_element_summary", lambda s: {})

    out = H.collect_player_qoe(cfg, H.Path("/tmp"))
    assert out["youtube"]["collector_returncode"] == 0
