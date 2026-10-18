import os, sys, time, json
sys.path.insert(0, "/app")
from collect import start_display, build_driver, ensure_window_size
start_display("99"); os.environ["DISPLAY"]=":99"
d=build_driver(); d.set_page_load_timeout(60)
ensure_window_size(d,"twitch")
d.get("https://www.twitch.tv/relaxbeats"); time.sleep(40)
JS = r"""
function findPlayer(root){const seen=new Set();const st=[root];let n=0;
 while(st.length&&n++<200000){const x=st.pop();if(!x||typeof x!=='object'||seen.has(x))continue;seen.add(x);
 try{if(typeof x.getQualities==='function'&&typeof x.getQuality==='function')return x;}catch(e){}
 for(const k in x){try{const v=x[k];if(v&&typeof v==='object')st.push(v);}catch(e){}}}
 return null;}
const host=document.querySelector('[data-a-target="video-player"]')||document.body;
const fk=Object.keys(host).find(k=>k.startsWith('__reactFiber$')||k.startsWith('__reactInternalInstance$'));
const p=fk?findPlayer(host[fk]):null;
const v=document.querySelector('video');
const out={decoded:v?(v.videoWidth+'x'+v.videoHeight):null};
if(p){ let q=null; try{q=p.getQualities();}catch(e){out.err=String(e);}
  out.type=Object.prototype.toString.call(q);
  try{ out.raw = JSON.parse(JSON.stringify(q)); }catch(e){ out.raw_err=String(e); }
  try{ out.current = JSON.parse(JSON.stringify(p.getQuality())); }catch(e){}
}
return out;
"""
try: res=d.execute_script(JS)
except Exception as e: res={"error":f"{type(e).__name__}: {e}"}
print(json.dumps(res, indent=1)[:1800], flush=True)
try: d.quit()
except Exception: pass
print("TQ2_DONE", flush=True)
