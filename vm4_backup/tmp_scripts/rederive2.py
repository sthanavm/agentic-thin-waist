import json, glob, os, sys
sys.path.insert(0,"/home/student/sthanav-agentic-thin-waist")
from shared import qoe as Q
R="/home/student/sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
d=[x for x in glob.glob(R+"/**/*47ee56f16f", recursive=True) if os.path.isdir(x)][0]
rec=json.load(open(os.path.join(d,"record.json")))
print(f"  run: {os.path.basename(d)}   (in ARCHIVE — quarantined)")
for app in rec["config"]["apps"]:
    f=os.path.join(d,"qoe",f"{app}_stats.jsonl")
    if not os.path.exists(f): continue
    rows=[json.loads(l) for l in open(f) if l.strip()]
    meta=[r for r in rows if r.get("record")=="meta"]
    ptt=(meta[0].get("play_trigger_ts") if meta else None)
    stored=(rec.get("player_qoe") or {}).get(app) or {}
    out=Q.summarize(f, app, play_trigger_ts=ptt)
    samples=[r for r in rows if r.get("record")!="meta"]
    cur=[(s.get('stats') or {}).get('current_time_secs') for s in samples]
    cur=[c for c in cur if c is not None]
    drops=sum(1 for i in range(1,len(cur)) if cur[i]<cur[i-1])
    naive=max(0.0, cur[-1]-cur[0]) if cur else 0
    print(f"\n  --- {app} ---")
    print(f"    STORED (old code)   watched={stored.get('watched_seconds')}  session={stored.get('session_seconds')}")
    print(f"    RE-DERIVED (today)  watched={out.get('watched_seconds')}  session={out.get('session_seconds')}")
    print(f"    media clock first={cur[0]:.2f} last={cur[-1]:.2f} min={min(cur):.2f} max={max(cur):.2f}")
    print(f"    backward jumps={drops}   last-minus-first would give {naive:.2f}")
    print(f"    frames={stored.get('total_video_frames')} res={stored.get('video_resolution_p')}")
