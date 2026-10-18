import json, pathlib, sys, collections
H_ = pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0, str(H_/"experiments/pramana")); sys.path.insert(0, str(H_))
import pramana_helpers as H
base = H_/"experiments/pramana/results/pramana_runs"
for bw in (3,6,10):
    d = sorted(base.glob("zoom+vimeo_%dmbps_50ms_0pct_pfifo_cubic_concurrent2_t1_*"%bw),
               key=lambda p: p.stat().st_mtime)[-1]
    at = H.attribute_capture(d/"capture.pcap", ["zoom","vimeo"], float(bw))
    # combined peak from the SUMMED series, aligned on a 1s grid
    grid = collections.defaultdict(float)
    for app in ("zoom","vimeo"):
        t = at.traffic(app,"download")
        if not t: continue
        for ts, mb in zip(t.times, t.mbps):
            grid[round(ts)] += mb
    ser = sorted(grid.items())
    peak = max(v for _,v in ser) if ser else 0
    p95  = sorted(v for _,v in ser)[int(len(ser)*0.95)] if ser else 0
    print("== %2dM  combined: peak %.2f  p95 %.2f  cap %d  (samples %d)"%(bw,peak,p95,bw,len(ser)))
    over = [(t,round(v,2)) for t,v in ser if v > bw*1.15]
    if over: print("   over-cap instants: %s%s"%(over[:6], " ..." if len(over)>6 else ""))

    # zoom dropped-frame provenance from raw jsonl
    rows=[json.loads(l) for l in open(d/"qoe"/"zoom_stats.jsonl") if l.strip()]
    st=[r.get("stats",r) for r in rows if r.get("record")!="meta"]
    tf=[s.get("total_video_frames") for s in st]; df=[s.get("dropped_video_frames") for s in st]
    print("   zoom total_frames first5=%s last3=%s"%(tf[:5], tf[-3:]))
    print("   zoom dropped_frames first5=%s last3=%s"%(df[:5], df[-3:]))
    dec=[(a or 0)-(b or 0) for a,b in zip(tf[1:],tf[:-1])]
    ddec=[(a or 0)-(b or 0) for a,b in zip(df[1:],df[:-1])]
    print("   zoom per-sample NEW total=%s"%(collections.Counter(dec).most_common(5),))
    print("   zoom per-sample NEW dropped=%s"%(collections.Counter(ddec).most_common(5),))
