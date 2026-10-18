#!/usr/bin/env python3
"""Step A sweep: YouTube and Vimeo, 1/3/6 Mbps, 2 trials, one cell at a time.

Serial by construction. Two runs at once would share the 2 vCPUs and the shaped
link, so neither would measure what it claims to, and one app per run keeps a
capture and a chart attributable to a single application.

Checkpointed: a tag already recorded in the checkpoint file is skipped, so an
interrupted sweep resumes instead of repeating work.

    DUR=180 python run_matrix.py
"""
import json
import os
import subprocess
import sys
import time
import traceback

HOME = os.path.expanduser("~/sthanav-agentic-thin-waist")
sys.path.insert(0, os.path.join(HOME, "experiments/pramana"))
sys.path.insert(0, HOME)
import pramana_helpers as H  # noqa: E402

os.environ.setdefault(
    "PRAMANA_COLLECTOR_SRC",
    os.path.join(HOME, "services/orchestration/scripts/selenium_video_qoe/collect.py"),
)
os.environ.setdefault("PRAMANA_DEFER_ANALYSIS", "1")

# Host-load gate. The rule and its justification are in
# results_release/validation/THRESHOLDS.md, committed before any gated run.
GATE_MAX_LOAD1 = 4.0  # 2-vCPU box; measured idle floor is ~3.0
GATE_HOLD_S = 180  # must stay under the bar this long
GATE_POLL_S = 15
GATE_TIMEOUT_S = 1200  # then run anyway, flagged high-load


def host_load1():
    with open("/proc/loadavg") as fh:
        return float(fh.read().split()[0])


def chrome_procs():
    """Count chrome AND chrome-headless: the other workload's are the latter,
    so counting only `chrome` reports zero while 159 are running."""
    n = 0
    for name in ("chrome", "chrome-headless"):
        try:
            out = subprocess.run(
                ["pgrep", "-c", "-x", name], capture_output=True, text=True
            )
            n += int((out.stdout or "0").strip() or 0)
        except Exception:
            pass
    return n


def wait_for_quiet_host(tag):
    """Hold until the host is quiet, or give up and say so.

    Returns (gate_result, waited_s, load1_at_start, chrome_at_start).
    A timeout is NOT a failure to record -- the run proceeds and is labelled.
    """
    t0 = time.time()
    held = 0.0
    while True:
        l1 = host_load1()
        if l1 <= GATE_MAX_LOAD1:
            held += GATE_POLL_S
            if held >= GATE_HOLD_S:
                waited = time.time() - t0
                print(
                    "GATE %s passed after %.0fs (load1=%.2f)" % (tag, waited, l1),
                    flush=True,
                )
                return "passed", waited, l1, chrome_procs()
        else:
            held = 0.0
        waited = time.time() - t0
        if waited >= GATE_TIMEOUT_S:
            print(
                "GATE %s TIMEOUT after %.0fs (load1=%.2f) - running anyway, "
                "flagged high-load" % (tag, waited, l1),
                flush=True,
            )
            return "timeout", waited, l1, chrome_procs()
        time.sleep(GATE_POLL_S)


APPS = ("youtube", "vimeo")
BANDWIDTHS = (1, 3, 6)
TRIALS = (1, 2)
LATENCY_MS = 50  # baseline for every solo run in this project
DUR = int(os.environ.get("DUR", "180"))
CKPT = os.environ.get("PRAMANA_CKPT", "/tmp/stepa_done.json")


def done():
    try:
        return set(json.load(open(CKPT)))
    except Exception:
        return set()


def mark(tag):
    d = done()
    d.add(tag)
    json.dump(sorted(d), open(CKPT, "w"))


def run_cell(app, bw, trial):
    tag = "%s_%gM_t%d" % (app, bw, trial)
    if tag in done():
        print("SKIP %s (already done)" % tag, flush=True)
        return
    print("=== %s ===" % tag, flush=True)
    gate, waited, l1_start, chrome_start = wait_for_quiet_host(tag)
    try:
        cfg = H.ExperimentConfig(
            apps=[app],
            bandwidth_mbps=float(bw),
            latency_ms=LATENCY_MS,
            loss_pct=0.0,
            aqm="pfifo",
            cca="cubic",
            duration_s=DUR,
            trial=trial,
        )
        rec = H.run_direct(cfg)
    except Exception:
        print("FAILED %s" % tag, flush=True)
        traceback.print_exc()
        return
    # Conditions this cell actually ran under, written beside the run so a
    # reader sees them instead of inferring them.
    try:
        d = rec.get("run_dir") if isinstance(rec, dict) else None
        if not d:
            import glob

            cand = sorted(
                glob.glob(
                    os.path.join(
                        HOME,
                        "experiments/pramana/results/pramana_runs",
                        "%s_%gmbps_*_t%d_*" % (app, float(bw), trial),
                    )
                ),
                key=os.path.getmtime,
            )
            d = cand[-1] if cand else None
        if d:
            with open(os.path.join(d, "host_conditions.json"), "w") as fh:
                json.dump(
                    {
                        "host_load_gate": gate,
                        "host_load_gate_wait_s": round(waited, 1),
                        "load1_at_start": l1_start,
                        "chrome_procs_at_start": chrome_start,
                        "gate_rule": {
                            "max_load1": GATE_MAX_LOAD1,
                            "hold_s": GATE_HOLD_S,
                            "timeout_s": GATE_TIMEOUT_S,
                        },
                    },
                    fh,
                    indent=1,
                )
    except Exception as exc:  # noqa: BLE001
        print("could not write host_conditions.json: %s" % exc, flush=True)
    if isinstance(rec, dict):
        q = (rec.get("player_qoe") or {}).get(app) or {}
        u = q.get("uniform") or {}
        print(
            "ROW %s %s"
            % (
                tag,
                json.dumps(
                    {
                        "status": q.get("status"),
                        "res_p": q.get("video_resolution_p"),
                        "startup_delay_ms": u.get("startup_delay_ms"),
                        "rebuffer_events": u.get("rebuffer_events"),
                        "video_mbps": u.get("delivered_video_bitrate_mbps"),
                        "audio_mbps": u.get("delivered_audio_bitrate_mbps"),
                        "switch_count": u.get("switch_count"),
                        "rendered_fps": u.get("rendered_fps"),
                    }
                ),
            ),
            flush=True,
        )
    mark(tag)
    time.sleep(5)


if __name__ == "__main__":
    for app in APPS:
        for bw in BANDWIDTHS:
            for trial in TRIALS:
                run_cell(app, bw, trial)
    print("STEP_A_DONE", flush=True)
