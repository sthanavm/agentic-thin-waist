import json, pathlib
H_ = pathlib.Path.home()/"sthanav-agentic-thin-waist"
d = sorted((H_/"experiments/pramana/results/pramana_runs").glob("zoom_10mbps_50ms_*"),
           key=lambda p: p.stat().st_mtime)[-1]
rows=[json.loads(l) for l in open(d/"qoe"/"zoom_stats.jsonl") if l.strip()]
rows=[r for r in rows if r.get("record")!="meta"]
st=[r.get("stats",r) for r in rows]; ts=[r["timestamp"] for r in rows]
print("  idx  t(s)   resolution   total_frames  dropped  current_time")
for i,(s,t) in enumerate(zip(st,ts)):
    if i < 3 or i > len(st)-5:
        print("  %3d %6.1f  %-11s %12s %8s  %s" % (i, t-ts[0], s.get("resolution"),
              s.get("total_video_frames"), s.get("dropped_video_frames"), s.get("current_time_secs")))
    elif i == 3: print("   ...")
# where does the counter go backwards?
bad=[i for i in range(1,len(st)) if (st[i].get("total_video_frames") or 0) < (st[i-1].get("total_video_frames") or 0)]
print("\n  samples where total_video_frames DECREASED (element switch):", bad)
# recompute over the stable majority segment
good=[i for i,s in enumerate(st) if s.get("resolution")=="1280x720"]
if good:
    a,b=good[0],good[-1]
    # find the longest monotonic run inside
    seg=[a]
    for i in range(a+1,b+1):
        if (st[i].get("total_video_frames") or 0) >= (st[i-1].get("total_video_frames") or 0): seg.append(i)
        else: break
    i0,i1=seg[0],seg[-1]
    span=ts[i1]-ts[i0]
    fr=(st[i1].get("total_video_frames") or 0)-(st[i0].get("total_video_frames") or 0)
    dr=(st[i1].get("dropped_video_frames") or 0)-(st[i0].get("dropped_video_frames") or 0)
    print("  stable 1280x720 segment: samples %d..%d  (%.1fs)" % (i0,i1,span))
    print("    frames decoded : %d  -> %.2f fps" % (fr, fr/span if span else 0))
    print("    frames dropped : %d  -> %.3f%%" % (dr, 100*dr/max(1,fr+dr)))
