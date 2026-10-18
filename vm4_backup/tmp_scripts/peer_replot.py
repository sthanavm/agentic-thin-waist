import json, pathlib, sys, dataclasses
H_=pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0,str(H_/"experiments/pramana")); sys.path.insert(0,str(H_))
import pramana_helpers as H
d=sorted((H_/"experiments/pramana/results/pramana_runs").glob("webrtc_peer_*"))[-1]
rec=json.load(open(d/"record.json"))
flds={f.name for f in dataclasses.fields(H.ExperimentConfig)}
cfg=H.ExperimentConfig(**{k:v for k,v in rec["config"].items() if k in flds})
at=H.attribute_capture(d/"capture.pcap", cfg.apps, float(cfg.bandwidth_mbps),
                       app_endpoint_ips=H.app_endpoint_ips_from_urls(cfg))
rec["capture_buckets_mb"]=at.bucket_mb
rec["per_app_stats"]={a:{dn:at.traffic(a,dn).as_record() for dn in ("download","upload") if at.traffic(a,dn)} for a in cfg.apps}
rec.setdefault("limitations",{})["attributed_byte_fraction"]=at.attributed_fraction
json.dump(rec, open(d/"record.json","w"), indent=1)
plots=H.generate_run_plots(at,cfg,d,rec.get("player_qoe"))
print("  buckets:", at.bucket_mb)
print("  plots  :", sorted(pathlib.Path(v).name for v in plots.values()))
for dn in ("download","upload"):
    t=at.traffic("webrtc_peer",dn)
    if t and t.avg_throughput_mbps is not None:
        print("  %-8s total=%.2f MB avg=%.3f peak=%.3f idle=%.0fs delivered=%.3f" % (
            dn,t.total_mb,t.avg_throughput_mbps,t.peak_throughput_mbps,t.stall_seconds,t.delivered_fraction))
print("  run:", d.name)
