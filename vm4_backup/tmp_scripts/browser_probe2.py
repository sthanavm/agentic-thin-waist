import json, sys, time, os
sys.path.insert(0, "/app")
from collect import build_driver, start_display
URL = sys.argv[1]
start_display("99"); os.environ["DISPLAY"] = ":99"
d = build_driver()
try:
    d.get(URL)
    time.sleep(8)
    diag = d.execute_script("""
      const v=document.querySelector('video');
      return {
        status: (document.getElementById('s')||{}).textContent,
        vids: document.querySelectorAll('video').length,
        w: v?v.videoWidth:null, h: v?v.videoHeight:null,
        readyState: v?v.readyState:null, paused: v?v.paused:null,
        hasSrcObject: v? !!v.srcObject : null,
        tracks: (v&&v.srcObject)? v.srcObject.getTracks().map(t=>t.kind+':'+t.readyState) : null,
      };
    """)
    print("  page diag:", json.dumps(diag))
    for e in d.get_log("browser")[-8:]:
        print("   console:", e.get("level"), str(e.get("message"))[:160])
finally:
    try: d.quit()
    except Exception: pass
