#!/usr/bin/env python3
"""One concurrent cell: YouTube and Vimeo sharing a single shaped link.

This is the case Jaber's rule 7 is about -- the cap is the TOTAL for the link,
not an allowance per app -- so the two apps are launched into the same
namespace behind one 6 Mbps cap and must share it between them. 6 Mbps is
chosen because both apps have to be able to play: at 1 Mbps a shared link
starves one of them and the run would measure starvation, not sharing.

The collector gives each app its own display and serialises the driver launch,
because DISPLAY is process-global. A barrier then holds both apps until each
has a ready player, so neither spends its window waiting for the other to load.

What the barrier does NOT guarantee -- measured, not assumed -- is an identical
sampling window. After the barrier each app independently waits for media-ready,
triggers play and drains its own CDP backlog, and the player may still reload
under contention. In the committed run the two windows are 178.8 s (vimeo, 180
samples) and 161.1 s (youtube, 160 samples), with youtube's first sample 18.9 s
after vimeo's and its last sample 1.2 s after vimeo's. The windows therefore
overlap for all but the final 1.2 s, so the shared-cap check is summed on a
common 1 s grid rather than assuming equal windows, and
each app's own metrics are computed over its own window. Compare the two apps
run-to-run only with that difference in mind.

Uses the same host-load gate as every other run.
"""

import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, HOME)
import run_matrix as M  # noqa: E402  (the host-load gate lives there)
import pramana_helpers as H  # noqa: E402

os.environ.setdefault(
    "PRAMANA_COLLECTOR_SRC",
    os.path.join(HOME, "services/orchestration/scripts/selenium_video_qoe/collect.py"),
)
os.environ.setdefault("PRAMANA_DEFER_ANALYSIS", "1")

APPS = ["youtube", "vimeo"]
BW = float(os.environ.get("BW", "6"))
DUR = int(os.environ.get("DUR", "180"))
TRIAL = int(os.environ.get("TRIAL", "1"))

if __name__ == "__main__":
    tag = "concurrent_%s_%gM_t%d" % ("+".join(APPS), BW, TRIAL)
    gate, waited, l1, chrome = M.wait_for_quiet_host(tag)
    print("=== %s ===" % tag, flush=True)
    try:
        cfg = H.ExperimentConfig(
            apps=APPS,
            bandwidth_mbps=BW,
            latency_ms=50,
            loss_pct=0.0,
            aqm="pfifo",
            cca="cubic",
            duration_s=DUR,
            trial=TRIAL,
        )
        rec = H.run_direct(cfg)
    except Exception:
        print("FAILED %s" % tag, flush=True)
        traceback.print_exc()
        sys.exit(1)

    # record the conditions beside the run, exactly as the solo cells do
    import glob

    cand = sorted(
        glob.glob(
            os.path.join(
                HERE,
                "results/pramana_runs",
                "*%gmbps*concurrent*t%d_*" % (BW, TRIAL),
            )
        ),
        key=os.path.getmtime,
    )
    if cand:
        with open(os.path.join(cand[-1], "host_conditions.json"), "w") as fh:
            json.dump(
                {
                    "host_load_gate": gate,
                    "host_load_gate_wait_s": round(waited, 1),
                    "load1_at_start": l1,
                    "chrome_procs_at_start": chrome,
                    "gate_rule": {
                        "max_load1": M.GATE_MAX_LOAD1,
                        "hold_s": M.GATE_HOLD_S,
                        "timeout_s": M.GATE_TIMEOUT_S,
                    },
                },
                fh,
                indent=1,
            )
        print("DIR %s" % cand[-1], flush=True)
    if isinstance(rec, dict):
        for app in APPS:
            q = (rec.get("player_qoe") or {}).get(app) or {}
            u = q.get("uniform") or {}
            print(
                "ROW %s %s"
                % (
                    app,
                    json.dumps(
                        {
                            "status": q.get("status"),
                            "res_p": q.get("video_resolution_p"),
                            "startup_delay_ms": u.get("startup_delay_ms"),
                            "rebuffer_events": u.get("rebuffer_events"),
                            "video_mbps": u.get("delivered_video_bitrate_mbps"),
                            "switch_count": u.get("switch_count"),
                            "rendered_fps": u.get("rendered_fps"),
                        }
                    ),
                ),
                flush=True,
            )
    print("CONCURRENT_DONE", flush=True)
