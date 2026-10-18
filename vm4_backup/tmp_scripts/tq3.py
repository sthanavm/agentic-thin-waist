import os, sys, time, json
sys.path.insert(0, "/app")
from collect import start_display, build_driver, ensure_window_size
start_display("99"); os.environ["DISPLAY"]=":99"
d=build_driver(); d.set_page_load_timeout(60); d.set_script_timeout(30)
ensure_window_size(d,"twitch")
d.get("https://www.twitch.tv/relaxbeats"); time.sleep(40)
JS = r"""
const done = arguments[arguments.length-1];
function findPlayer(root){const seen=new Set();const st=[root];let n=0;
 while(st.length&&n++<200000){const x=st.pop();if(!x||typeof x!=='object'||seen.has(x))continue;seen.add(x);
 try{if(typeof x.getQualities==='function'&&typeof x.getQuality==='function')return x;}catch(e){}
 for(const k in x){try{const v=x[k];if(v&&typeof v==='object')st.push(v);}catch(e){}}}
 return null;}
const host=document.querySelector('[data-a-target="video-player"]')||document.body;
const fk=Object.keys(host).find(k=>k.startsWith('__reactFiber$')||k.startsWith('__reactInternalInstance$'));
const p=fk?findPlayer(host[fk]):null;
const v=document.querySelector('video');
const out={decoded:v?(v.videoWidth+'x'+v.videoHeight):null, found:!!p};
if(!p){ done(out); }
else {
  Promise.resolve(p.getQualities()).then(function(q){
    try{ out.qualities = (q||[]).map(function(x){return {name:x.name, group:x.group, height:x.height, bitrate:x.bitrate};}); }
    catch(e){ out.map_err=String(e); out.raw=JSON.parse(JSON.stringify(q||null)); }
    return Promise.resolve(p.getQuality());
  }).then(function(c){ try{ out.current=JSON.parse(JSON.stringify(c)); }catch(e){} done(out); })
   .catch(function(e){ out.err=String(e); done(out); });
}
"""
try: res=d.execute_async_script(JS)
except Exception as e: res={"error":f"{type(e).__name__}: {e}"}
print(json.dumps(res, indent=1)[:2000], flush=True)
try: d.quit()
except Exception: pass
print("TQ3_DONE", flush=True)
