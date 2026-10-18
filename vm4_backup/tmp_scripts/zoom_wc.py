"""Go straight to the web client, skipping the launcher button entirely."""
import os, sys, time, json
sys.path.insert(0, "/app")
from collect import start_display, build_driver, ensure_window_size
MID="77577309562"; PWD="xUBE4CH1bwnh6zVdNMwpEhBLgVA9dw.1"
CANDIDATES=[f"https://us04web.zoom.us/wc/{MID}/join?pwd={PWD}",
            f"https://us04web.zoom.us/wc/join/{MID}?pwd={PWD}"]
start_display("99"); os.environ["DISPLAY"]=":99"
HOOK = r"""
(() => { if(window.__p) return; window.__p=1; window.__ws=[]; window.__wasm=[];
  const N=window.WebSocket; window.WebSocket=new Proxy(N,{construct(t,a,nt){
    const s=Reflect.construct(t,a,nt); window.__ws.push(String(a[0]).slice(0,130)); return s;}});
  for (const fn of ['instantiate','instantiateStreaming','compile','compileStreaming'])
    if (window.WebAssembly && WebAssembly[fn]) { const o=WebAssembly[fn].bind(WebAssembly);
      WebAssembly[fn]=function(...a){ window.__wasm.push(fn+':'+String(a[0]).slice(0,110)); return o(...a); }; }
  const K='__netgentPeerConnections';
  if(!Array.isArray(window[K])) Object.defineProperty(window,K,{value:[],writable:false,enumerable:false});
  for (const nm of ['RTCPeerConnection','webkitRTCPeerConnection']) {
    const Nat=window[nm]; if(!Nat||Nat.__w) continue;
    const W=new Proxy(Nat,{construct(t,a,nt){const pc=Reflect.construct(t,a,nt); window[K].push(pc); return pc;}});
    Object.defineProperty(W,'__w',{value:true}); window[nm]=W;
  }
})();
"""
STATE = r"""
const v=document.querySelector('video');
return {url:location.href.slice(0,130), title:document.title.slice(0,70),
        body:(document.body.innerText||'').replace(/\s+/g,' ').slice(0,200),
        pcs:(window.__netgentPeerConnections||[]).length,
        ws:(window.__ws||[]).length, ws_urls:(window.__ws||[]).slice(0,8),
        wasm:(window.__wasm||[]).length, wasm_args:(window.__wasm||[]).slice(0,8),
        canvases:document.querySelectorAll('canvas').length,
        video:v?{w:v.videoWidth,h:v.videoHeight,paused:v.paused}:null,
        wasm_res: performance.getEntriesByType('resource').map(e=>e.name)
                    .filter(u=>/\.wasm|worker/i.test(u)).slice(0,10)};
"""
JOIN = r"""
const name=arguments[0]; const out={typed:false,clicked:null,inputs:[],buttons:[]};
const ins=[...document.querySelectorAll('input')];
out.inputs=ins.map(i=>((i.placeholder||'')+'|'+(i.getAttribute('aria-label')||'')+'|'+(i.id||'')).slice(0,40)).slice(0,6);
const ni=ins.find(i=>{const l=((i.placeholder||'')+' '+(i.getAttribute('aria-label')||'')+' '+(i.id||'')).toLowerCase();
  return l.includes('name')&&!l.includes('pass');});
if(ni&&ni.value!==name){const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
  s.call(ni,name); ni.dispatchEvent(new Event('input',{bubbles:true}));
  ni.dispatchEvent(new Event('change',{bubbles:true})); out.typed=true;}
const bs=[...document.querySelectorAll('button,[role=button]')];
out.buttons=[...new Set(bs.map(b=>(b.innerText||b.getAttribute('aria-label')||'').trim()).filter(t=>t&&t.length<30))].slice(0,10);
for(const b of bs){const l=(b.innerText||b.getAttribute('aria-label')||'').trim().toLowerCase();
  if(l==='join'||l==='join meeting'||l==='agree and proceed'||l.includes('join audio')){b.click();out.clicked=l;break;}}
return out;
"""
for url in CANDIDATES:
    print(f"\n########## {url}", flush=True)
    d=build_driver(); d.set_page_load_timeout(60); d.set_script_timeout(30)
    try:
        ensure_window_size(d,"zoom")
        d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",{"source":HOOK})
        try: d.get(url)
        except Exception as e: print(f"  nav warn {type(e).__name__}", flush=True)
        time.sleep(14)
        s=d.execute_script(STATE)
        print(f"  landed: {s['url'][:110]}", flush=True)
        print(f"  title : {s['title']}", flush=True)
        print(f"  body  : {s['body'][:170]}", flush=True)
        if "invalid" in s["body"].lower() or "Error" in s["title"]:
            print("  -> rejected, trying next form", flush=True); continue
        for i in range(10):
            try:
                j=d.execute_script(JOIN,"pramana-probe")
                if i==0 or j.get("clicked") or j.get("typed"):
                    print(f"  join: {json.dumps(j)[:250]}", flush=True)
            except Exception as ex: print(f"  join err {type(ex).__name__}", flush=True)
            time.sleep(6)
            s=d.execute_script(STATE)
            print(f"  t+{(i+1)*6:>3}s PCs={s['pcs']} WS={s['ws']} wasm={s['wasm']} canvas={s['canvases']} video={s['video']}", flush=True)
            if s["pcs"] or s["wasm"] or s["ws"]: break
        d.save_screenshot("/out/zoom_wc.png")
        print(f"\n  FINAL: {json.dumps(d.execute_script(STATE), indent=1)[:1400]}", flush=True)
        if s["pcs"] or s["wasm"] or s["ws"]: break
    finally:
        try: d.quit()
        except Exception: pass
print("ZOOM_WC_DONE", flush=True)
