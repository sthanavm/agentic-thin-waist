"""Does relaxbeats offer above 720p, and will it serve it if forced?

force_max_quality in collect.py drives YouTube's setPlaybackQualityRange and is
gated to app=='youtube'. Twitch's equivalent is its own quality preference, held
in localStorage as `video-quality` ('chunked' = source/highest).
"""
import os, sys, time, json
sys.path.insert(0, "/app")
from collect import start_display, build_driver, ensure_window_size

URL = "https://www.twitch.tv/relaxbeats"
start_display("99"); os.environ["DISPLAY"] = ":99"

def probe(driver, label, settle=45):
    time.sleep(settle)
    r = driver.execute_script("""
      const v = document.querySelector('video');
      let ls = null; try { ls = localStorage.getItem('video-quality'); } catch(e){}
      // Twitch exposes the quality menu options in the DOM once opened; the
      // element's own dimensions are the ground truth for what is decoding.
      return {
        vw: v ? v.videoWidth : null, vh: v ? v.videoHeight : null,
        elem_w: v ? Math.round(v.getBoundingClientRect().width) : null,
        elem_h: v ? Math.round(v.getBoundingClientRect().height) : null,
        paused: v ? v.paused : null, ready: v ? v.readyState : null,
        quality_pref: ls,
      };""")
    print(f"  [{label}] decoded={r['vw']}x{r['vh']}  player_elem={r['elem_w']}x{r['elem_h']}  "
          f"readyState={r['ready']} paused={r['paused']}  pref={r['quality_pref']}", flush=True)
    return r

d = build_driver(); d.set_page_load_timeout(60)
ensure_window_size(d, "twitch")

print("=== A. AUTO (what our runs do today) ===", flush=True)
d.get(URL)
a = probe(d, "auto")

print("\n=== B. FORCED to source ('chunked') via Twitch's quality preference ===", flush=True)
d.execute_script("try{localStorage.setItem('video-quality', JSON.stringify({default:'chunked'}));}catch(e){}")
d.get(URL)
b = probe(d, "forced-source")

# sample a few times to catch any climb
print("\n=== C. forced, sampled over 40s ===", flush=True)
seen = set()
for i in range(8):
    time.sleep(5)
    r = d.execute_script("const v=document.querySelector('video');return v?[v.videoWidth,v.videoHeight]:null;")
    if r: seen.add(tuple(r)); print(f"    t+{(i+1)*5:>3}s  {r[0]}x{r[1]}", flush=True)
print(f"\n  distinct decoded resolutions while forced: {sorted(seen)}")
json.dump({"auto": a, "forced": b, "seen": [list(x) for x in seen]}, open("/out/twitch_ceiling.json","w"), default=str)
try: d.quit()
except Exception: pass
print("TWITCH_CEILING_DONE", flush=True)
