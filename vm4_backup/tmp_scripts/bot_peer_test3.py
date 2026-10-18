"""Two bots join the room; operator admits; then measure real inbound video."""
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

STATE_JS=r"""
const t=(document.body.innerText||'').replace(/\s+/g,' ');
return {waiting: /wait until a meeting host|asking to be let in/i.test(t),
        denied: /denied|can't join|no one responded/i.test(t), snippet:t.slice(0,110)};"""

OUTBOUND_JS=r"""
const done=arguments[arguments.length-1];
(async()=>{ const pcs=Array.from(new Set(window.__netgentPeerConnections||[]));
 let b=0,f=0,w=0,h=0,n=0;
 for(const pc of pcs){ const r=await pc.getStats();
   r.forEach(s=>{ if(s.type==='outbound-rtp' && (s.kind||s.mediaType)==='video'){
     n++; b+=s.bytesSent||0; f+=s.framesSent||0; w=Math.max(w,s.frameWidth||0); h=Math.max(h,s.frameHeight||0);}});}
 done({streams:n, bytes_sent:b, frames_sent:f, w:w, h:h});})();"""

def click_join(d,name,tag):
    for _ in range(12):
        try:
            r=d.execute_script(GOOGLE_MEET_JOIN_JS,name)
            if r.get("clicked"):
                print(f"  [{tag}] clicked '{r.get('label')}'", flush=True); return r
        except Exception as e: print(f"  [{tag}] {str(e)[:60]}", flush=True)
        time.sleep(3)
    return None

print(f"=== room {ROOM} ===", flush=True)
start_display("99"); start_display("100")
drv={}
for tag,disp,extra in (("botA-publisher","99",[f"--use-file-for-fake-video-capture={Y4M}"]),
                       ("botB-measuring","100",[])):
    d=mk(disp,extra); d.set_page_load_timeout(60); d.set_script_timeout(30)
    ensure_window_size(d,tag)
    install_webrtc_hook(d)            # both: A for outbound proof, B for inbound stats
    d.get(ROOM); time.sleep(10)
    click_join(d,f"Bot-{tag.split('-')[0][-1].upper()}",tag)
    drv[tag]=d

print("\n" + "="*64, flush=True)
print("  >>> BOTH BOTS ARE NOW KNOCKING - PLEASE ADMIT THEM IN MEET <<<", flush=True)
print("  waiting up to 5 minutes for admission ...", flush=True)
print("="*64 + "\n", flush=True)

admitted_at=None
for i in range(30):                    # 30 x 10s = 5 min
    time.sleep(10)
    states={}
    for tag,d in drv.items():
        try: states[tag]=d.execute_script(STATE_JS)
        except Exception as e: states[tag]={"snippet":str(e)[:40]}
    try: st=get_google_meet_stats(drv["botB-measuring"])
    except Exception: st={}
    inb=st.get("inbound_video_stream_count",0); by=st.get("bytes_received",0)
    wait_flags={t:s.get("waiting") for t,s in states.items()}
    print(f"  t+{(i+1)*10:>4}s  waiting={wait_flags}  inbound_streams={inb} bytes={by}", flush=True)
    if inb and by:
        admitted_at=(i+1)*10
        print(f"\n  *** ADMITTED - inbound video detected at t+{admitted_at}s ***\n", flush=True)
        break

if admitted_at is None:
    print("\n  never received inbound video within 5 minutes", flush=True)
    for t,d in drv.items():
        try: print(f"  [{t}] {d.execute_script(STATE_JS)}", flush=True)
        except Exception: pass
else:
    print("=== sampling 60s of real inbound video on botB ===", flush=True)
    first=None; last=None
    for i in range(12):
        time.sleep(5)
        st=get_google_meet_stats(drv["botB-measuring"])
        ob=drv["botA-publisher"].execute_async_script(OUTBOUND_JS)
        if first is None: first=(st,time.time())
        last=(st,time.time())
        print(f"  t+{(i+1)*5:>3}s  IN: {st.get('resolution')} @{st.get('frames_per_second')}fps "
              f"bytes={st.get('bytes_received')} frames_dec={st.get('frames_decoded')} "
              f"pkts={st.get('packets_received')} lost={st.get('packets_lost')} "
              f"jitter={st.get('jitter_seconds')}  || OUT(botA): {ob['w']}x{ob['h']} "
              f"bytes_sent={ob['bytes_sent']} frames_sent={ob['frames_sent']}", flush=True)
    s0,t0=first; s1,t1=last; dt=t1-t0
    db=(s1.get('bytes_received',0)-s0.get('bytes_received',0))
    print("\n=== MEASURED QoE ===", flush=True)
    print(f"  window                : {dt:.1f} s", flush=True)
    print(f"  resolution            : {s1.get('resolution')}", flush=True)
    print(f"  fps                   : {s1.get('frames_per_second')}", flush=True)
    print(f"  inbound bitrate       : {db*8/max(dt,1e-9)/1e6:.3f} Mbps  ({db} bytes)", flush=True)
    print(f"  frames_decoded        : {s0.get('frames_decoded')} -> {s1.get('frames_decoded')}", flush=True)
    print(f"  packets recv / lost   : {s1.get('packets_received')} / {s1.get('packets_lost')}", flush=True)
    print(f"  jitter / freeze_count : {s1.get('jitter_seconds')} / {s1.get('freeze_count')}", flush=True)
    print(f"  codec                 : {s1.get('codecs')}", flush=True)
    print(f"  peer_connection_count : {s1.get('peer_connection_count')}", flush=True)
    print(f"\n  full final sample: {json.dumps(s1)[:900]}", flush=True)
for d in drv.values():
    try: d.quit()
    except Exception: pass
print("BOT_TEST3_DONE", flush=True)
