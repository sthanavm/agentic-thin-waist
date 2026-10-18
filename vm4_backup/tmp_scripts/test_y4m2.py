import os, sys, time, json, subprocess
sys.path.insert(0,"/app")
subprocess.run(["python3","/mk_y4m.py","/tmp/testpattern.y4m"], check=True)
from collect import start_display
start_display("99"); os.environ["DISPLAY"]=":99"
from seleniumbase import Driver
args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu",
      "--use-fake-ui-for-media-stream","--use-fake-device-for-media-stream",
      "--use-file-for-fake-video-capture=/tmp/testpattern.y4m",
      "--window-size=1280,800","--autoplay-policy=no-user-gesture-required"]
d=Driver(browser="chrome", headed=True, uc=True, undetectable=True,
         use_auto_ext=False, chromium_arg=",".join(args))   # comma-joined, as build_driver does
d.set_script_timeout(30)
try:
    d.get("https://example.com")
    JS=r"""
    const done=arguments[arguments.length-1];
    navigator.mediaDevices.getUserMedia({video:true, audio:false}).then(async s=>{
      const t=s.getVideoTracks()[0]; const st=t.getSettings();
      const v=document.createElement('video'); v.srcObject=s; v.muted=true; await v.play();
      await new Promise(r=>setTimeout(r,600));
      const c=document.createElement('canvas'); c.width=v.videoWidth; c.height=v.videoHeight;
      const ctx=c.getContext('2d'); const samples=[];
      for(let i=0;i<6;i++){
        ctx.drawImage(v,0,0);
        const p=ctx.getImageData(Math.floor(c.width/2), Math.floor(c.height/2), 1, 1).data;
        samples.push(p[0]);
        await new Promise(r=>setTimeout(r,250));
      }
      done({ok:true, label:t.label, settings:{w:st.width,h:st.height,fps:st.frameRate},
            video:[v.videoWidth,v.videoHeight], center_luma_samples:samples});
    }).catch(e=>done({ok:false, err:String(e)}));
    """
    r=d.execute_async_script(JS)
    print(json.dumps(r, indent=1), flush=True)
    if r.get("ok"):
        s=r["center_luma_samples"]
        print(f"\n  file says 320x240; track reports {r['video']}", flush=True)
        print(f"  centre luma over time: {s}  -> changing: {len(set(s))>1}", flush=True)
        print(f"  (our y4m frames carry luma 16,24,32,... so a changing flat value = our file)", flush=True)
finally:
    try: d.quit()
    except Exception: pass
print("Y4M2_DONE", flush=True)
