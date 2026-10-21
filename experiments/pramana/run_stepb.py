#!/usr/bin/env python3
"""Step B, first pass: the latency sweep, solo runs only.

Plan and bands were pre-registered in validation/THRESHOLDS.md and committed
before this script was ever run. Nothing here chooses a band.

Solo only, on purpose. The concurrent cell is not measurable on this host --
a single competing CPU-bound process stalls YouTube's page for 5-30 s -- so a
concurrent latency number collected here would not mean what it looks like.

One cell at a time, behind the host-load gate and a free-space guard, because
a run started on a nearly full disk does not fail honestly: Chrome never
launches and the run completes with an empty qoe/ and null metrics.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, HOME)
import run_matrix as M  # noqa: E402
import pramana_helpers as H  # noqa: E402

os.environ.setdefault(
    "PRAMANA_COLLECTOR_SRC",
    os.path.join(HOME, "services/orchestration/scripts/selenium_video_qoe/collect.py"),
)
os.environ.setdefault("PRAMANA_DEFER_ANALYSIS", "1")

BW = float(os.environ.get("BW", "6"))
DUR = int(os.environ.get("DUR", "180"))
AQM = "pfifo"  # unchanged from Step A; the AQM sweep is a later pass
BASELINE_LAT_MS = 50  # stated in the pre-registration
MIN_FREE_GB = float(os.environ.get("PRAMANA_MIN_FREE_GB", "2"))
# L1: the substrate must actually apply the latency we asked for.
L1_TOLERANCE = 0.10


def free_gb(path="/"):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / 1e9


def main():
    app = sys.argv[1]
    lat = float(sys.argv[2])
    trial = int(os.environ.get("TRIAL", "1"))

    gb = free_gb()
    if gb < MIN_FREE_GB:
        print(
            "ABORT: %.2f GB free, need %.1f. Nothing run." % (gb, MIN_FREE_GB),
            flush=True,
        )
        raise SystemExit(3)
    print("disk check: %.2f GB free" % gb, flush=True)

    tag = "stepb_%s_%gms_t%d" % (app, lat, trial)
    gate, waited, l1, chrome = M.wait_for_quiet_host(tag)
    print(
        "=== %s === gate=%s load1=%s waited=%.0fs" % (tag, gate, l1, waited), flush=True
    )

    rec = None
    try:
        rec = H.run_direct(
            H.ExperimentConfig(
                apps=[app],
                bandwidth_mbps=BW,
                latency_ms=lat,
                loss_pct=0.0,
                aqm=AQM,
                cca="cubic",
                duration_s=DUR,
                trial=trial,
            )
        )
    except Exception:
        import traceback

        traceback.print_exc()
        print("STEPB_FAILED %s %g t%d" % (app, lat, trial), flush=True)
        raise SystemExit(1)

    # L1, checked here as well as in the release build, so a mis-shaped cell is
    # visible in the run log and not only after the fact.
    applied = ((rec or {}).get("shaping_applied") or {}).get("latency_ms")
    if applied is None:
        print(
            "L1 UNKNOWN %s %gms: substrate reported no applied latency" % (app, lat),
            flush=True,
        )
    else:
        off = abs(applied - lat) / max(lat, 1e-9)
        print(
            "L1 %s %s %gms requested, %gms applied (%.1f%% off, bar %.0f%%)"
            % (
                "PASS" if off <= L1_TOLERANCE else "FAIL",
                app,
                lat,
                applied,
                off * 100,
                L1_TOLERANCE * 100,
            ),
            flush=True,
        )

    q = (rec.get("player_qoe") or {}).get(app) or {}
    u = q.get("uniform") or {}
    print(
        "ROW %s %s"
        % (
            app,
            json.dumps(
                {
                    "latency_ms": lat,
                    "trial": trial,
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
    # The substrate leaves a staging copy of the capture in /tmp per run; the
    # run dir keeps its own, and the staging dir is root-owned and otherwise
    # unreclaimable, so drop ours now rather than let 12 runs fill the disk.
    slug = "%s_%gmbps_%gms_0pct_%s_cubic_solo_t%d" % (app, BW, lat, AQM, trial)
    f = "/tmp/substrate-capture/%s.pcap" % slug
    if os.path.exists(f):
        subprocess.run(
            ["sudo", "rm", "-f", f],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("cleaned staging capture %s.pcap" % slug, flush=True)
    print("disk after: %.2f GB free" % free_gb(), flush=True)
    print("STEPB_DONE %s %g t%d" % (app, lat, trial), flush=True)


if __name__ == "__main__":
    main()
