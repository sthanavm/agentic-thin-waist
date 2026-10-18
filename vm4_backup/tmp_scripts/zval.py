import json, pathlib, sys, collections
H_ = pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0, str(H_/"experiments/pramana")); sys.path.insert(0, str(H_))
import pramana_helpers as H
from qoe_report import build_report
base = H_/"experiments/pramana/results/pramana_runs"

def check(bw, lat, make_report=True):
    cands = sorted(base.glob("zoom_%dmbps_%dms_0pct_pfifo_cubic_solo_t1_*" % (bw,lat)),
                   key=lambda p: p.stat().st_mtime)
    d = None
    for c in reversed(cands):
        try:
            r = json.load(open(c/"record.json"))
            if (r.get("player_qoe",{}).get("zoom",{}) or {}).get("status")=="ok": d=c; break
        except Exception: pass
    if d is None: return None
    rec = json.load(open(d/"record.json")); q = rec["player_qoe"]["zoom"]
    fails, notes = [], []
    rows=[json.loads(l) for l in open(d/"qoe"/"zoom_stats.jsonl") if l.strip()]
    rows=[r for r in rows if r.get("record")!="meta"]
    st=[r.get("stats",r) for r in rows]; ts=[r["timestamp"] for r in rows]
    # --- tile stability: counter must never go backwards ---
    tf=[s.get("total_video_frames") for s in st]
    resets=[i for i in range(1,len(tf)) if (tf[i] or 0) < (tf[i-1] or 0)]
    if resets: fails.append("frame counter reset at samples %s (element switch)" % resets[:6])
    res=collections.Counter(s.get("resolution") for s in st)
    live={k:v for k,v in res.items() if k and k!="0x0"}
    if len(live) > 1: notes.append("multiple resolutions %s" % dict(live))
    adv=sum(1 for a,b in zip(tf,tf[1:]) if a is not None and b is not None and b>a)
    if adv < len(tf)*0.5: fails.append("frames advanced only %d/%d" % (adv,len(tf)-1))
    # --- L1 ---
    dl=((rec.get("per_app_stats") or {}).get("zoom") or {}).get("download") or {}
    pk,av=dl.get("peak_throughput_mbps"),dl.get("avg_throughput_mbps")
    if pk and pk>bw*1.05: fails.append("peak %.2f>cap"%pk)
    if av and av>bw*1.05: fails.append("avg %.2f>cap"%av)
    if q.get("mean_bitrate_mbps") is not None: fails.append("bitrate invented (should be null)")
    # --- L3 ---
    at=H.attribute_capture(d/"capture.pcap", ["zoom"], float(bw))
    t=at.traffic("zoom","download"); l3="no traffic"
    if t:
        drift=abs((t.total_mb or 0)-(dl.get("total_mb") or 0))
        l3="pcap %.2fMB vs rec %.2fMB drift %.3f"%(t.total_mb, dl.get("total_mb") or 0, drift)
        if drift>0.05: fails.append("pcap/record drift %.3f"%drift)
    sess=q.get("session_start_epoch"); shift=(sess-at.t0_epoch) if sess and at.t0_epoch else None
    covered = shift is not None and (shift+(q.get("session_seconds") or 0)) <= at.duration_s+2
    if not covered: fails.append("capture did not cover the call")
    if make_report:
        try: build_report(d)
        except Exception as e: fails.append("report failed %s"%str(e)[:40])
    charts=len(list((d/"report").glob("*.png"))) if (d/"report").is_dir() else 0
    return dict(bw=bw,lat=lat,dir=d.name,fails=fails,notes=notes,charts=charts,
        res=q.get("video_resolution_p"),fps=q.get("frame_rate_fps"),drop=q.get("dropped_frame_pct"),
        reb=q.get("rebuffer_events"),w=q.get("watched_seconds"),n=q.get("total_samples"),
        br=q.get("mean_bitrate_mbps"),pk=pk,av=av,l3=l3,shift=shift,capdur=at.duration_s,
        resets=resets,live=dict(live))

if __name__ == "__main__":
    combos=json.loads(sys.argv[1])
    out=[]
    for bw,lat in combos:
        r=check(bw,lat)
        if r is None: print("  %dM/%dms: no ok run"%(bw,lat)); continue
        cls="VERIFIED" if not r["fails"] else "PIPELINE-BUG"
        print("### %2dM/%3dms -> %s"%(r["bw"],r["lat"],cls))
        print("    res=%sp fps=%s drop=%s%% rebuf=%s watched=%ss samples=%s bitrate=%s"%(
            r["res"],r["fps"],r["drop"],r["reb"],r["w"],r["n"],r["br"]))
        print("    tiles seen=%s | counter resets=%s | shaping peak/avg=%.2f/%.2f (cap %d)"%(
            r["live"], r["resets"] or "none", r["pk"] or 0, r["av"] or 0, r["bw"]))
        print("    L3: %s | capture %.0fs, call starts %.0fs, covered=%s | charts=%d"%(
            r["l3"], r["capdur"], r["shift"] or -1, "yes" if not any("cover" in f for f in r["fails"]) else "NO", r["charts"]))
        if r["fails"]: print("    FAILS: %s"%r["fails"])
        if r["notes"]: print("    notes: %s"%r["notes"])
        out.append(r)
    print("DIRS", json.dumps({"%dM_%dms"%(r["bw"],r["lat"]): r["dir"] for r in out}))
