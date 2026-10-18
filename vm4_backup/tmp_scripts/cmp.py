import json,sys,pathlib,importlib.util
H=pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0,str(H))
def load(name,path):
    sp=importlib.util.spec_from_file_location(name,path); m=importlib.util.module_from_spec(sp)
    sys.modules[name]=m; sp.loader.exec_module(m); return m
NEW=load("qoe_new", H/"shared/qoe.py")
PREV=load("qoe_prev", "/tmp/qoe_prev.py")
base=H/"experiments/pramana/results/pramana_runs"
KEYS=("video_resolution_p","frame_rate_fps","dropped_video_frames","watched_seconds",
      "rebuffer_events","rebuffer_duration_ms","stall_duration_ms","video_startup_time_ms","total_video_frames")
same=diff=err=0; rows=[]; byapp={}
for f in sorted(base.glob("*/qoe/*_stats.jsonl")):
    app=f.name.replace("_stats.jsonl","")
    try:
        a=PREV.summarize(f,app); b=NEW.summarize(f,app)
    except Exception as e:
        err+=1; continue
    if a.get("status")!="ok" and b.get("status")!="ok": continue
    d={k:(a.get(k),b.get(k)) for k in KEYS if a.get(k)!=b.get(k)}
    byapp.setdefault(app,[0,0])
    if d: diff+=1; byapp[app][1]+=1; rows.append((f.parent.parent.name,app,d))
    else: same+=1; byapp[app][0]+=1
print(f"  identical: {same}   changed: {diff}   errored: {err}")
print("  per app (unchanged/changed): "+"  ".join(f"{k}={v[0]}/{v[1]}" for k,v in sorted(byapp.items())))
for n,a,d in rows[:15]: print(f"   CHANGED {a:9s} {n[:50]}  {d}")
