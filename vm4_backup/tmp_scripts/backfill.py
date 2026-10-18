import json,sys,pathlib
H=pathlib.Path.home()/"sthanav-agentic-thin-waist"; sys.path.insert(0,str(H))
from shared import qoe as Q
d=pathlib.Path(sys.argv[1]); app="zoom"
rec=json.load(open(d/"record.json")); old=rec["player_qoe"][app]
new=Q.summarize(d/"qoe"/f"{app}_stats.jsonl", app)
# faithfulness check: everything except the rebuffer fields must be reproduced
drift={k:(old.get(k),new.get(k)) for k in old
       if k not in ("rebuffer_events","rebuffer_duration_ms","stall_duration_ms","rebuffer_spans","derivation","series")
       and old.get(k)!=new.get(k)}
print("  non-rebuffer drift vs original record:", drift if drift else "NONE (faithful re-derivation)")
if drift: sys.exit("aborting: re-derivation not faithful")
rec["player_qoe"][app]=new
if app in rec.get("per_app",{}): rec["per_app"][app]["qoe"]=new
json.dump(rec, open(d/"record.json","w"), indent=1)
print("  record.json updated\n")
for k in ("status","video_startup_time_ms","video_resolution_p","resolutions_observed","resolution_changes",
          "frame_rate_fps","total_video_frames","dropped_video_frames","dropped_frame_pct",
          "rebuffer_events","rebuffer_duration_ms","watched_seconds","session_seconds","total_samples","is_live"):
    print("   %-22s %s" % (k,new.get(k)))
for k,v in (new.get("derivation") or {}).items(): print("   derivation.%-11s %s" % (k,v))
