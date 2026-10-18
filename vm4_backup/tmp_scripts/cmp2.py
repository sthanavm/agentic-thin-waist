import json, pathlib
base=pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
old=json.load(open(base/"meet_10mbps_50ms_0pct_pfifo_cubic_solo_t1_05afa404ea"/"record.json"))
new=json.load(open(base/"meet_10mbps_50ms_0pct_pfifo_cubic_solo_t1_eefe603aa4"/"record.json"))
def row(k, fmt="%s"):
    a=old["player_qoe"]["meet"].get(k); b=new["player_qoe"]["meet"].get(k)
    print("   %-24s %-16s %-16s" % (k, a, b))
print("   %-24s %-16s %-16s" % ("metric","PREV 05afa404ea","NEW eefe603aa4"))
print("   " + "-"*58)
for k in ("status","video_resolution_p","frame_rate_fps","mean_bitrate_mbps","max_bitrate_mbps",
          "rebuffer_events","rebuffer_duration_ms","dropped_frame_pct","dropped_video_frames",
          "total_video_frames","watched_seconds","session_seconds","video_startup_time_ms",
          "packet_loss_pct","mean_jitter_secs","total_samples"):
    row(k)
print()
for r,n in ((old,"PREV"),(new,"NEW ")):
    ns=r["network_stats"]; pas=r["per_app_stats"]["meet"]
    print("   %s capture=%.1fs  dl avg=%.2f peak=%.2f tot=%.1fMB idle=%.0fs | ul avg=%.2f peak=%.2f tot=%.1fMB idle=%.0fs" % (
        n, ns["duration_s"], pas["download"]["avg_throughput_mbps"], pas["download"]["peak_throughput_mbps"],
        pas["download"]["total_mb"], pas["download"]["stall_seconds"],
        pas["upload"]["avg_throughput_mbps"], pas["upload"]["peak_throughput_mbps"],
        pas["upload"]["total_mb"], pas["upload"]["stall_seconds"]))
print()
print("   NEW verdict:", new.get("per_app",{}).get("meet",{}).get("traffic_verdict"))
print("   NEW buckets:", new.get("capture_buckets_mb"))
print("   NEW adopted:", new.get("limitations",{}).get("adopted_unnamed_ips"))
