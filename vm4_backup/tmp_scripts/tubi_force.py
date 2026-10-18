"""Find Tubi's player instance and force its highest rendition."""
import os, sys, time, json
sys.path.insert(0, "/app")
from collect import start_display, build_driver, ensure_window_size
URL="https://tubitv.com/movies/593796/another-cinderella-story"
start_display("99"); os.environ["DISPLAY"]=":99"
d=build_driver(); d.set_page_load_timeout(60); d.set_script_timeout(40)
ensure_window_size(d,"tubi")
d.get(URL)

def res(tag):
    r=d.execute_script("const v=document.querySelector('video');return v?[v.videoWidth,v.videoHeight]:null;")
    print(f"  [{tag}] decoded={r[0]}x{r[1]}" if r and r[0] else f"  [{tag}] no video yet", flush=True)
    return tuple(r) if r else None

print("=== A. AUTO: let ABR settle for 75s ===", flush=True)
for _ in range(38):
    time.sleep(2)
    r=d.execute_script("const v=document.querySelector('video');return v?v.videoWidth:0;")
    if r: break
seen=set()
for i in range(15):
    time.sleep(5)
    r=res(f"auto t+{(i+1)*5}s")
    if r and r[0]: seen.add(r)
print(f"  AUTO distinct resolutions: {sorted(seen)}", flush=True)

print("\n=== B. locate the player instance ===", flush=True)
FIND = r"""
function scan(root, depth){
  const seen=new WeakSet(); const st=[[root,0]]; let n=0; const hits=[];
  while(st.length && n++<300000){
    const [x,dep]=st.pop();
    if(!x||typeof x!=='object'||dep>depth) continue;
    try{ if(seen.has(x)) continue; seen.add(x);}catch(e){continue;}
    try{
      if(Array.isArray(x.levels) && x.levels.length && ('currentLevel' in x))
        hits.push({kind:'hlsjs', levels:x.levels.map(l=>({h:l.height,w:l.width,b:l.bitrate}))});
      else if(typeof x.getVariantTracks==='function')
        hits.push({kind:'shaka'});
      else if(typeof x.getBitrateInfoListFor==='function')
        hits.push({kind:'dashjs'});
    }catch(e){}
    if(hits.length) return hits[0];
    for(const k in x){ try{const v=x[k]; if(v&&typeof v==='object') st.push([v,dep+1]);}catch(e){} }
  }
  return null;
}
const v=document.querySelector('video');
const out={};
// 1) properties hung directly on the video element
try{ out.video_props = Object.keys(v).filter(k=>!k.startsWith('__react')).slice(0,20); }catch(e){}
// 2) react fiber under the player container
const host=document.querySelector('[data-testid*="player"],[class*="player"],video') || document.body;
const fk=Object.keys(host).find(k=>k.startsWith('__reactFiber$')||k.startsWith('__reactInternalInstance$'));
out.has_fiber=!!fk;
let hit=null;
if(v) hit = scan(v, 4);
if(!hit && fk) hit = scan(host[fk], 12);
if(!hit) hit = scan(window, 4);
out.found=hit;
return out;
"""
try: found=d.execute_script(FIND)
except Exception as e: found={"error":f"{type(e).__name__}: {e}"}
print(json.dumps(found, indent=1)[:1200], flush=True)

print("\n=== C. UI quality control present? ===", flush=True)
UI = r"""
const out={buttons:[]};
document.querySelectorAll('button,[role="button"],[aria-label]').forEach(b=>{
  const t=(b.getAttribute('aria-label')||b.textContent||'').trim();
  if(/qual|resol|hd|1080|720|auto|setting|gear/i.test(t) && t.length<40) out.buttons.push(t);
});
out.buttons=[...new Set(out.buttons)].slice(0,15);
return out;
"""
try: ui=d.execute_script(UI)
except Exception as e: ui={"error":str(e)}
print(json.dumps(ui, indent=1)[:800], flush=True)
try: d.quit()
except Exception: pass
print("TUBI_FORCE_DONE", flush=True)
