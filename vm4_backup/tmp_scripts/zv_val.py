import json, pathlib, sys, collections
H_ = pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0, str(H_/"experiments/pramana")); sys.path.insert(0, str(H_))
import pramana_helpers as H
base = H_/"experiments/pramana/results/pramana_runs"

def check(bw, lat):
    cands = sorted(base.glob("zoom+vimeo_%dmbps_%dms_0pct_pfifo_cubic_concurrent2_t1_*" % (bw,lat)),
                   key=lambda p: p.stat().st_mtime)
    if not cands: return None
    d = cands[-1]
    rec = json.load(open(d/"record.json"))
    at = H.attribute_capture(d/"capture.pcap", ["zoom","vimeo"], float(bw))
    out = {"dir": d.name, "bw": bw, "lat": lat, "apps": {}, "fails": [], "notes": []}
    for app in ("zoom","vimeo"):
        q = (rec.get("player_qoe") or {}).get(app) or {}
        f = []
        rows=[json.loads(l) for l in open(d/"qoe"/f"{app}_stats.jsonl") if l.strip()]
        rows=[r for r in rows if r.get("record")!="meta"]
        st=[r.get("stats",r) for r in rows]
        tf=[s.get("total_video_frames") for s in st]
        resets=[i for i in range(1,len(tf)) if (tf[i] or 0) < (tf[i-1] or 0)]
        if resets: f.append("counter reset @%s" % resets[:4])
        live={k:v for k,v in collections.Counter(s.get("resolution") for s in st).items() if k and k!="0x0"}
        if app=="zoom" and len(live)>1: out["notes"].append("zoom tiles %s" % live)
        dl=((rec.get("per_app_stats") or {}).get(app) or {}).get("download") or {}
        t=at.traffic(app,"download")
        l3="n/a"
        if t:
            drift=abs((t.total_mb or 0)-(dl.get("total_mb") or 0))
            l3="pcap %.2f vs rec %.2f (drift %.3f)"%(t.total_mb, dl.get("total_mb") or 0, drift)
            if drift>0.05: f.append("pcap drift %.3f"%drift)
        if app=="zoom" and q.get("mean_bitrate_mbps") is not None: f.append("zoom bitrate invented")
        sess=q.get("session_start_epoch"); shift=(sess-at.t0_epoch) if sess and at.t0_epoch else None
        if shift is None or (shift+(q.get("session_seconds") or 0)) > at.duration_s+2:
            f.append("capture did not cover call")
        out["apps"][app]=dict(res=q.get("video_resolution_p"),fps=q.get("frame_rate_fps"),
            drop=q.get("dropped_frame_pct"),reb=q.get("rebuffer_events"),w=q.get("watched_seconds"),
            br=q.get("mean_bitrate_mbps"),status=q.get("status"),tiles=live,l3=l3,fails=f,
            peak=dl.get("peak_throughput_mbps"),avg=dl.get("avg_throughput_mbps"))
        out["fails"] += ["%s: %s"%(app,x) for x in f]
    # shared cap: COMBINED traffic must respect the cap
    tot_peak = sum((at.traffic(a,"download").peak_throughput_mbps or 0) for a in ("zoom","vimeo") if at.traffic(a,"download"))
    out["combined_peak"] = tot_peak
    if tot_peak > bw*1.15: out["fails"].append("combined peak %.2f > cap %d" % (tot_peak, bw))
    out["charts"]=sorted(p.name for p in d.glob("*.png"))
    out["pcaps"]=sorted(p.name for p in d.glob("*.pcap"))
    return out

if __name__ == "__main__":
    for bw,lat in json.loads(sys.argv[1]):
        r=check(bw,lat)
        if r is None: print("  %dM/%dms: no run"%(bw,lat)); continue
        cls = "VERIFIED" if not r["fails"] else "PIPELINE-BUG"
        print("### %2dM/%3dms -> %s   (%s)"%(r["bw"],r["lat"],cls,r["dir"][-12:]))
        for app,a in r["apps"].items():
            print("    %-6s res=%-5s fps=%-7s drop=%-8s reb=%-3s watched=%-7s br=%-7s tiles=%s"%(
                app,a["res"],a["fps"],a["drop"],a["reb"],a["w"],a["br"],a["tiles"]))
            print("           peak/avg=%.2f/%.2f  L3: %s"%(a["peak"] or 0,a["avg"] or 0,a["l3"]))
        print("    combined peak %.2f Mbps vs cap %d | pcaps=%s | charts=%d"%(
            r["combined_peak"], r["bw"], r["pcaps"], len(r["charts"])))
        if r["fails"]: print("    FAILS: %s"%r["fails"])
        if r["notes"]: print("    notes: %s"%r["notes"])
