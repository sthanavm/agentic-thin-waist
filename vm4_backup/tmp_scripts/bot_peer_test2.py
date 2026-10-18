"""Retry with correct in-call detection (the waiting room also has a Leave button)."""
import os, sys, time, json, subprocess
sys.path.insert(0, "/app")
from collect import (start_display, ensure_window_size, install_webrtc_hook,
                     get_google_meet_stats, GOOGLE_MEET_JOIN_JS)
from seleniumbase import Driver
ROOM=os.environ.get("MEET_ROOM","https://meet.google.com/kmr-oamc-jam"); Y4M="/tmp/bot.y4m"
subprocess.run(["python3","/mk_y4m2.py",Y4M], check=True)
BASE=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu",
      "--use-fake-ui-for-media-stream","--use-fake-device-for-media-stream",
      "--disable-blink-features=AutomationControlled","--window-size=1920,1080",
      "--autoplay-policy=no-user-gesture-required","--no-first-run","--no-default-browser-check"]
def mk(disp, extra=()):
    os.environ["DISPLAY"]=f":{disp}"
    return Driver(browser="chrome",headed=True,uc=True,undetectable=True,
                  use_auto_ext=False,chromium_arg=",".join(list(BASE)+list(extra)))
STATE_JS = r"""
const t=(document.body.innerText||'').replace(/\s+/g,' ');
return {
  waiting: /wait until a meeting host|asking to be let in|Asking to join/i.test(t),
  denied:  /denied|can't join|cannot join|no one responded/i.test(t),
  // real in-call chrome: participant/chat panel buttons only exist inside the call
  in_call: !!document.querySelector('[aria-label*="Show everyone" i],[aria-label*="Chat with everyone" i]'),
  snippet: t.slice(0,150)
};"""
def click_join(d,name,tag):
    for _ in range(12):
        try:
            r=d.execute_script(GOOGLE_MEET_JOIN_JS,name)
            if r.get("clicked"):
                print(f"  [{tag}] clicked: '{r.get('label')}'  <-- note which control Meet offered", flush=True)
                return r
        except Exception as e: print(f"  [{tag}] join js: {str(e)[:70]}", flush=True)
        time.sleep(3)
    return None
print(f"=== room {ROOM} ===", flush=True)
start_display("99"); start_display("100")
drivers={}
for tag,disp,extra in (("botA","99",[f"--use-file-for-fake-video-capture={Y4M}"]),("botB","100",[])):
    d=mk(disp,extra); d.set_page_load_timeout(60); d.set_script_timeout(30)
    ensure_window_size(d,tag)
    if tag=="botB": install_webrtc_hook(d)
    d.get(ROOM); time.sleep(10)
    click_join(d,f"Bot-{tag}",tag)
    drivers[tag]=d
print("\n=== watching admission state for 150s ===", flush=True)
for i in range(10):
    time.sleep(15)
    line=f"  t+{(i+1)*15:>4}s "
    for tag,d in drivers.items():
        try: s=d.execute_script(STATE_JS)
        except Exception as e: s={"snippet":str(e)[:40]}
        line+=f"| {tag}: in_call={s.get('in_call')} waiting={s.get('waiting')} "
    try:
        st=get_google_meet_stats(drivers["botB"])
        line+=f"| inbound_streams={st.get('inbound_video_stream_count')} bytes={st.get('bytes_received')} frames={st.get('frames_decoded')}"
    except Exception as e: line+=f"| stats err {str(e)[:30]}"
    print(line, flush=True)
print("\n=== final screen text ===", flush=True)
for tag,d in drivers.items():
    try: print(f"  [{tag}] {d.execute_script(STATE_JS)}", flush=True)
    except Exception as e: print(f"  [{tag}] {e}", flush=True)
try:
    st=get_google_meet_stats(drivers["botB"])
    print(f"\n  final stats: pcs={st.get('peer_connection_count')} inbound={st.get('inbound_video_stream_count')} "
          f"bytes={st.get('bytes_received')} frames={st.get('frames_decoded')} res={st.get('resolution')}", flush=True)
except Exception as e: print(f"  final stats err: {e}", flush=True)
for d in drivers.values():
    try: d.quit()
    except Exception: pass
print("BOT_TEST2_DONE", flush=True)
