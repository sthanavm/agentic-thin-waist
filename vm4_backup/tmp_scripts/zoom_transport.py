"""Determine what transport Zoom's web client actually uses. Evidence only.

Installs the same RTCPeerConnection proxy the Meet path uses, BEFORE navigation,
then joins as a guest and reports what it finds. If no peer connection is ever
constructed it enumerates the alternatives (WebSocket, WASM, workers) rather
than assuming one.
"""
import os, sys, time, json
sys.path.insert(0, "/app")
from collect import (start_display, build_driver, ensure_window_size,
                     install_webrtc_hook, get_google_meet_stats)

URL  = os.environ.get("ZOOM_URL", "")
NAME = os.environ.get("ZOOM_NAME", "pramana-probe")
if not URL:
    print("ZOOM_URL not set"); sys.exit(2)

# Count WebSocket + WASM the same way we count peer connections: by proxying
# the constructor before any page script runs.
PROBE_HOOK = r"""
(() => {
  window.__ws = []; window.__wasm = [];
  const NativeWS = window.WebSocket;
  window.WebSocket = new Proxy(NativeWS, {
    construct(t, a, nt) { const s = Reflect.construct(t, a, nt);
      window.__ws.push({url: String(a[0]).slice(0,120), t: Date.now()}); return s; }
  });
  for (const fn of ['instantiate','instantiateStreaming','compile','compileStreaming']) {
    if (window.WebAssembly && WebAssembly[fn]) {
      const orig = WebAssembly[fn].bind(WebAssembly);
      WebAssembly[fn] = function(...a) {
        window.__wasm.push({fn, arg: String(a[0]).slice(0,120), t: Date.now()});
        return orig(...a);
      };
    }
  }
})();
"""

JOIN_JS = r"""
const name = arguments[0];
const out = {typed:false, clicked:null, buttons:[]};
const inputs = Array.from(document.querySelectorAll('input'));
const nameIn = inputs.find(i => {
  const l = ((i.placeholder||'')+' '+(i.getAttribute('aria-label')||'')+' '+(i.id||'')).toLowerCase();
  return l.includes('name') && !l.includes('pass');
});
if (nameIn && nameIn.value !== name) {
  const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
  set.call(nameIn, name);
  nameIn.dispatchEvent(new Event('input',{bubbles:true}));
  nameIn.dispatchEvent(new Event('change',{bubbles:true}));
  out.typed = true;
}
const btns = Array.from(document.querySelectorAll('button,[role="button"],a.btn'));
out.buttons = [...new Set(btns.map(b=>(b.innerText||b.getAttribute('aria-label')||'').trim())
                .filter(t=>t&&t.length<32))].slice(0,12);
for (const b of btns) {
  const l = (b.innerText||b.getAttribute('aria-label')||'').trim().toLowerCase();
  if (l==='join' || l.startsWith('join ') || l.includes('join meeting') || l.includes('join audio')) {
    b.click(); out.clicked = l; break;
  }
}
return out;
"""

REPORT_JS = r"""
const v = document.querySelector('video');
return {
  url: location.href.slice(0,110),
  title: document.title.slice(0,60),
  body: (document.body.innerText||'').replace(/\s+/g,' ').slice(0,200),
  peer_connections: (window.__netgentPeerConnections||[]).length,
  websockets: (window.__ws||[]).length,
  ws_urls: (window.__ws||[]).map(x=>x.url).slice(0,8),
  wasm_calls: (window.__wasm||[]).length,
  wasm_args: (window.__wasm||[]).map(x=>x.fn+':'+x.arg).slice(0,8),
  workers_hint: performance.getEntriesByType('resource')
      .map(e=>e.name).filter(u=>/worker|\.wasm/i.test(u)).slice(0,10),
  video_el: v ? {w:v.videoWidth, h:v.videoHeight, paused:v.paused, src:(v.currentSrc||'').slice(0,50)} : null,
  canvases: document.querySelectorAll('canvas').length,
};
"""

start_display("99"); os.environ["DISPLAY"] = ":99"
d = build_driver(); d.set_page_load_timeout(60); d.set_script_timeout(30)
try:
    ensure_window_size(d, "zoom")
    install_webrtc_hook(d)                       # same hook as Meet
    d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": PROBE_HOOK})
    print(f"=== navigating: {URL}", flush=True)
    try: d.get(URL)
    except Exception as e: print(f"  nav warn {type(e).__name__}", flush=True)
    time.sleep(12)
    print(f"  initial: {json.dumps(d.execute_script(REPORT_JS))[:400]}", flush=True)

    for attempt in range(1, 13):
        try: j = d.execute_script(JOIN_JS, NAME)
        except Exception as e: j = {"err": str(e)[:60]}
        if attempt == 1 or j.get("clicked"):
            print(f"  join attempt {attempt}: {json.dumps(j)[:220]}", flush=True)
        time.sleep(5)
        r = d.execute_script(REPORT_JS)
        if r["peer_connections"] or r["wasm_calls"] or r["websockets"] > 2:
            break

    print("\n=== TRANSPORT EVIDENCE (after join attempts) ===", flush=True)
    for i in range(8):
        time.sleep(6)
        r = d.execute_script(REPORT_JS)
        print(f"  t+{(i+1)*6:>3}s  PCs={r['peer_connections']} WS={r['websockets']} "
              f"wasm={r['wasm_calls']} canvases={r['canvases']} video={r['video_el']}", flush=True)
    r = d.execute_script(REPORT_JS)
    print("\n=== FINAL ===", flush=True)
    print(json.dumps(r, indent=1)[:1800], flush=True)
    if r["peer_connections"]:
        print("\n  RTCPeerConnection IS used -> trying the existing Meet stats extraction", flush=True)
        try: print("  getStats ->", json.dumps(get_google_meet_stats(d))[:600], flush=True)
        except Exception as e: print(f"  getStats failed: {type(e).__name__}: {e}", flush=True)
    else:
        print("\n  NO RTCPeerConnection was ever constructed.", flush=True)
        print(f"  websocket urls: {r['ws_urls']}", flush=True)
        print(f"  wasm calls    : {r['wasm_args']}", flush=True)
        print(f"  worker/wasm resources: {r['workers_hint']}", flush=True)
finally:
    try: d.quit()
    except Exception: pass
print("ZOOM_TRANSPORT_DONE", flush=True)
