import json, sys, os, time
sys.path.insert(0, "/app")
from collect import start_display
start_display("99"); os.environ["DISPLAY"] = ":99"
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
JS = """
  const done = arguments[arguments.length-1];
  const pc = new RTCPeerConnection({iceServers:[{urls:'stun:stun.l.google.com:19302'}]});
  const c = [];
  pc.onicecandidate = e => { if (e.candidate) c.push(e.candidate.candidate); };
  pc.createDataChannel('x');
  pc.createOffer().then(o => pc.setLocalDescription(o));
  setTimeout(() => done({n:c.length, state:pc.iceGatheringState,
      types:[...new Set(c.map(x=>(x.match(/ typ (\\w+)/)||[])[1]))], sample:c.slice(0,3)}), 7000);
"""
for label, extra in [("minimal (no-sandbox only)", []),
                     ("+ our media flags", ["--use-fake-ui-for-media-stream",
                                            "--use-fake-device-for-media-stream"]),
                     ("+ disable-background-networking", ["--disable-background-networking"])]:
    o = Options()
    o.add_argument("--no-sandbox"); o.add_argument("--disable-dev-shm-usage")
    o.add_argument("--disable-gpu"); o.add_argument("--headless=new")
    for a in extra: o.add_argument(a)
    try:
        d = webdriver.Chrome(options=o)
        d.get("https://example.com/")
        r = d.execute_async_script(JS)
        print("  %-34s candidates=%-3s state=%-9s types=%s" % (label, r["n"], r["state"], r["types"]))
        for s in r["sample"]: print("        ", s)
        d.quit()
    except Exception as e:
        print("  %-34s ERROR %s %s" % (label, type(e).__name__, str(e)[:90]))
