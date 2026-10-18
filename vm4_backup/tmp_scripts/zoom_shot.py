"""Screenshot the launcher, dismiss any consent overlay, click, screenshot again."""
import os, sys, time, json
sys.path.insert(0, "/app")
from collect import start_display, build_driver, ensure_window_size
from selenium.webdriver.common.by import By
URL=os.environ.get("ZOOM_URL","")
start_display("99"); os.environ["DISPLAY"]=":99"
d=build_driver(); d.set_page_load_timeout(60); d.set_script_timeout(30)
try:
    ensure_window_size(d,"zoom")
    d.get(URL); time.sleep(10)
    d.save_screenshot("/out/zoom_1_landing.png"); print("  shot 1: landing", flush=True)

    # Is anything overlaying the Join button?
    info=d.execute_script(r"""
      const b=[...document.querySelectorAll('button')].find(x=>(x.innerText||'').trim()==='Join from browser');
      if(!b) return {found:false};
      const r=b.getBoundingClientRect();
      const cx=Math.round(r.left+r.width/2), cy=Math.round(r.top+r.height/2);
      const top=document.elementFromPoint(cx,cy);
      return {found:true, rect:[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)],
              topmost_tag: top?top.tagName:null,
              topmost_cls: top?(top.className||'').toString().slice(0,80):null,
              is_the_button: top===b || (b.contains(top)),
              overlays: [...document.querySelectorAll('div,section')].filter(e=>{
                 const s=getComputedStyle(e); const rr=e.getBoundingClientRect();
                 return (s.position==='fixed'||s.position==='absolute') && rr.width>300 && rr.height>150
                        && s.visibility!=='hidden' && s.display!=='none' && (parseFloat(s.zIndex)||0)>0;
               }).map(e=>({cls:(e.className||'').toString().slice(0,50), z:getComputedStyle(e).zIndex})).slice(0,6)};
    """)
    print(f"  overlay check: {json.dumps(info)[:600]}", flush=True)

    # dismiss cookie/consent if present
    for label in ("Accept All Cookies","Accept Cookies","I Agree","Agree","Close"):
        try:
            for e in d.find_elements(By.XPATH, f"//button[normalize-space()='{label}']"):
                if e.is_displayed():
                    e.click(); print(f"  dismissed consent via '{label}'", flush=True); time.sleep(3); break
        except Exception: pass
    d.save_screenshot("/out/zoom_2_after_consent.png"); print("  shot 2: after consent", flush=True)

    for e in d.find_elements(By.XPATH, "//button[normalize-space()='Join from browser']"):
        if e.is_displayed():
            d.execute_script("arguments[0].scrollIntoView({block:'center'});", e); time.sleep(0.5)
            try:
                e.click(); print("  clicked Join from browser", flush=True)
            except Exception as ex:
                print(f"  click raised: {type(ex).__name__}: {str(ex)[:120]}", flush=True)
            break
    time.sleep(12)
    d.save_screenshot("/out/zoom_3_after_click.png"); print("  shot 3: after click", flush=True)
    print(f"  url now : {d.current_url[:120]}", flush=True)
    print(f"  title   : {d.title[:70]}", flush=True)
    print(f"  handles : {len(d.window_handles)}", flush=True)
    BODY_JS = "return (document.body.innerText||'').replace(/\\s+/g,' ').slice(0,300);"
    print("  body    : " + str(d.execute_script(BODY_JS)), flush=True)
finally:
    try: d.quit()
    except Exception: pass
print("ZOOM_SHOT_DONE", flush=True)
