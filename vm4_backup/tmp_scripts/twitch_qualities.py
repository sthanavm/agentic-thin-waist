"""Enumerate the qualities the channel actually offers, via the player instance."""
import os, sys, time, json
sys.path.insert(0, "/app")
from collect import start_display, build_driver, ensure_window_size
URL="https://www.twitch.tv/relaxbeats"
start_display("99"); os.environ["DISPLAY"]=":99"
d=build_driver(); d.set_page_load_timeout(60)
ensure_window_size(d,"twitch")
d.get(URL); time.sleep(40)

JS = r"""
// Walk React internals to find the Twitch player instance, which exposes
// getQualities()/getQuality(). Falls back to null if the shape changed.
function findPlayer(root) {
  const seen = new Set(); const stack = [root]; let steps = 0;
  while (stack.length && steps++ < 200000) {
    const n = stack.pop();
    if (!n || typeof n !== 'object' || seen.has(n)) continue;
    seen.add(n);
    try {
      if (typeof n.getQualities === 'function' && typeof n.getQuality === 'function') return n;
    } catch (e) {}
    for (const k in n) { try { const v = n[k]; if (v && typeof v === 'object') stack.push(v); } catch(e){} }
  }
  return null;
}
const host = document.querySelector('[data-a-target="video-player"]') || document.body;
let fiberKey = Object.keys(host).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
let p = null;
if (fiberKey) p = findPlayer(host[fiberKey]);
const v = document.querySelector('video');
let out = {decoded: v ? (v.videoWidth+'x'+v.videoHeight) : null, found_player: !!p};
if (p) {
  try { out.qualities = p.getQualities().map(q => ({name:q.name, group:q.group, height:q.height, bitrate:q.bitrate})); } catch(e){ out.q_err=String(e); }
  try { const c = p.getQuality(); out.current = c && (c.name || c.group || c); } catch(e){}
}
return out;
"""
try:
    res = d.execute_script(JS)
except Exception as e:
    res = {"error": f"{type(e).__name__}: {e}"}
print(json.dumps(res, indent=2)[:1400], flush=True)
try: d.quit()
except Exception: pass
print("QUALITIES_DONE", flush=True)
