"""What do Meet and Zoom actually present to an unauthenticated browser?"""
import os, sys, time, json
sys.path.insert(0,"/app")
from collect import start_display, build_driver, ensure_window_size, install_webrtc_hook
start_display("99"); os.environ["DISPLAY"]=":99"

TARGETS=[
 ("meet-root",        "https://meet.google.com/"),
 ("meet-fake-room",   "https://meet.google.com/abc-defg-hij"),
 ("zoom-wc-join",     "https://zoom.us/wc/join/1234567890"),
 ("zoom-app-join",    "https://app.zoom.us/wc/1234567890/join"),
]
PROBE = r"""
const out={url:location.href, title:document.title.slice(0,80)};
const txt=(document.body.innerText||'').replace(/\s+/g,' ').slice(0,700);
out.body=txt;
// signals
out.has_name_input = !!Array.from(document.querySelectorAll('input')).find(i=>{
  const l=((i.placeholder||'')+' '+(i.getAttribute('aria-label')||'')).toLowerCase();
  return l.includes('your name')||l==='name'||l.includes('name');});
out.buttons = [...new Set(Array.from(document.querySelectorAll('button,[role=button]'))
   .map(b=>(b.innerText||b.getAttribute('aria-label')||'').trim())
   .filter(t=>t&&t.length<40))].slice(0,12);
out.signin = /sign in|log in|signin|login/i.test(txt);
out.pc_count = (window.__netgentPeerConnections||[]).length;
return out;
"""
for name,url in TARGETS:
    d=build_driver(); d.set_page_load_timeout(60); d.set_script_timeout(30)
    try:
        ensure_window_size(d,name)
        install_webrtc_hook(d)          # capture any RTCPeerConnection construction
        try: d.get(url)
        except Exception as e: print(f"  {name}: nav warn {type(e).__name__}")
        time.sleep(14)
        r=d.execute_script(PROBE)
        print(f"\n=== {name}  ({url}) ===", flush=True)
        print(f"  landed  : {r['url'][:100]}", flush=True)
        print(f"  title   : {r['title']}", flush=True)
        print(f"  sign-in wording present: {r['signin']}   name input: {r['has_name_input']}   RTCPeerConnections: {r['pc_count']}", flush=True)
        print(f"  buttons : {r['buttons']}", flush=True)
        print(f"  body    : {r['body'][:320]}", flush=True)
    except Exception as e:
        print(f"  {name}: FAILED {type(e).__name__}: {e}", flush=True)
    finally:
        try: d.quit()
        except Exception: pass
print("\nCONF_PROBE_DONE", flush=True)
