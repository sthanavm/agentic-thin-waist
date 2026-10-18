"""Click 'Join from browser' and follow whatever window/tab it opens."""
import os, sys, time, json
sys.path.insert(0, "/app")
from collect import start_display, build_driver, ensure_window_size, install_webrtc_hook
from selenium.webdriver.common.by import By
URL=os.environ.get("ZOOM_URL",""); NAME=os.environ.get("ZOOM_NAME","pramana-probe")
start_display("99"); os.environ["DISPLAY"]=":99"
HOOK = r"""
(() => { if(window.__probed) return; window.__probed=1; window.__ws=[]; window.__wasm=[];
  const N=window.WebSocket; window.WebSocket=new Proxy(N,{construct(t,a,nt){
    const s=Reflect.construct(t,a,nt); window.__ws.push(String(a[0]).slice(0,120)); return s;}});
  for (const fn of ['instantiate','instantiateStreaming','compile','compileStreaming'])
    if (window.WebAssembly && WebAssembly[fn]) { const o=WebAssembly[fn].bind(WebAssembly);
      WebAssembly[fn]=function(...a){ window.__wasm.push(fn+':'+String(a[0]).slice(0,100)); return o(...a); }; }
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
        body:(document.body.innerText||'').replace(/\s+/g,' ').slice(0,160),
        pcs:(window.__netgentPeerConnections||[]).length,
        ws:(window.__ws||[]).length, ws_urls:(window.__ws||[]).slice(0,8),
        wasm:(window.__wasm||[]).length, wasm_args:(window.__wasm||[]).slice(0,8),
        canvases:document.querySelectorAll('canvas').length,
        video: v?{w:v.videoWidth,h:v.videoHeight,paused:v.paused}:null,
        res: performance.getEntriesByType('resource').map(e=>e.name)
               .filter(u=>/\.wasm|worker|rtc|media|video/i.test(u)).slice(0,12)};
"""
d=build_driver(); d.set_page_load_timeout(60); d.set_script_timeout(30)
try:
    ensure_window_size(d,"zoom")
    d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",{"source":HOOK})  # applies to EVERY new document/tab
    d.get(URL); time.sleep(10)
    before=set(d.window_handles)
    print(f"  handles before click: {len(before)}", flush=True)
    for e in d.find_elements(By.XPATH, "//button[normalize-space()='Join from browser']"):
        if e.is_displayed():
            d.execute_script("arguments[0].scrollIntoView({block:'center'});", e); time.sleep(0.5)
            e.click(); print("  clicked 'Join from browser'", flush=True); break
    time.sleep(8)
    after=set(d.window_handles)
    print(f"  handles after click : {len(after)}   new: {len(after-before)}", flush=True)
    if after-before:
        h=list(after-before)[0]; d.switch_to.window(h)
        print("  switched to the NEW tab", flush=True)
    time.sleep(8)
    print(f"  current: {json.dumps(d.execute_script(STATE))[:420]}", flush=True)
    for i in range(12):
        try:
            r=d.execute_script(r"""
              const name=arguments[0]; const out={typed:false,clicked:null};
              const ins=[...document.querySelectorAll('input')];
              const ni=ins.find(i=>{const l=((i.placeholder||'')+' '+(i.getAttribute('aria-label')||'')+' '+(i.id||'')).toLowerCase();
                 return l.includes('name')&&!l.includes('pass');});
              if(ni&&ni.value!==name){const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
                s.call(ni,name); ni.dispatchEvent(new Event('input',{bubbles:true}));
                ni.dispatchEvent(new Event('change',{bubbles:true})); out.typed=true;}
              for(const b of document.querySelectorAll('button,[role=button]')){
                const l=(b.innerText||b.getAttribute('aria-label')||'').trim().toLowerCase();
                if(l==='join'||l==='join meeting'||l==='agree and proceed'||l.includes('join audio')){b.click();out.clicked=l;break;}}
              return out;""", NAME)
            if r.get("typed") or r.get("clicked"): print(f"  join step: {r}", flush=True)
        except Exception as ex: print(f"  join step err {type(ex).__name__}", flush=True)
        time.sleep(6)
        s=d.execute_script(STATE)
        print(f"  t+{(i+1)*6:>3}s PCs={s['pcs']} WS={s['ws']} wasm={s['wasm']} canvas={s['canvases']} video={s['video']} | {s['title'][:45]}", flush=True)
        if s["pcs"] or s["wasm"] or s["ws"]: break
    print("\n=== FINAL ===", flush=True)
    print(json.dumps(d.execute_script(STATE), indent=1)[:1800], flush=True)
finally:
    try: d.quit()
    except Exception: pass
print("ZOOM_TABS_DONE", flush=True)
