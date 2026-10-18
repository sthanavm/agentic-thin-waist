import sys, json, pathlib
H_=pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0,str(H_))
from shared import qoe as Q
base=H_/"experiments/pramana/results/pramana_runs"
KEYS=("status","video_resolution_p","frame_rate_fps","dropped_video_frames","watched_seconds",
      "rebuffer_events","rebuffer_duration_ms","video_startup_time_ms","mean_bitrate_mbps")
same=diff=0; byapp={}
for rj in sorted(base.glob("*/record.json")):
    try: rec=json.load(open(rj))
    except Exception: continue
    for app,old in (rec.get("player_qoe") or {}).items():
        f=rj.parent/"qoe"/f"{app}_stats.jsonl"
        if not f.exists() or not isinstance(old,dict) or old.get("status")!="ok": continue
        try: new=Q.summarize(f,app)
        except Exception: continue
        d={k:(old.get(k),new.get(k)) for k in KEYS if old.get(k)!=new.get(k)}
        byapp.setdefault(app,[0,0])
        if d: diff+=1; byapp[app][1]+=1
        else: same+=1; byapp[app][0]+=1
print(f"  unchanged: {same}   changed: {diff}")
print("  per app (unchanged/changed): "+"  ".join(f"{k}={v[0]}/{v[1]}" for k,v in sorted(byapp.items())))
