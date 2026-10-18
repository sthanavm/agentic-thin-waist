"""Find the real 'Join from browser' element and follow it into the web client."""
import os, sys, time, json
sys.path.insert(0, "/app")
from collect import start_display, build_driver, ensure_window_size, install_webrtc_hook
URL=os.environ.get("ZOOM_URL",""); NAME=os.environ.get("ZOOM_NAME","pramana-probe")
start_display("99"); os.environ["DISPLAY"]=":99"

HOOK = r"""
(() => { window.__ws=[]; window.__wasm=[];
  const N=window.WebSocket; window.WebSocket=new Proxy(N,{construct(t,a,nt){
    const s=Reflect.construct(t,a,nt); window.__ws.push(String(a[0]).slice(0,120)); return s;}});
  for (const fn of ['instantiate','instantiateStreaming','compile','compileStreaming'])
    if (window.WebAssembly && WebAssembly[fn]) { const o=WebAssembly[fn].bind(WebAssembly);
      WebAssembly[fn]=function(...a){ window.__wasm.push(fn+':'+String(a[0]).slice(0,100)); return o(...a); }; }
})();
"""
INSPECT = r"""
const hits=[];
document.querySelectorAll('*').forEach(el=>{
  const t=(el.innerText||el.textContent||'').trim();
  if (t.toLowerCase()==='join from browser' && t.length<40)
    hits.push({tag:el.tagName, id:el.id, cls:(el.className||'').toString().slice(0,60),
               href:el.getAttribute('href'), role:el.getAttribute('role'),
               onclick: !!el.onclick, rect:(()=>{const r=el.getBoundingClientRect();
                 return Math.round(r.width)+'x'+Math.round(r.height)+'@'+Math.round(r.top);})()});
});
return hits.slice(0,6);
"""
STATE = r"""
const v=document.querySelector('video');
return {url:location.href.slice(0,120), title:document.title.slice(0,60),
        body:(document.body.innerText||'').replace(/\s+/g,' ').slice(0,180),
        pcs:(window.__netgentPeerConnections||[]).length,
        ws:(window.__ws||[]).length, ws_urls:(window.__ws||[]).slice(0,6),
        wasm:(window.__wasm||[]).length, wasm_args:(window.__wasm||[]).slice(0,6),
        canvases:document.querySelectorAll('canvas').length,
        video: v?{w:v.videoWidth,h:v.videoHeight,paused:v.paused}:null,
        res: performance.getEntriesByType('resource').map(e=>e.name)
               .filter(u=>/\.wasm|worker|webrtc|media/i.test(u)).slice(0,10)};
"""
d=build_driver(); d.set_page_load_timeout(60); d.set_script_timeout(30)
try:
    ensure_window_size(d,"zoom"); install_webrtc_hook(d)
    d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",{"source":HOOK})
    d.get(URL); time.sleep(10)
    print("=== the 'Join from browser' element(s) ===", flush=True)
    hits=d.execute_script(INSPECT)
    print(json.dumps(hits,indent=1)[:700], flush=True)

    print("\n=== clicking it via Selenium (real input, not JS) ===", flush=True)
    clicked=False
    from selenium.webdriver.common.by import By
    for xp in ["//a[normalize-space()='Join from browser']",
               "//button[normalize-space()='Join from browser']",
               "//*[normalize-space()='Join from browser']"]:
        try:
            els=d.find_elements(By.XPATH, xp)
            for e in els:
                if e.is_displayed():
                    d.execute_script("arguments[0].scrollIntoView({block:'center'});", e)
                    time.sleep(0.5); e.click(); clicked=True
                    print(f"  clicked via {xp}", flush=True); break
        except Exception as ex:
            print(f"  {xp} -> {type(ex).__name__}: {str(ex)[:70]}", flush=True)
        if clicked: break
    if not clicked:
        print("  Selenium click failed; trying JS click", flush=True)
        d.execute_script("""
          const els=[...document.querySelectorAll('*')].filter(e=>
            (e.innerText||'').trim().toLowerCase()==='join from browser');
          if (els.length) els[els.length-1].click();""")
    time.sleep(12)
    print(f"  after click: {json.dumps(d.execute_script(STATE))[:400]}", flush=True)

    # a name field usually appears in the web client
    for i in range(10):
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
                if(l==='join'||l==='join meeting'||l.includes('join audio')){b.click();out.clicked=l;break;}}
              return out;""", NAME)
            if r.get("typed") or r.get("clicked"): print(f"  join step: {r}", flush=True)
        except Exception: pass
        time.sleep(6)
        s=d.execute_script(STATE)
        print(f"  t+{(i+1)*6:>3}s PCs={s['pcs']} WS={s['ws']} wasm={s['wasm']} canvas={s['canvases']} video={s['video']} | {s['title'][:40]}", flush=True)
        if s["pcs"] or s["wasm"] or s["ws"]:
            break
    print("\n=== FINAL ===", flush=True)
    print(json.dumps(d.execute_script(STATE), indent=1)[:1600], flush=True)
finally:
    try: d.quit()
    except Exception: pass
print("ZOOM_CLICK_DONE", flush=True)
