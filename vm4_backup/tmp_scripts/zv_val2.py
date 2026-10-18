import json, pathlib, sys, collections
H_ = pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0, str(H_/"experiments/pramana")); sys.path.insert(0, str(H_))
import pramana_helpers as H
base = H_/"experiments/pramana/results/pramana_runs"
ROWS=[]

def one(bw, lat):
    c = sorted(base.glob("zoom+vimeo_%dmbps_%dms_0pct_pfifo_cubic_concurrent2_t1_*"%(bw,lat)),
               key=lambda p: p.stat().st_mtime)
    if not c: return None
    d=c[-1]; rec=json.load(open(d/"record.json"))
    at=H.attribute_capture(d/"capture.pcap", ["zoom","vimeo"], float(bw))
    fails=[]; warns=[]; apps={}
    # --- shared cap on COMBINED traffic, from the summed 1s series ---
    grid=collections.defaultdict(float)
    for a in ("zoom","vimeo"):
        t=at.traffic(a,"download")
        if t:
            for ts,mb in zip(t.times,t.mbps): grid[round(ts)]+=mb
    ser=sorted(grid.values()); cpeak=max(ser) if ser else 0.0
    if cpeak > bw*1.15: fails.append("combined peak %.2f > cap %d"%(cpeak,bw))

    for app in ("zoom","vimeo"):
        q=(rec.get("player_qoe") or {}).get(app) or {}
        st=[json.loads(l).get("stats",{}) for l in open(d/"qoe"/f"{app}_stats.jsonl")
            if l.strip() and '"meta"' not in l]
        st=[s for s in st if s]
        f=[]
        # L2 vs L1: pcap bytes vs record bytes
        dl=((rec.get("per_app_stats") or {}).get(app) or {}).get("download") or {}
        t=at.traffic(app,"download"); l3="n/a"
        if t:
            drift=abs((t.total_mb or 0)-(dl.get("total_mb") or 0))
            l3="%.2f MB (drift %.3f)"%(t.total_mb,drift)
            if drift>0.05: f.append("pcap/record drift %.3f"%drift)
        # frame counter continuity
        tf=[s.get("total_video_frames") for s in st]
        resets=[i for i in range(1,len(tf)) if (tf[i] or 0)<(tf[i-1] or 0)]
        if resets: warns.append("%s counter reset @%s"%(app,resets[:3]))
        # layout box -> is dropped_frame_pct trustworthy?
        zero=sum(1 for s in st if not ((s.get("player_elem_w") or 0)*(s.get("player_elem_h") or 0)))
        frac=zero/len(st) if st else 1.0
        drop_ok = frac < 0.5
        if not drop_ok:
            warns.append("%s dropped_pct INVALID (0x0 element in %d/%d samples)"%(app,zero,len(st)))
        # zoom must not invent a bitrate
        if app=="zoom" and q.get("mean_bitrate_mbps") is not None:
            f.append("zoom bitrate invented")
        # capture must cover the whole call
        sess=q.get("session_start_epoch")
        shift=(sess-at.t0_epoch) if sess and at.t0_epoch else None
        if shift is None or shift<-1 or (shift+(q.get("session_seconds") or 0))>at.duration_s+2:
            f.append("capture did not cover call")
        res=collections.Counter(s.get("resolution") for s in st
                                if s.get("resolution") and s.get("resolution")!="0x0")
        apps[app]=dict(res=q.get("video_resolution_p"),fps=q.get("frame_rate_fps"),
            drop=q.get("dropped_frame_pct"),drop_ok=drop_ok,reb=q.get("rebuffer_events"),
            w=q.get("watched_seconds"),br=q.get("mean_bitrate_mbps"),ladder=res.most_common(3),
            l3=l3,peak=dl.get("peak_throughput_mbps"),avg=dl.get("avg_throughput_mbps"),fails=f)
        fails+=["%s: %s"%(app,x) for x in f]
    return dict(dir=d.name,bw=bw,lat=lat,apps=apps,fails=fails,warns=warns,cpeak=cpeak,
                pngs=sorted(p.name for p in d.glob("*.png")),
                pcaps=sorted(p.name for p in d.glob("*.pcap")))

for lat,label in ((50,"B1"),(150,"B2")):
    for bw in (3,6,10):
        r=one(bw,lat)
        if not r: print("%s %2dM/%3dms : NO RUN"%(label,bw,lat)); continue
        cls="VERIFIED" if not r["fails"] else "PIPELINE-BUG"
        if not r["fails"] and r["warns"]: cls="VERIFIED (metric caveat)"
        print("\n### %s %2dM/%3dms -> %s   [%s]"%(label,r["bw"],r["lat"],cls,r["dir"][-10:]))
        print("    combined peak %.2f / cap %d   pcaps=%d  charts=%d"%(
            r["cpeak"],r["bw"],len(r["pcaps"]),len(r["pngs"])))
        for a,v in r["apps"].items():
            dtxt = ("%.2f%%"%v["drop"]) if v["drop"] is not None else "None"
            if not v["drop_ok"]: dtxt+=" (INVALID)"
            print("    %-6s res=%-5s fps=%-6s drop=%-18s reb=%-3s watched=%-7s br=%-5s"%(
                a,v["res"],v["fps"],dtxt,v["reb"],v["w"],v["br"]))
            print("           peak/avg %.2f/%.2f Mbps | pcap %s | ladder %s"%(
                v["peak"] or 0,v["avg"] or 0,v["l3"],v["ladder"]))
        if r["fails"]: print("    FAILS: %s"%r["fails"])
        if r["warns"]: print("    WARN : %s"%r["warns"])
        ROWS.append((label,r))
