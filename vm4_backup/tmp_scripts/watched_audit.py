"""Does any run still show watched=0 (or implausible) while demonstrably playing?"""
import json, glob, os, datetime
R="/home/student/sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
bug=[]; impl=[]; total=0; checked=0
for p in glob.glob(R+"/**/record.json", recursive=True):
    j=json.load(open(p)); c=j.get("config") or {}
    for a in c.get("apps") or []:
        pq=(j.get("player_qoe") or {}).get(a) or {}
        if not pq.get("player_qoe_available"): continue
        total+=1
        w=pq.get("watched_seconds"); s=pq.get("session_seconds")
        frames=pq.get("total_video_frames"); res=pq.get("video_resolution_p")
        played = bool(frames and frames>0) or bool(res)
        if played and (w is None or w==0):
            bug.append((os.path.basename(os.path.dirname(p)), a, w, s, frames, res, j["timestamp"]))
        if w is not None and s and w > s*1.20:
            impl.append((os.path.basename(os.path.dirname(p)), a, w, s))
        checked+=1
print(f"  app-instances with player data: {total}")
print(f"  watched==0 (or None) while frames/resolution present: {len(bug)}")
for b in bug[:12]:
    t=datetime.datetime.fromtimestamp(b[6],datetime.UTC).strftime('%m-%d %H:%M')
    print(f"    {t}  {b[0][-10:]}  {b[1]}  watched={b[2]} session={b[3]} frames={b[4]} res={b[5]}")
print(f"  watched > session*1.2 (impossible): {len(impl)}")
for b in impl[:8]: print(f"    {b[0][-10:]} {b[1]} watched={b[2]} session={b[3]}")
