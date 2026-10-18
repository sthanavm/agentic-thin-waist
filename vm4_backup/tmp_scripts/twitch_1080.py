import json, glob, os, datetime
from collections import Counter
R="/home/student/sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
d=[x for x in glob.glob(R+"/tubi+twitch_100mbps*") if os.path.isdir(x)]
for run in d:
    rec=json.load(open(os.path.join(run,"record.json")))
    t=datetime.datetime.fromtimestamp(rec["timestamp"],datetime.UTC).strftime('%m-%d %H:%M')
    f=os.path.join(run,"qoe","twitch_stats.jsonl")
    if not os.path.exists(f): continue
    smp=[json.loads(l) for l in open(f) if l.strip() and json.loads(l).get("record")!="meta"]
    hs=[(s.get("stats") or {}).get("video_height") for s in smp]
    hs=[h for h in hs if h]
    c=Counter(hs)
    print(f"\n  {os.path.basename(run)[-10:]}  {t}  ({len(smp)} samples)")
    print(f"    height histogram: {dict(sorted(c.items()))}")
    print(f"    sequence (first 12): {hs[:12]}")
    print(f"    sequence (last 8)  : {hs[-8:]}")
    pq=(rec.get("player_qoe") or {}).get("twitch") or {}
    print(f"    record says: {pq.get('video_resolution_p')}p  observed={pq.get('resolutions_observed')}")
