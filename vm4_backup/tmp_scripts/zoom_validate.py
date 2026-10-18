import json, pathlib, sys, collections
H_ = pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0, str(H_/"experiments/pramana")); sys.path.insert(0, str(H_))
import pramana_helpers as H
d = sorted((H_/"experiments/pramana/results/pramana_runs").glob("zoom_10mbps_50ms_*"),
           key=lambda p: p.stat().st_mtime)[-1]
print("### run:", d.name)
rec = json.load(open(d/"record.json")); q = rec["player_qoe"]["zoom"]
print("\n--- LAYER 1: record.json ---")
for k in ("status","video_resolution_p","resolutions_observed","frame_rate_fps","total_video_frames",
          "dropped_video_frames","dropped_frame_pct","rebuffer_events","rebuffer_duration_ms",
          "watched_seconds","session_seconds","total_samples","video_startup_time_ms"):
    print("   %-22s %s" % (k, q.get(k)))
print("   derivation:", json.dumps(q.get("derivation")))
print("\n--- LAYER 2: raw samples ---")
rows=[json.loads(l) for l in open(d/"qoe"/"zoom_stats.jsonl") if l.strip()]
rows=[r for r in rows if r.get("record")!="meta"]
st=[r.get("stats",r) for r in rows]; ts=[r["timestamp"] for r in rows]
res=collections.Counter(s.get("resolution") for s in st)
tf=[s.get("total_video_frames") for s in st]
dv=[s.get("dropped_video_frames") for s in st]
print("   resolutions:", dict(res))
print("   total_video_frames  first/last:", tf[0], tf[-1], "| None count:", sum(1 for v in tf if v is None))
print("   dropped_video_frames first/last:", dv[0], dv[-1])
adv=sum(1 for a,b in zip(tf,tf[1:]) if a is not None and b is not None and b>a)
print("   frames advanced in %d/%d intervals" % (adv, len(tf)-1))
if tf[0] is not None and tf[-1] is not None:
    span=ts[-1]-ts[0]
    print("   fps recomputed from raw: %.2f  (record says %s)" % ((tf[-1]-tf[0])/span, q.get("frame_rate_fps")))
ct=[s.get("current_time_secs") for s in st]
print("   current_time first/last:", ct[0], ct[-1])
print("\n--- LAYER 3: pcap ---")
p = d/"capture.pcap"
if p.is_file():
    at=H.attribute_capture(p, ["zoom"], 10.0)
    t=at.traffic("zoom","download")
    pas=((rec.get("per_app_stats") or {}).get("zoom") or {}).get("download") or {}
    if t:
        print("   pcap %.2f MB vs record %.2f MB (drift %.3f)" % (t.total_mb, pas.get("total_mb") or 0, abs(t.total_mb-(pas.get("total_mb") or 0))))
        print("   peak %.2f / avg %.2f Mbps (cap 10)" % (t.peak_throughput_mbps or 0, t.avg_throughput_mbps or 0))
    sess=q.get("session_start_epoch")
    if sess and at.t0_epoch:
        off=sess-at.t0_epoch
        print("   capture %.0fs | call starts %.0fs | covered: %s" % (at.duration_s, off, (off+(q.get("session_seconds") or 0)) <= at.duration_s+2))
else:
    print("   NO PCAP")
print("\n   plots:", sorted(x.name for x in d.glob("*.png")))
