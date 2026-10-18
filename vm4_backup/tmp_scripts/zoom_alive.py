import os, sys, time, json
sys.path.insert(0,"/app")
from collect import start_display, build_driver, ensure_window_size, ZOOM_JOIN_JS
URL="https://us04web.zoom.us/wc/73093848652/join?pwd=bkQH53Qa0iQ2gefK20wx6b79Sn9TEX.1"
start_display("98"); os.environ["DISPLAY"]=":98"
d=build_driver(); d.set_page_load_timeout(60); d.set_script_timeout(30)
try:
    ensure_window_size(d,"alive"); d.get(URL); time.sleep(12)
    for i in range(5):
        try: d.execute_script(ZOOM_JOIN_JS,"alive-check")
        except Exception: pass
        time.sleep(4)
    r=d.execute_script("""
      const t=(document.body.innerText||"").replace(/\\s+/g," ");
      const live=[...document.querySelectorAll("video")].filter(v=>v.videoWidth>0);
      return {body:t.slice(0,220), live:live.length,
              size: live.length? live[0].videoWidth+"x"+live[0].videoHeight : null,
              toolbar: !!document.querySelector("[aria-label*=Leave i],[class*=footer-button]")};""")
    print(json.dumps(r, indent=1)[:700], flush=True)
finally:
    try: d.quit()
    except Exception: pass
print("ALIVE_DONE", flush=True)
