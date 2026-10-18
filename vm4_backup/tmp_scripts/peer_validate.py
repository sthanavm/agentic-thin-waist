import json, pathlib, sys, collections
H_=pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0,str(H_/"experiments/pramana")); sys.path.insert(0,str(H_))
import pramana_helpers as H
d=sorted((H_/"experiments/pramana/results/pramana_runs").glob("webrtc_peer_*"))[-1]
r=json.load(open(d/"record.json")); app="webrtc_peer"; q=r["player_qoe"][app]
print("### LAYER 1 - record.json")
for k in ("status","video_resolution_p","frame_rate_fps","total_video_frames","dropped_video_frames",
          "dropped_frame_pct","rebuffer_events","rebuffer_duration_ms","watched_seconds",
          "session_seconds","mean_bitrate_mbps","video_startup_time_ms","total_samples"):
    print("   %-22s %s" % (k, q.get(k)))
print("   derivation:", json.dumps(q.get("derivation")))
print("   verdict   :", r.get("per_app",{}).get(app,{}).get("traffic_verdict"))
print("   buckets   :", r.get("capture_buckets_mb"), "| adopted:", r.get("limitations",{}).get("adopted_unnamed_ips"))

print("\n### LAYER 2 - raw samples")
rows=[json.loads(l) for l in open(d/"qoe"/f"{app}_stats.jsonl") if l.strip()]
rows=[x for x in rows if x.get("record")!="meta"]
st=[x.get("stats",x) for x in rows]
res=collections.Counter(s.get("resolution") for s in st)
tf=[s.get("total_video_frames") for s in st if s.get("total_video_frames") is not None]
ct=[s.get("current_time_secs") for s in st if s.get("current_time_secs") is not None]
adv=sum(1 for a,b in zip(tf,tf[1:]) if b>a)
print("   samples:", len(st), "| resolutions:", dict(res))
print("   frames %s -> %s (advanced in %d/%d intervals)" % (tf[0], tf[-1], adv, len(tf)-1))
print("   current_time %.2f -> %.2f" % (ct[0], ct[-1]))
print("   standardized blocks:", all(k in st[len(st)//2] for k in ("video","playback","frames")))
print("   video block:", json.dumps(st[len(st)//2].get("video")))
fps_check=(tf[-1]-tf[0])/(rows[-1]["timestamp"]-rows[0]["timestamp"])
print("   fps recomputed from raw: %.2f  (record says %s)" % (fps_check, q.get("frame_rate_fps")))

print("\n### LAYER 3 - pcap")
at=H.attribute_capture(d/"capture.pcap",[app],10.0)
for dirn in ("download","upload"):
    t=at.traffic(app,dirn)
    if t: print("   %-8s total=%.2f MB avg=%.3f peak=%.3f idle=%.0fs delivered=%.3f cls=%s" % (
        dirn,t.total_mb,t.avg_throughput_mbps,t.peak_throughput_mbps,t.stall_seconds,t.delivered_fraction,t.classification))
sess=q.get("session_start_epoch"); off=(sess-at.t0_epoch) if sess and at.t0_epoch else None
print("   capture %.1fs | call starts at t=%.1fs | adopted=%s" % (at.duration_s, off or -1, at.adopted_ips))
dl=at.traffic(app,"download")
if dl and off:
    incall=[v for i,v in enumerate(dl.mbps) if i>=off]
    print("   in-call mean download: %.3f Mbps over %d bins" % (sum(incall)/max(len(incall),1), len(incall)))
print("\n   plots present:", sorted(p.name for p in d.glob("*.png")) or "NONE")
