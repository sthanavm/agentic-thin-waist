import json, sys, os, time
sys.path.insert(0, "/app")
from collect import start_display, ZOOM_JOIN_JS, ZOOM_READY_JS
from bot_peer import build_bot_driver
from make_test_clip import write_clip
from make_test_audio import write_tone
URL = sys.argv[1]
start_display("99"); os.environ["DISPLAY"] = ":99"
if not os.path.exists("/tmp/c.y4m"): write_clip("/tmp/c.y4m", 320, 240, 30, 30)
if not os.path.exists("/tmp/a.wav"): write_tone("/tmp/a.wav", seconds=5)
d = build_bot_driver("/tmp/c.y4m", "/tmp/a.wav")
try:
    d.get(URL); time.sleep(12)
    for rnd in range(3):
        r = d.execute_script(ZOOM_JOIN_JS, "pramana-peer")
        print("  join attempt %d -> %s" % (rnd+1, json.dumps(r)))
        time.sleep(6)
    print("  ready:", json.dumps(d.execute_script(ZOOM_READY_JS)))
    diag = d.execute_script("""
      const t=(document.body.innerText||'').replace(/\\s+/g,' ').slice(0,300);
      const btns=[...document.querySelectorAll('button,[role=button],a')]
        .map(b=>(b.innerText||b.getAttribute('aria-label')||'').trim()).filter(Boolean).slice(0,18);
      const inputs=[...document.querySelectorAll('input')].map(i=>i.id||i.placeholder||i.name).slice(0,8);
      return {url:location.href, text:t, buttons:btns, inputs:inputs,
              iframes:document.querySelectorAll('iframe').length,
              videos:document.querySelectorAll('video').length};
    """)
    print("  URL     :", diag["url"][:110])
    print("  iframes :", diag["iframes"], "| videos:", diag["videos"])
    print("  inputs  :", diag["inputs"])
    print("  buttons :", diag["buttons"])
    print("  text    :", diag["text"][:220])
finally:
    try: d.quit()
    except Exception: pass
