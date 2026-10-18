"""Did the shared/qoe.py change disturb the html5 (youtube/vimeo/...) path?"""
import json, glob, os, sys
sys.path.insert(0,"/home/student/sthanav-agentic-thin-waist")
from shared import qoe as Q
R="/home/student/sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
runs=[]
for p in glob.glob(R+"/*/record.json"):
    j=json.load(open(p)); c=j.get("config") or {}
    if c.get("tag") in ("racefix","winfix","baseline","sweep_bw","sweep_lat","tt_ladder"):
        runs.append((j["timestamp"], os.path.dirname(p), j))
runs.sort(); runs=runs[-12:]
same=diff=0; checked=[]
FIELDS=("watched_seconds","video_resolution_p","mean_bitrate_mbps","rebuffer_events",
        "video_startup_time_ms","dropped_frame_pct","total_video_frames","status")
for ts,d,j in runs:
    for app in j["config"]["apps"]:
        f=os.path.join(d,"qoe",f"{app}_stats.jsonl")
        if not os.path.exists(f): continue
        rows=[json.loads(l) for l in open(f) if l.strip()]
        meta=[r for r in rows if r.get("record")=="meta"]
        ptt=meta[0].get("play_trigger_ts") if meta else None
        stored=(j.get("player_qoe") or {}).get(app) or {}
        out=Q.summarize(f,app,play_trigger_ts=ptt)
        mism=[k for k in FIELDS if stored.get(k)!=out.get(k)]
        checked.append((os.path.basename(d)[-10:],app))
        if mism:
            diff+=1
            print(f"  CHANGED {os.path.basename(d)[-10:]} {app}: " +
                  ", ".join(f"{k}: {stored.get(k)!r}->{out.get(k)!r}" for k in mism))
        else: same+=1
print(f"\n  html5 app-instances re-derived: {len(checked)}   identical: {same}   changed: {diff}")
