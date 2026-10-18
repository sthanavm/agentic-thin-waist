"""With a camera publishing: which surface carries remote camera video?"""
import os, sys, time, json
sys.path.insert(0, "/app")
from collect import start_display, build_driver, ensure_window_size
MID="73093848652"; PWD="bkQH53Qa0iQ2gefK20wx6b79Sn9TEX.1"
URL=f"https://us04web.zoom.us/wc/{MID}/join?pwd={PWD}"
start_display("99"); os.environ["DISPLAY"]=":99"
HOOK = r"""
(() => { if(window.__p) return; window.__p=1;
  const K='__netgentPeerConnections';
  if(!Array.isArray(window[K])) Object.defineProperty(window,K,{value:[],writable:false,enumerable:false});
  for (const nm of ['RTCPeerConnection','webkitRTCPeerConnection']) {
    const Nat=window[nm]; if(!Nat||Nat.__w) continue;
    const W=new Proxy(Nat,{construct(t,a,nt){const pc=Reflect.construct(t,a,nt); window[K].push(pc); return pc;}});
    Object.defineProperty(W,'__w',{value:true}); window[nm]=W;
  }
})();
"""
# Distinguish a LIVE surface from an empty placeholder: a video must have
# non-zero intrinsic size, a canvas must actually be painted (pixels change).
SURFACE = r"""
function box(e){const r=e.getBoundingClientRect();return Math.round(r.width)+'x'+Math.round(r.height);}
const out={videos:[],canvases:[],pcs:(window.__netgentPeerConnections||[]).length};
document.querySelectorAll('video').forEach((v,i)=>out.videos.push({
  i,id:(v.id||'').slice(0,40),cls:(v.className||'').toString().slice(0,40),
  vw:v.videoWidth,vh:v.videoHeight,box:box(v),paused:v.paused,
  t:+(v.currentTime||0).toFixed(2),srcObject:!!v.srcObject,src:(v.currentSrc||'').slice(0,40)}));
document.querySelectorAll('canvas').forEach((c,i)=>{
  let sig=null, painted=null;
  try{ const g=c.getContext('2d');
       if(g && c.width>2 && c.height>2){
         const d=g.getImageData(Math.floor(c.width/2),Math.floor(c.height/2),2,2).data;
         sig=Array.from(d).join(','); painted=Array.from(d).some(x=>x!==0);
       } else if(!g) { sig='webgl-or-none'; }
  }catch(e){ sig='readback-blocked'; }
  out.canvases.push({i,id:(c.id||'').slice(0,40),cls:(c.className||'').toString().slice(0,40),
                     w:c.width,h:c.height,box:box(c),sig,painted});
});
const t=(document.body.innerText||'').replace(/\s+/g,' ');
out.waiting=/let them know you.?re here|waiting for the host/i.test(t);
out.body=t.slice(0,120);
return out;
"""
JOIN = r"""
const name=arguments[0]; const out={typed:false,clicked:null};
const ins=[...document.querySelectorAll('input')];
const ni=ins.find(i=>{const l=((i.placeholder||'')+' '+(i.getAttribute('aria-label')||'')+' '+(i.id||'')).toLowerCase();
  return (l.includes('name')||l.includes('input-for-name'))&&!l.includes('pass');});
if(ni&&ni.value!==name){const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
  s.call(ni,name); ni.dispatchEvent(new Event('input',{bubbles:true}));
  ni.dispatchEvent(new Event('change',{bubbles:true})); out.typed=true;}
for(const b of document.querySelectorAll('button,[role=button]')){
  const l=(b.innerText||b.getAttribute('aria-label')||'').trim().toLowerCase();
  if(l==='join'||l==='join meeting'||l==='join audio by computer'){b.click();out.clicked=l;break;}}
return out;
"""
d=build_driver(); d.set_page_load_timeout(60); d.set_script_timeout(30)
try:
    ensure_window_size(d,"zoom")
    d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",{"source":HOOK})
    d.get(URL); time.sleep(14)
    for i in range(6):
        j=d.execute_script(JOIN,"pramana-probe")
        if j.get("typed") or j.get("clicked"): print(f"  join: {j}", flush=True)
        time.sleep(4)
    print("\n=== watching surfaces ===", flush=True)
    prev={}
    for i in range(30):
        time.sleep(6)
        s=d.execute_script(SURFACE)
        live_v=[v for v in s["videos"] if v["vw"]>0 and v["vh"]>0]
        live_c=[c for c in s["canvases"] if c["w"]>2 and c["h"]>2]
        moving=[]
        for c in live_c:
            k=f"c{c['i']}"
            if prev.get(k) is not None and c["sig"] not in (None,"readback-blocked") and c["sig"]!=prev[k]:
                moving.append(c["i"])
            prev[k]=c["sig"]
        vt=[f"v{v['i']}({v['id'][:22]}):{v['vw']}x{v['vh']} t={v['t']}" for v in live_v]
        ct=[f"c{c['i']}({c['id'][:22] or c['cls'][:22]}):{c['w']}x{c['h']} painted={c['painted']}" for c in live_c]
        print(f"  t+{(i+1)*6:>4}s wait={str(s['waiting'])[:5]:5} PCs={s['pcs']} | LIVE video={vt} | LIVE canvas={ct} changing={moving}", flush=True)
        if live_v or moving: 
            if i>3: break
    d.save_screenshot("/out/zoom_render2.png")
    print("\n=== FULL SURFACE DUMP ===", flush=True)
    print(json.dumps(d.execute_script(SURFACE), indent=1)[:2600], flush=True)
finally:
    try: d.quit()
    except Exception: pass
print("ZOOM_RENDER2_DONE", flush=True)
