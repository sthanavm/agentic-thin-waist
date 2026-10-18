import json, sys, os, time
sys.path.insert(0, "/app")
from collect import start_display, STATS_JS
start_display("99"); os.environ["DISPLAY"] = ":99"
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
o = Options()
for a in ["--no-sandbox","--disable-dev-shm-usage","--disable-gpu","--headless=new",
          "--use-fake-ui-for-media-stream","--use-fake-device-for-media-stream",
          "--autoplay-policy=no-user-gesture-required","--window-size=1920,1080"]:
    o.add_argument(a)
d = webdriver.Chrome(options=o)
try:
    d.get(sys.argv[1]); time.sleep(14)
    tr = d.execute_script("return window.__trace || []")
    print("  trace:", " -> ".join(e["k"] for e in tr))
    st = d.execute_script(STATS_JS)
    print("  video    :", json.dumps((st or {}).get("video")))
    print("  playback :", json.dumps((st or {}).get("playback")))
    print("  frames   :", json.dumps((st or {}).get("frames")))
    time.sleep(4)
    st2 = d.execute_script(STATS_JS)
    f1 = ((st or {}).get("frames") or {}).get("total") or 0
    f2 = ((st2 or {}).get("frames") or {}).get("total") or 0
    print("  frames advancing: %s -> %s  => %s" % (f1, f2, "DECODING" if f2 > f1 else "STATIC"))
finally:
    try: d.quit()
    except Exception: pass
