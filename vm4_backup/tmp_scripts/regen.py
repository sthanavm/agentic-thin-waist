import sys, json, pathlib, dataclasses, shutil
H_ = pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana"
sys.path.insert(0, str(H_))
import pramana_helpers as H
base = H_/"results/pramana_runs"
run = sys.argv[1]
d = base/run
rec = json.load(open(d/"record.json"))
cfgd = dict(rec["config"])
fields = {f.name for f in dataclasses.fields(H.ExperimentConfig)}
cfg = H.ExperimentConfig(**{k: v for k, v in cfgd.items() if k in fields})
cap = float(cfg.bandwidth_mbps)
at = H.attribute_capture(d/"capture.pcap", cfg.apps, cap)

# back up once, then refresh the attribution-derived parts of the record
bak = d/"record.json.pre_attrfix.bak"
if not bak.exists(): shutil.copy2(d/"record.json", bak)

per_app_stats = {a: {dn: (at.traffic(a, dn).as_record() if at.traffic(a, dn) else None)
                     for dn in ("download","upload")} for a in cfg.apps}
per_app_stats = {a: {k: v for k, v in dd.items() if v} for a, dd in per_app_stats.items()}
rec["per_app_stats"] = per_app_stats
rec["capture_buckets_mb"] = at.bucket_mb
lim = rec.setdefault("limitations", {})
lim["attributed_byte_fraction"] = at.attributed_fraction
lim["adopted_unnamed_ips"] = at.adopted_ips
json.dump(rec, open(d/"record.json","w"), indent=1)

plots = H.generate_run_plots(at, cfg, d, rec.get("player_qoe"))
print("  regenerated:", {k: pathlib.Path(v).name for k, v in plots.items()})
for a in cfg.apps:
    for dn in ("download","upload"):
        t = at.traffic(a, dn)
        if t: print("   %-8s %-8s total=%7.3f MB  idle=%5.1fs  delivered=%.3f  class=%s"
                    % (a, dn, t.total_mb, t.stall_seconds, t.delivered_fraction, t.classification))
print("  adopted:", at.adopted_ips)
