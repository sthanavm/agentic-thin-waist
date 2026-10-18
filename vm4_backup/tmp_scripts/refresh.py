import sys, json, pathlib, dataclasses, shutil
H_ = pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0, str(H_/"experiments/pramana")); sys.path.insert(0, str(H_))
import pramana_helpers as H
from shared import qoe as Q
base = H_/"experiments/pramana/results/pramana_runs"
run, app = sys.argv[1], sys.argv[2]
d = base/run
rec = json.load(open(d/"record.json"))
old = dict(rec["player_qoe"][app])

# 1. re-derive player QoE from the raw samples
jl = d/"qoe"/f"{app}_stats.jsonl"
new = Q.summarize(jl, app)
rec["player_qoe"][app] = new
if app in rec.get("per_app", {}):
    rec["per_app"][app]["qoe"] = new
    rec["per_app"][app]["player_qoe_available"] = bool(new.get("player_qoe_available"))
# 2. top-level + limitations flags derived, not stale (bug 5)
rec["player_qoe_available"] = bool(Q.any_real_qoe(rec["player_qoe"]))
rec.setdefault("limitations", {})["player_qoe_available"] = rec["player_qoe_available"]

# 3. re-attribute and replot with aligned axes
flds = {f.name for f in dataclasses.fields(H.ExperimentConfig)}
cfg = H.ExperimentConfig(**{k: v for k, v in rec["config"].items() if k in flds})
at = H.attribute_capture(d/"capture.pcap", cfg.apps, float(cfg.bandwidth_mbps))
rec["capture_buckets_mb"] = at.bucket_mb
rec["limitations"]["attributed_byte_fraction"] = at.attributed_fraction
rec["limitations"]["adopted_unnamed_ips"] = at.adopted_ips
rec["per_app_stats"] = {a: {dn: at.traffic(a, dn).as_record()
                            for dn in ("download","upload") if at.traffic(a, dn)}
                        for a in cfg.apps}
if not (d/"record.json.pre_qoefix2.bak").exists():
    shutil.copy2(d/"record.json", d/"record.json.pre_qoefix2.bak")
json.dump(rec, open(d/"record.json","w"), indent=1)
plots = H.generate_run_plots(at, cfg, d, rec.get("player_qoe"))

shift = (new.get("session_start_epoch") or 0) - (at.t0_epoch or 0)
print(f"  x-axis shift applied: {shift:+.1f}s   (capture t0={at.t0_epoch}, session t0={new.get('session_start_epoch')})")
print("  field                 before -> after")
for k in ("status","watched_seconds","session_seconds","frame_rate_fps","video_startup_time_ms",
          "video_resolution_p","rebuffer_events","rebuffer_duration_ms","mean_bitrate_mbps",
          "max_bitrate_mbps","total_video_frames","dropped_video_frames"):
    a, b = old.get(k), new.get(k)
    print("   %-22s %-14s -> %s%s" % (k, a, b, "   <-- changed" if a != b else ""))
lbl, why, _ = H._panel_verdict(at.traffic(app,"download"), new)
print(f"  VERDICT: {lbl} — {why}")
print(f"  top-level player_qoe_available: {rec['player_qoe_available']}")
