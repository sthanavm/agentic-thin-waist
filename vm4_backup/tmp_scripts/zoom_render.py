"""Once admitted: where does Zoom put REMOTE video - <video> or <canvas>?"""
import os, sys, time, json
sys.path.insert(0, "/app")
from collect import start_display, build_driver, ensure_window_size
MID="73093848652"; PWD="bkQH53Qa0iQ2gefK20wx6b79Sn9TEX.1"
URL=f"https://us04web.zoom.us/wc/{MID}/join?pwd={PWD}"
start_display("99"); os.environ["DISPLAY"]=":99"

HOOK = r"""
(() => { if(window.__p) return; window.__p=1; window.__ws=[]; window.__wasm=[]; window.__workers=[];
  const NW=window.Worker;
  if (NW) window.Worker=new Proxy(NW,{construct(t,a,nt){
    window.__workers.push(String(a[0]).slice(0,120)); return Reflect.construct(t,a,nt);}});
  const N=window.WebSocket; window.WebSocket=new Proxy(N,{construct(t,a,nt){
    const s=Reflect.construct(t,a,nt); window.__ws.push(String(a[0]).slice(0,130)); return s;}});
  for (const fn of ['instantiate','instantiateStreaming','compile','compileStreaming'])
    if (window.WebAssembly && WebAssembly[fn]) { const o=WebAssembly[fn].bind(WebAssembly);
      WebAssembly[fn]=function(...a){ window.__wasm.push(fn); return o(...a); }; }
  const K='__netgentPeerConnections';
  if(!Array.isArray(window[K])) Object.defineProperty(window,K,{value:[],writable:false,enumerable:false});
  for (const nm of ['RTCPeerConnection','webkitRTCPeerConnection']) {
    const Nat=window[nm]; if(!Nat||Nat.__w) continue;
    const W=new Proxy(Nat,{construct(t,a,nt){const pc=Reflect.construct(t,a,nt); window[K].push(pc); return pc;}});
    Object.defineProperty(W,'__w',{value:true}); window[nm]=W;
  }
})();
"""

SURFACE = r"""
function vis(e){const r=e.getBoundingClientRect();
  return {w:Math.round(r.width),h:Math.round(r.height),top:Math.round(r.top)};}
const vids=[...document.querySelectorAll('video')].map((v,i)=>({
  i, vw:v.videoWidth, vh:v.videoHeight, paused:v.paused, t:+(v.currentTime||0).toFixed(1),
  srcObject: !!v.srcObject, src:(v.currentSrc||'').slice(0,42),
  id:(v.id||'').slice(0,34), cls:(v.className||'').toString().slice(0,42), box:vis(v)}));
const cans=[...document.querySelectorAll('canvas')].map((c,i)=>({
  i, w:c.width, h:c.height, id:(c.id||'').slice(0,34),
  cls:(c.className||'').toString().slice(0,42), box:vis(c)}));
const t=(document.body.innerText||'').replace(/\s+/g,' ');
return {url:location.href.slice(0,90), title:document.title.slice(0,50),
        waiting:/let them know you.?re here|waiting for the host|please wait/i.test(t),
        body:t.slice(0,150), videos:vids, canvases:cans,
        pcs:(window.__netgentPeerConnections||[]).length,
        ws:(window.__ws||[]).length, wasm:(window.__wasm||[]).length,
        workers:(window.__workers||[]).length, worker_urls:(window.__workers||[]).slice(0,6)};
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
  if(l==='join'||l==='join meeting'||l==='join audio by computer'||l==='agree and proceed'){b.click();out.clicked=l;break;}}
return out;
"""

d=build_driver(); d.set_page_load_timeout(60); d.set_script_timeout(30)
try:
    ensure_window_size(d,"zoom")
    d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",{"source":HOOK})
    print(f"=== {URL}", flush=True)
    try: d.get(URL)
    except Exception as e: print(f"  nav warn {type(e).__name__}", flush=True)
    time.sleep(14)
    for i in range(6):
        j=d.execute_script(JOIN,"pramana-probe")
        if j.get("typed") or j.get("clicked"): print(f"  join: {j}", flush=True)
        time.sleep(4)
    print("\n=== watching for remote media (up to 6 min - ADMIT pramana-probe) ===", flush=True)
    admitted_note=False
    for i in range(60):
        time.sleep(6)
        s=d.execute_script(SURFACE)
        if not s["waiting"] and not admitted_note:
            admitted_note=True; print(f"  *** no longer in waiting room at t+{(i+1)*6}s ***", flush=True)
        vs=[f"v{v['i']}:{v['vw']}x{v['vh']}{'(box '+str(v['box']['w'])+'x'+str(v['box']['h'])+')' if v['vw']==0 else ''}" for v in s["videos"]]
        cs=[f"c{c['i']}:{c['w']}x{c['h']}" for c in s["canvases"]]
        print(f"  t+{(i+1)*6:>4}s wait={str(s['waiting'])[:5]:5} PCs={s['pcs']} wasm={s['wasm']} ws={s['ws']} workers={s['workers']} | video={vs} canvas={cs}", flush=True)
    d.save_screenshot("/out/zoom_render.png")
    print("\n=== FINAL SURFACE DETAIL ===", flush=True)
    print(json.dumps(d.execute_script(SURFACE), indent=1)[:2200], flush=True)
finally:
    try: d.quit()
    except Exception: pass
print("ZOOM_RENDER_DONE", flush=True)
