"""Does the remote <video> advance frames, and can our existing HTML5-style
measurement read it? currentTime is unreliable on a MediaStream-backed element,
so use getVideoPlaybackQuality() - the same API collect.py already uses."""
import os, sys, time, json
sys.path.insert(0, "/app")
from collect import start_display, build_driver, ensure_window_size
MID="73093848652"; PWD="bkQH53Qa0iQ2gefK20wx6b79Sn9TEX.1"
URL=f"https://us04web.zoom.us/wc/{MID}/join?pwd={PWD}"
start_display("99"); os.environ["DISPLAY"]=":99"
JOIN = r"""
const name=arguments[0]; const out={};
const ins=[...document.querySelectorAll('input')];
const ni=ins.find(i=>((i.getAttribute('aria-label')||'')+(i.id||'')+(i.placeholder||'')).toLowerCase().includes('name'));
if(ni&&ni.value!==name){const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
  s.call(ni,name); ni.dispatchEvent(new Event('input',{bubbles:true})); out.typed=true;}
for(const b of document.querySelectorAll('button,[role=button]')){
  const l=(b.innerText||'').trim().toLowerCase();
  if(l==='join'||l==='join audio by computer'){b.click();out.clicked=l;break;}}
return out;
"""
# Exactly the fields collect.py's STATS_JS reads for html5 apps.
FRAMES = r"""
const vids=[...document.querySelectorAll('video')].filter(v=>v.videoWidth>0);
if(!vids.length) return {live:0};
const v=vids[0];
let q={};
if (typeof v.getVideoPlaybackQuality==='function'){
  const p=v.getVideoPlaybackQuality();
  q={total:p.totalVideoFrames, dropped:p.droppedVideoFrames};
}
let tracks=null;
try{ if(v.srcObject&&v.srcObject.getVideoTracks){
       const t=v.srcObject.getVideoTracks()[0];
       if(t){const s=t.getSettings(); tracks={label:(t.label||'').slice(0,40),
             state:t.readyState, w:s.width,h:s.height,fps:s.frameRate};}
}}catch(e){}
return {live:vids.length, vw:v.videoWidth, vh:v.videoHeight, paused:v.paused,
        currentTime:+(v.currentTime||0).toFixed(2), srcObject:!!v.srcObject,
        quality:q, track:tracks};
"""
d=build_driver(); d.set_page_load_timeout(60); d.set_script_timeout(30)
try:
    ensure_window_size(d,"zoom")
    d.get(URL); time.sleep(14)
    for i in range(6):
        d.execute_script(JOIN,"pramana-probe"); time.sleep(4)
    print("=== waiting for live remote video ===", flush=True)
    for i in range(30):
        time.sleep(5)
        r=d.execute_script(FRAMES)
        if r.get("live"): print(f"  live video found at t+{(i+1)*5}s", flush=True); break
    print("\n=== frame progression on the remote <video> ===", flush=True)
    print(f"  {'t':>6}{'res':>11}{'totalFrames':>13}{'dropped':>9}{'currentTime':>13}", flush=True)
    first=last=None
    for i in range(12):
        r=d.execute_script(FRAMES)
        if not r.get("live"): print("  (video disappeared)", flush=True); break
        q=r.get("quality") or {}
        if first is None: first=(q.get("total"),time.time())
        last=(q.get("total"),time.time())
        print(f"  {(i+1)*5:>5}s{str(r['vw'])+'x'+str(r['vh']):>11}{str(q.get('total')):>13}{str(q.get('dropped')):>9}{r['currentTime']:>13}", flush=True)
        time.sleep(5)
    print(f"\n  track detail: {json.dumps((d.execute_script(FRAMES) or {}).get('track'))}", flush=True)
    if first and last and first[0] is not None and last[0] is not None:
        dt=last[1]-first[1]; df=last[0]-first[0]
        print(f"  frames advanced {first[0]} -> {last[0]}  (+{df} in {dt:.0f}s = {df/max(dt,1):.1f} fps)", flush=True)
finally:
    try: d.quit()
    except Exception: pass
print("ZOOM_FRAMES_DONE", flush=True)
