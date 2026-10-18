import json, pathlib, sys, collections
H_ = pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0, str(H_/"experiments/pramana")); sys.path.insert(0, str(H_))
import pramana_helpers as H
from qoe_report import build_report

base = H_/"experiments/pramana/results/pramana_runs"
WANT = [(3,50),(3,150),(6,50),(6,150),(10,50)]
out = []
for bw, lat in WANT:
    cands = sorted(base.glob("meet_%dmbps_%dms_0pct_pfifo_cubic_solo_t1_*" % (bw,lat)),
                   key=lambda p: p.stat().st_mtime)
    d = None
    for c in reversed(cands):
        try:
            r = json.load(open(c/"record.json"))
            if (r.get("player_qoe",{}).get("meet",{}) or {}).get("status") == "ok":
                d = c; break
        except Exception: pass
    if d is None:
        print("  %dM/%dms: no ok run found" % (bw,lat)); continue
    rec = json.load(open(d/"record.json")); q = rec["player_qoe"]["meet"]
    fails = []
    # L1 shaping + consistency
    dl = (rec.get("per_app_stats") or {}).get("meet",{}).get("download",{}) or {}
    pk, av = dl.get("peak_throughput_mbps"), dl.get("avg_throughput_mbps")
    if pk and pk > bw*1.05: fails.append("peak %.2f>cap" % pk)
    if av and av > bw*1.05: fails.append("avg %.2f>cap" % av)
    if bool(rec.get("player_qoe_available")) != bool((rec.get("per_app",{}).get("meet",{}) or {}).get("player_qoe_available")):
        fails.append("player_qoe_available mismatch")
    if q.get("mean_bitrate_mbps") is not None and "unavailable" in str((q.get("derivation") or {}).get("bitrate_basis","")):
        fails.append("bitrate vs basis contradiction")
    # L2 raw samples
    rows=[json.loads(l) for l in open(d/"qoe"/"meet_stats.jsonl") if l.strip()]
    rows=[r for r in rows if r.get("record")!="meta"]
    st=[r.get("stats",r) for r in rows]
    fd=[s.get("frames_decoded") for s in st if s.get("frames_decoded") is not None]
    adv=sum(1 for a,b in zip(fd,fd[1:]) if b>a)
    if adv < len(fd)*0.5: fails.append("frames advanced %d/%d" % (adv,len(fd)-1))
    streams=set(s.get("inbound_video_stream_count") for s in st)
    # L3 pcap
    at = H.attribute_capture(d/"capture.pcap", ["meet"], float(bw))
    t = at.traffic("meet","download")
    l3 = "no traffic"
    if t:
        drift = abs((t.total_mb or 0) - (dl.get("total_mb") or 0))
        l3 = "pcap %.2fMB vs rec %.2fMB drift %.3f" % (t.total_mb, dl.get("total_mb") or 0, drift)
        if drift > 0.05: fails.append("pcap/record drift %.3f" % drift)
        if (t.peak_throughput_mbps or 0) > bw*1.05: fails.append("pcap peak>cap")
    # window coverage: did the capture actually cover the call?
    sess = q.get("session_start_epoch"); shift = (sess - at.t0_epoch) if sess and at.t0_epoch else None
    covered = shift is not None and (shift + (q.get("session_seconds") or 0)) <= at.duration_s + 2
    if not covered: fails.append("capture did NOT cover the call")
    try: build_report(d)
    except Exception as e: fails.append("report failed: %s" % str(e)[:40])
    plots = sorted(p.name for p in (d/"report").glob("*.png"))
    out.append(dict(bw=bw, lat=lat, dir=d.name, fails=fails, plots=len(plots),
        res=q.get("video_resolution_p"), fps=q.get("frame_rate_fps"), br=q.get("mean_bitrate_mbps"),
        drop=q.get("dropped_frame_pct"), reb=q.get("rebuffer_events"), jit=q.get("mean_jitter_secs"),
        loss=q.get("packet_loss_pct"), w=q.get("watched_seconds"), n=q.get("total_samples"),
        pk=pk, av=av, l3=l3, streams=streams, shift=shift, capdur=at.duration_s))
for o in out:
    cls = "VERIFIED" if not o["fails"] else "PIPELINE-BUG"
    print("### %2dM/%3dms -> %s" % (o["bw"], o["lat"], cls))
    print("    res=%sp fps=%s bitrate=%s drop=%s%% rebuf=%s jitter=%ss loss=%s%%" % (
        o["res"],o["fps"],o["br"],o["drop"],o["reb"],o["jit"],o["loss"]))
    print("    watched=%ss samples=%s | shaping peak/avg=%.2f/%.2f (cap %d)" % (o["w"],o["n"],o["pk"] or 0,o["av"] or 0,o["bw"]))
    print("    L3: %s | capture %.0fs, call starts %.0fs, covered=%s | streams=%s | charts=%d" % (
        o["l3"], o["capdur"], o["shift"] or -1, "yes" if not any("cover" in f for f in o["fails"]) else "NO", o["streams"], o["plots"]))
    if o["fails"]: print("    FAILS: %s" % o["fails"])
print("DIRS", json.dumps({"%dM_%dms"%(o["bw"],o["lat"]): o["dir"] for o in out}))
