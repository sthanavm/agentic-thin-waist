"""Does hiding AV1 keep Vimeo's ladder free AND decodable at 10Mbps?"""
import os, sys, json
os.environ["PRAMANA_CAPTURE_OVERHEAD_S"] = "45"
os.environ["PRAMANA_PLOT_YMAX"] = "11"
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana"))
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))
import pramana_helpers as H

DUR = int(os.environ.get("DUR", "120"))
print("=== NOAV1TEST bw=10Mbps block=av01 ===", flush=True)
cfg = H.ExperimentConfig(
    apps=["vimeo"], bandwidth_mbps=10, upload_mbps=10, latency_ms=50,
    loss_pct=0, aqm="pfifo", buffer_packets=1000, cca="cubic",
    duration_s=DUR, trial=1, tag="noav1",
    app_block_codecs={"vimeo": ["av01"]},
)
try:
    rec = H.run_direct(cfg)
    q = (rec.get("player_qoe") or {}).get("vimeo") or {}
    print("NOAV1RESULT %s" % json.dumps({
        k: q.get(k) for k in ("status", "video_resolution_p", "frame_rate_fps",
                              "dropped_frame_pct", "rebuffer_events",
                              "watched_seconds", "total_samples")}), flush=True)
    print("NOAV1MARK blocked=%s" % rec.get("app_blocked_codecs"), flush=True)
except Exception as e:
    print("NOAV1FAILED %s: %s" % (type(e).__name__, str(e)[:250]), flush=True)
print("NOAV1_DONE", flush=True)
