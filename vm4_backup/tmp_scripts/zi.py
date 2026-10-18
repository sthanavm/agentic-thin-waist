import json,sys
rows=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
rows=[r for r in rows if isinstance(r.get("stats"),dict)]
s=[r["stats"] for r in rows]
print("stats keys:", sorted(s[5].keys()))
t0=rows[0]["timestamp"]
print("\n idx  wall  cur_time  frames  dropped  paused   WxH")
for i,(r,x) in enumerate(zip(rows,s)):
    if i%5 and i < len(s)-2: continue
    print("%4d %6.1f %9s %7s %8s %6s   %sx%s" % (
        i, r["timestamp"]-t0, x.get("current_time_secs"), x.get("total_video_frames"),
        x.get("dropped_video_frames"), x.get("paused"), x.get("video_width"), x.get("video_height")))
adv=sum(1 for a,b in zip(s,s[1:]) if (b.get("total_video_frames") or 0)>(a.get("total_video_frames") or 0))
ct =sum(1 for a,b in zip(s,s[1:]) if (b.get("current_time_secs") or 0)>(a.get("current_time_secs") or 0))
print("\n intervals where FRAMES advanced      : %d/%d" % (adv,len(s)-1))
print(" intervals where current_time advanced: %d/%d" % (ct,len(s)-1))
