"""What renditions does this Tubi title actually publish?"""
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
time.sleep(30)
print("  settled at:", d.execute_script("const v=document.querySelector('video');return v.videoWidth+'x'+v.videoHeight;"), flush=True)

# 1. find manifest + segment URLs from resource timing
urls = d.execute_script("""
return performance.getEntriesByType('resource').map(e=>e.name)
  .filter(u=>/\\.m3u8|\\.mpd|manifest|\\.ts(\\?|$)|\\.m4s|seg/i.test(u)).slice(0,400);
""") or []
man=[u for u in urls if re.search(r'\.m3u8|\.mpd|manifest', u, re.I)]
seg=[u for u in urls if u not in man]
print(f"  manifest-ish URLs: {len(man)}   segment-ish URLs: {len(seg)}", flush=True)
for u in man[:6]: print("    M", u[:140], flush=True)
for u in seg[:4]: print("    S", u[:140], flush=True)

# 2. fetch the master manifest from page context and read its variants
if man:
    FETCH = """
    const done=arguments[arguments.length-1];
    fetch(arguments[0]).then(r=>r.text()).then(t=>done(t.slice(0,4000))).catch(e=>done('ERR '+e));
    """
    for u in man[:3]:
        try:
            txt=d.execute_async_script(FETCH, u)
        except Exception as e:
            txt=f"ERR {e}"
        print(f"\n  --- manifest {u[:90]} ---", flush=True)
        if txt.startswith("ERR"):
            print("   ", txt[:160], flush=True); continue
        res=re.findall(r'RESOLUTION=(\d+x\d+)', txt)
        bw=re.findall(r'BANDWIDTH=(\d+)', txt)
        if res:
            print(f"    RESOLUTIONS advertised: {sorted(set(res), key=lambda s:int(s.split('x')[1]))}", flush=True)
            print(f"    BANDWIDTHs: {sorted(set(int(b) for b in bw))}", flush=True)
        else:
            print("    (no RESOLUTION tags; first lines below)", flush=True)
            print("   ", " | ".join(txt.splitlines()[:8])[:300], flush=True)
# 3. resolutions hinted in segment paths
hints=sorted(set(re.findall(r'(\d{3,4})[pP][/_.-]', " ".join(seg))))
print(f"\n  rendition hints in segment paths: {hints}", flush=True)
try: d.quit()
except Exception: pass
print("TUBI_MANIFEST_DONE", flush=True)
