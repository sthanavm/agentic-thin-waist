import json, pathlib, collections, sys
H_=pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0,str(H_/"experiments/pramana"))
import pramana_helpers as H
d=H_/"experiments/pramana/results/pramana_runs/meet_10mbps_50ms_0pct_pfifo_cubic_solo_t1_eefe603aa4"
rows=[json.loads(l) for l in open(d/"qoe"/"meet_stats.jsonl") if l.strip()]
rows=[r for r in rows if r.get("record")!="meta"]
def st(r): return r.get("stats",r)
print("  inbound_video_stream_count:", dict(collections.Counter(st(r).get("inbound_video_stream_count") for r in rows)))
print("  peer_connection_count     :", dict(collections.Counter(st(r).get("peer_connection_count") for r in rows)))
print("  resolution (primary)      :", dict(collections.Counter(st(r).get("resolution") for r in rows)))
r=json.load(open(d/"record.json")); q=r["player_qoe"]["meet"]
at=H.attribute_capture(d/"capture.pcap",["meet"],10.0)
off=q["session_start_epoch"]-at.t0_epoch
print()
print("  call starts at capture t=%.1fs, ends t=%.1fs, capture last packet t=%.1fs" % (off, off+q["session_seconds"], at.duration_s))
tail=at.duration_s-(off+q["session_seconds"])
print("  margin after call end     : %+.1fs %s" % (tail, "(call fully captured)" if tail>=0 else "<-- STILL TRUNCATED"))
