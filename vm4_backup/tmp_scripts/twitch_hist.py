import json, glob, os, datetime
R="/home/student/sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
rows=[]
for p in glob.glob(R+"/**/record.json", recursive=True):
    j=json.load(open(p)); c=j.get("config") or {}
    if "twitch" not in (c.get("apps") or []): continue
    pq=(j.get("player_qoe") or {}).get("twitch") or {}
    if not pq.get("video_resolution_p"): continue
    rows.append((c["bandwidth_mbps"], pq["video_resolution_p"], pq.get("resolutions_observed"),
                 "+".join(c["apps"]), j["timestamp"]))
rows.sort()
from collections import defaultdict
by=defaultdict(list)
for bw,res,obs,apps,ts in rows: by[bw].append(res)
print(f"  {'cap':>6}  resolutions seen across all twitch runs")
for bw in sorted(by): print(f"  {bw:>5g}M  {sorted(set(by[bw]))}   (n={len(by[bw])})")
hi=[r for r in rows if r[1]>720]
print(f"\n  runs above 720p: {len(hi)}")
for bw,res,obs,apps,ts in hi:
    t=datetime.datetime.fromtimestamp(ts,datetime.UTC).strftime('%m-%d %H:%M')
    print(f"    {t}  {apps:<16}{bw:>5g}M -> {res}p  observed={obs}")
