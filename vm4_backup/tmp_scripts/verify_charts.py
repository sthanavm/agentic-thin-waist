import json, pathlib
base=pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
for run,app in [("meet_10mbps_50ms_0pct_pfifo_cubic_solo_t1_05afa404ea","meet"),
                ("zoom_10mbps_50ms_0pct_pfifo_cubic_solo_t1_e65a42bb06","zoom")]:
    r=json.load(open(base/run/"record.json")); q=r["player_qoe"][app]; pas=r["per_app_stats"][app]
    print("### %s" % app)
    tot=0
    for d in ("download","upload"):
        s=pas[d]; tot+=s["total_mb"]
        print("   %-8s avg=%.2f peak=%.2f total=%.1fMB idle=%.0fs deliv=%.3f cls=%s" % (
            d, s["avg_throughput_mbps"], s["peak_throughput_mbps"], s["total_mb"],
            s["stall_seconds"], s["delivered_fraction"], s["classification"]))
    print("   startup=%sms rebuf=%s (%sms) res=%sp fps=%s" % (
        q["video_startup_time_ms"], q["rebuffer_events"], q["rebuffer_duration_ms"],
        q["video_resolution_p"], q["frame_rate_fps"]))
    print("   dropped=%s%% bitrate=%s watched=%ss samples=%s" % (
        q["dropped_frame_pct"], q["mean_bitrate_mbps"], q["watched_seconds"], q["total_samples"]))
    print("   buckets=%s   dl+ul=%.2fMB" % (r["capture_buckets_mb"], tot))
    print("   adopted=%s" % r["limitations"].get("adopted_unnamed_ips"))
    print()
