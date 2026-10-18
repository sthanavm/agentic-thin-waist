"""Two bots in one real Meet room: one publishes fake video, one measures inbound."""
import os, sys, time, json, subprocess, threading
sys.path.insert(0, "/app")
from collect import (start_display, ensure_window_size, install_webrtc_hook,
                     get_google_meet_stats, GOOGLE_MEET_JOIN_JS)
from seleniumbase import Driver

ROOM = os.environ.get("MEET_ROOM", "https://meet.google.com/kmr-oamc-jam")
Y4M  = "/tmp/bot.y4m"
subprocess.run(["python3", "/mk_y4m2.py", Y4M], check=True)

BASE = ["--no-sandbox","--disable-dev-shm-usage","--disable-gpu",
        "--use-fake-ui-for-media-stream","--use-fake-device-for-media-stream",
        "--disable-blink-features=AutomationControlled","--force-device-scale-factor=1",
        "--window-size=1920,1080","--autoplay-policy=no-user-gesture-required",
        "--disable-background-networking","--no-first-run","--no-default-browser-check"]

def make_driver(display, extra=()):
    os.environ["DISPLAY"] = f":{display}"
    args = list(BASE) + list(extra)
    return Driver(browser="chrome", headed=True, uc=True, undetectable=True,
                  use_auto_ext=False, chromium_arg=",".join(args))   # comma-joined

def join(driver, name, tag, timeout=120):
    """Click through Meet's guest-join UI. Returns (joined, detail)."""
    t0 = time.time(); last = None
    while time.time() - t0 < timeout:
        try:
            r = driver.execute_script(GOOGLE_MEET_JOIN_JS, name)
            last = r
            if r.get("clicked"):
                print(f"  [{tag}] clicked join control: {r.get('label')}", flush=True)
                time.sleep(8)
                # in-call if the leave-call control exists
                incall = driver.execute_script("""
                  return !!document.querySelector('[aria-label*="Leave call" i],[data-tooltip*="Leave call" i]');""")
                if incall:
                    return True, r
        except Exception as e:
            last = {"err": str(e)[:80]}
        time.sleep(3)
        try:
            incall = driver.execute_script("""
              return !!document.querySelector('[aria-label*="Leave call" i],[data-tooltip*="Leave call" i]');""")
            if incall: return True, last
        except Exception: pass
    return False, last

def screen_state(driver):
    try:
        return driver.execute_script("""
          const t=(document.body.innerText||'').replace(/\\s+/g,' ').slice(0,260);
          return {text:t, url:location.href.slice(0,80)};""")
    except Exception as e:
        return {"err": str(e)[:80]}

print(f"=== room: {ROOM} ===", flush=True)
start_display("99"); start_display("100")

print("\n=== BOT A (publisher, fake video from y4m) joining ===", flush=True)
a = make_driver("99", [f"--use-file-for-fake-video-capture={Y4M}"])
a.set_page_load_timeout(60); a.set_script_timeout(30)
ensure_window_size(a, "botA")
a.get(ROOM); time.sleep(10)
okA, dA = join(a, "BotA-Publisher", "botA")
print(f"  [botA] joined={okA} detail={dA}", flush=True)
print(f"  [botA] screen: {screen_state(a)}", flush=True)

print("\n=== BOT B (app under test, reads inbound stats) joining ===", flush=True)
b = make_driver("100")
b.set_page_load_timeout(60); b.set_script_timeout(30)
ensure_window_size(b, "botB")
install_webrtc_hook(b)
b.get(ROOM); time.sleep(10)
okB, dB = join(b, "BotB-Measuring", "botB")
print(f"  [botB] joined={okB} detail={dB}", flush=True)
print(f"  [botB] screen: {screen_state(b)}", flush=True)

print("\n=== sampling inbound WebRTC stats on BOT B for 60s ===", flush=True)
rows=[]
for i in range(20):
    time.sleep(3)
    try:
        st = get_google_meet_stats(b)
    except Exception as e:
        print(f"  t+{(i+1)*3:>3}s  stats error: {type(e).__name__}: {str(e)[:80]}", flush=True); continue
    inb = st.get("inbound") or st.get("streams") or []
    tot_bytes = sum(x.get("bytes_received",0) for x in inb) if isinstance(inb,list) else 0
    frames = sum(x.get("frames_decoded",0) or 0 for x in inb) if isinstance(inb,list) else 0
    dims = [f"{x.get('frame_width')}x{x.get('frame_height')}" for x in inb] if isinstance(inb,list) else []
    rows.append((tot_bytes, frames))
    print(f"  t+{(i+1)*3:>3}s  streams={len(inb) if isinstance(inb,list) else '?'}  "
          f"bytes={tot_bytes}  frames_decoded={frames}  dims={dims}", flush=True)
    if i == 19 or (i==10 and tot_bytes):
        print(f"      raw sample: {json.dumps(st)[:600]}", flush=True)

print("\n=== RESULT ===", flush=True)
if rows and rows[-1][0] > 0:
    dur = 60.0
    dbytes = rows[-1][0] - rows[0][0]
    print(f"  inbound bytes grew {rows[0][0]} -> {rows[-1][0]}  (+{dbytes})", flush=True)
    print(f"  implied inbound bitrate: {dbytes*8/dur/1e6:.3f} Mbps", flush=True)
    print(f"  frames_decoded grew {rows[0][1]} -> {rows[-1][1]}", flush=True)
else:
    print("  NO non-zero inbound video stats were observed", flush=True)
for d in (a,b):
    try: d.quit()
    except Exception: pass
print("BOT_PEER_TEST_DONE", flush=True)
