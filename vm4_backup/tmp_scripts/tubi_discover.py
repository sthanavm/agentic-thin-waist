"""What player does Tubi use, and how are its renditions exposed?"""
import os, sys, time, json
sys.path.insert(0, "/app")
from collect import start_display, build_driver, ensure_window_size
URL="https://tubitv.com/movies/593796/another-cinderella-story"
start_display("99"); os.environ["DISPLAY"]=":99"
d=build_driver(); d.set_page_load_timeout(60); d.set_script_timeout(30)
ensure_window_size(d,"tubi")
d.get(URL)
print("  waiting for playback to establish ...", flush=True)
for _ in range(40):
    time.sleep(2)
    r=d.execute_script("const v=document.querySelector('video');return v?[v.videoWidth,v.videoHeight,v.readyState,v.paused]:null;")
    if r and r[0]: print(f"  playing: {r[0]}x{r[1]} readyState={r[2]} paused={r[3]}", flush=True); break

JS = r"""
const v = document.querySelector('video');
const out = {
  decoded: v ? v.videoWidth+'x'+v.videoHeight : null,
  src: v ? (v.currentSrc||'').slice(0,60) : null,
  elem: v ? Math.round(v.getBoundingClientRect().width)+'x'+Math.round(v.getBoundingClientRect().height) : null,
};
// which streaming library is present?
out.has_hlsjs   = typeof window.Hls !== 'undefined';
out.has_shaka   = typeof window.shaka !== 'undefined';
out.has_dashjs  = typeof window.dashjs !== 'undefined';
out.mse         = 'MediaSource' in window;
// localStorage keys that look quality-related
try {
  out.ls_keys = Object.keys(localStorage).filter(k => /qual|video|resol|bitrate|hd/i.test(k)).slice(0,15);
} catch(e) { out.ls_err = String(e); }
// any object on window exposing levels/tracks
const cands = [];
for (const k of Object.keys(window).slice(0, 4000)) {
  try {
    const o = window[k];
    if (o && typeof o === 'object') {
      if (Array.isArray(o.levels) || typeof o.getVariantTracks === 'function' || typeof o.getBitrateInfoListFor === 'function')
        cands.push(k);
    }
  } catch(e) {}
}
out.window_candidates = cands.slice(0,10);
// video track list (native)
try { out.videoTracks = v && v.videoTracks ? v.videoTracks.length : null; } catch(e){}
return out;
"""
try: res=d.execute_script(JS)
except Exception as e: res={"error":f"{type(e).__name__}: {e}"}
print(json.dumps(res, indent=1)[:1600], flush=True)
try: d.quit()
except Exception: pass
print("TUBI_DISCOVER_DONE", flush=True)
