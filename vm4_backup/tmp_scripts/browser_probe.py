import json, sys, time
sys.path.insert(0, "/app")
from collect import STATS_JS, MEDIA_HOOK_JS, build_driver, start_display, ensure_window_size

URL = sys.argv[1]
start_display("99")
import os; os.environ["DISPLAY"] = ":99"
d = build_driver()
try:
    try:
        d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": MEDIA_HOOK_JS})
    except Exception as e:
        print("  codec hook unavailable:", type(e).__name__)
    ensure_window_size(d, "webrtc-peer")
    d.get(URL)
    samples = []
    for i in range(14):
        time.sleep(1.0)
        try:
            s = d.execute_script(STATS_JS)
        except Exception as e:
            print("  stats error:", type(e).__name__, e); break
        if isinstance(s, dict):
            samples.append(s)
    ok = [s for s in samples if (s.get("video") or {}).get("width")]
    print("  samples: %d, with live video: %d" % (len(samples), len(ok)))
    if ok:
        first, last = ok[0], ok[-1]
        print("  video    :", json.dumps(last.get("video")))
        print("  playback :", json.dumps(last.get("playback")))
        print("  frames   :", json.dumps(last.get("frames")))
        tf0 = (first.get("frames") or {}).get("total") or 0
        tf1 = (last.get("frames") or {}).get("total") or 0
        print("  frames advanced: %s -> %s  (%s)" % (tf0, tf1, "LIVE" if tf1 > tf0 else "STATIC"))
        print("  flat keys kept :", sum(1 for k in ("resolution","current_time_secs",
              "total_video_frames","dropped_video_frames","video_height","paused")
              if k in last), "/6")
finally:
    try: d.quit()
    except Exception: pass
