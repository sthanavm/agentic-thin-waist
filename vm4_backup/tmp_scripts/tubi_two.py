import os, sys, time, json, re
sys.path.insert(0, "/app")
from collect import start_display, build_driver, ensure_window_size
TITLES=[("another-cinderella-story","https://tubitv.com/movies/593796/another-cinderella-story"),
        ("kung-fu-panda-2","https://tubitv.com/movies/100031650/kung-fu-panda-2"),
        ("the-mummy","https://tubitv.com/movies/100003216/the-mummy")]
start_display("99"); os.environ["DISPLAY"]=":99"
FETCH="""const done=arguments[arguments.length-1];
fetch(arguments[0]).then(r=>r.text()).then(t=>done(t.slice(0,6000))).catch(e=>done('ERR '+e));"""
for name,url in TITLES:
    d=build_driver(); d.set_page_load_timeout(60); d.set_script_timeout(40)
    ensure_window_size(d,"tubi")
    try:
        d.get(url)
        ok=False
        for _ in range(40):
            time.sleep(2)
            if d.execute_script("const v=document.querySelector('video');return v?v.videoWidth:0;"): ok=True; break
        if not ok:
            print(f"\n  {name}: never started playing", flush=True); d.quit(); continue
        time.sleep(30)
        final=d.execute_script("const v=document.querySelector('video');return v.videoWidth+'x'+v.videoHeight;")
        m3u8=d.execute_script("return [...new Set(performance.getEntriesByType('resource').map(e=>e.name).filter(u=>/\\.m3u8/i.test(u)))];") or []
        masters=[]
        for u in m3u8:
            try: txt=d.execute_async_script(FETCH,u)
            except Exception: continue
            if isinstance(txt,str) and not txt.startswith("ERR"):
                res=re.findall(r'RESOLUTION=(\d+x\d+)', txt)
                if res: masters.append((u.split("/")[-1].split("?")[0],
                                        sorted(set(res), key=lambda x:int(x.split('x')[1]))))
        print(f"\n  === {name} ===", flush=True)
        print(f"    AUTO settled at : {final}", flush=True)
        for tag,v in masters: print(f"    master {tag:<20} {v}", flush=True)
        tops=sorted({int(v[-1].split('x')[1]) for _,v in masters}) if masters else []
        print(f"    distinct master top-heights: {tops}", flush=True)
    except Exception as e:
        print(f"  {name}: FAILED {type(e).__name__}: {e}", flush=True)
    finally:
        try: d.quit()
        except Exception: pass
print("\nTUBI_TWO_DONE", flush=True)
