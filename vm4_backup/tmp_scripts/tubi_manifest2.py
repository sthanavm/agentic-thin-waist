import os, sys, time, json, re
sys.path.insert(0, "/app")
from collect import start_display, build_driver, ensure_window_size
URL="https://tubitv.com/movies/593796/another-cinderella-story"
start_display("99"); os.environ["DISPLAY"]=":99"
d=build_driver(); d.set_page_load_timeout(60); d.set_script_timeout(40)
ensure_window_size(d,"tubi")
d.get(URL)
for _ in range(40):
    time.sleep(2)
    if d.execute_script("const v=document.querySelector('video');return v?v.videoWidth:0;"): break
time.sleep(35)
final=d.execute_script("const v=document.querySelector('video');return v.videoWidth+'x'+v.videoHeight;")
print(f"  AUTO settled at: {final}", flush=True)
elem=d.execute_script("const v=document.querySelector('video');const r=v.getBoundingClientRect();return Math.round(r.width)+'x'+Math.round(r.height);")
print(f"  player element : {elem}", flush=True)

m3u8 = d.execute_script("""
return [...new Set(performance.getEntriesByType('resource').map(e=>e.name).filter(u=>/\\.m3u8/i.test(u)))];
""") or []
print(f"  .m3u8 URLs seen: {len(m3u8)}", flush=True)
FETCH="""const done=arguments[arguments.length-1];
fetch(arguments[0]).then(r=>r.text()).then(t=>done(t.slice(0,6000))).catch(e=>done('ERR '+e));"""
allres=set()
for u in m3u8:
    try: txt=d.execute_async_script(FETCH,u)
    except Exception as e: txt=f"ERR {e}"
    tag=u.split("/")[-1].split("?")[0]
    if txt.startswith("ERR"):
        print(f"    {tag}: {txt[:90]}", flush=True); continue
    res=re.findall(r'RESOLUTION=(\d+x\d+)', txt)
    if res:
        s=sorted(set(res), key=lambda x:int(x.split('x')[1]))
        allres.update(s)
        print(f"    {tag}: MASTER, variants {s}", flush=True)
    else:
        n=len(re.findall(r'#EXTINF', txt))
        print(f"    {tag}: media playlist ({n} segments), no variants", flush=True)
print(f"\n  UNION of all advertised video renditions: {sorted(allres, key=lambda x:int(x.split('x')[1]))}", flush=True)
print(f"  what we actually decoded on AUTO       : {final}", flush=True)
try: d.quit()
except Exception: pass
print("TUBI_M2_DONE", flush=True)
