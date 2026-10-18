"""Does a viewport cap bound the ladder without disabling ABR?

Run Vimeo alone at 10Mbps - the cap that let it reach the undecodable
1440x1080 rendition - with a reduced viewport and a free `quality` setting.
A working cap shows a ladder that tops out below 1440x1080 and still switches.
"""
import os, sys, json
os.environ["PRAMANA_CAPTURE_OVERHEAD_S"] = "45"
os.environ["PRAMANA_PLOT_YMAX"] = "11"
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana"))
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))
import pramana_helpers as H

DUR = int(os.environ.get("DUR", "120"))
for win in json.loads(sys.argv[1]):
    print("=== CAPTEST window=%s bw=10Mbps ===" % win, flush=True)
    cfg = H.ExperimentConfig(
        apps=["vimeo"], bandwidth_mbps=10, upload_mbps=10, latency_ms=50,
        loss_pct=0, aqm="pfifo", buffer_packets=1000, cca="cubic",
        duration_s=DUR, trial=1, tag="captest_%s" % win,
        app_window={"vimeo": win},
    )
    try:
        rec = H.run_direct(cfg)
        q = (rec.get("player_qoe") or {}).get("vimeo") or {}
        print("CAPRESULT %s %s" % (win, json.dumps({
            k: q.get(k) for k in ("status", "video_resolution_p", "frame_rate_fps",
                                  "dropped_frame_pct", "rebuffer_events",
                                  "watched_seconds", "total_samples")})), flush=True)
        print("CAPMARK %s app_window_caps=%s pinned=%s" % (
            win, rec.get("app_window_caps"), rec.get("app_ladder_pinned")), flush=True)
    except Exception as e:
        print("CAPFAILED %s %s: %s" % (win, type(e).__name__, str(e)[:200]), flush=True)
print("CAPTEST_DONE", flush=True)
