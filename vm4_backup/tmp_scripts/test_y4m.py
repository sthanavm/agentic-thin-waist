"""Does --use-file-for-fake-video-capture actually feed our file to getUserMedia?"""
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
d=Driver(browser="chrome", headed=True, uc=False, chromium_arg=" ".join(args))
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
      const ctx=c.getContext('2d');
      const samples=[];
      for(let i=0;i<6;i++){
        ctx.drawImage(v,0,0);
        const p=ctx.getImageData(Math.floor(c.width/2), Math.floor(c.height/2), 1, 1).data;
        samples.push([p[0],p[1],p[2]]);
        await new Promise(r=>setTimeout(r,250));
      }
      done({ok:true, label:t.label, settings:{w:st.width,h:st.height,fps:st.frameRate},
             video:[v.videoWidth,v.videoHeight], center_rgb_samples:samples});
    }).catch(e=>done({ok:false, err:String(e)}));
    """
    print(json.dumps(d.execute_async_script(JS), indent=1), flush=True)
finally:
    try: d.quit()
    except Exception: pass
print("Y4M_TEST_DONE", flush=True)
