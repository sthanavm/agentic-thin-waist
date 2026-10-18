"""Pin-behaviour test: does Vimeo's `quality` param FORCE a rendition or CAP it?

Run Vimeo alone at a low cap with and without the param. Forcing shows up as a
flat resolution series plus rebuffers (the player cannot adapt down); capping
shows up as a normal adaptive series that simply never exceeds the ceiling.
"""
import os, sys, json
os.environ["PRAMANA_CAPTURE_OVERHEAD_S"] = "45"
os.environ["PRAMANA_PLOT_YMAX"] = "11"
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana"))
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))
import pramana_helpers as H

BASE = "https://player.vimeo.com/video/911411289?autoplay=1&muted=1"
DUR = int(os.environ.get("DUR", "120"))
CASES = json.loads(sys.argv[1])  # [[bw, "suffix", "label"], ...]
for bw, suffix, label in CASES:
    url = BASE + suffix
    print("=== PINTEST %s bw=%dMbps url_suffix=%r ===" % (label, bw, suffix), flush=True)
    cfg = H.ExperimentConfig(
        apps=["vimeo"], bandwidth_mbps=bw, upload_mbps=bw, latency_ms=50,
        loss_pct=0, aqm="pfifo", buffer_packets=1000, cca="cubic",
        duration_s=DUR, trial=1, tag="pintest_%s_%dm" % (label, bw),
        app_urls={"vimeo": url},
    )
    try:
        rec = H.run_direct(cfg)
        q = (rec.get("player_qoe") or {}).get("vimeo") or {}
        print("PINRESULT %s %dM %s" % (label, bw, json.dumps({
            k: q.get(k) for k in ("status", "video_resolution_p", "frame_rate_fps",
                                  "dropped_frame_pct", "rebuffer_events",
                                  "rebuffer_duration_ms", "watched_seconds",
                                  "startup_ms", "total_samples")})), flush=True)
        print("PINDIR %s %s" % (label, rec.get("run_dir")), flush=True)
    except Exception as e:
        print("PINFAILED %s %dM %s: %s" % (label, bw, type(e).__name__, str(e)[:200]), flush=True)
print("PINTEST_DONE", flush=True)
