#!/usr/bin/env python3
"""Item 3 (probe overhead A/B) and item 6 (repeat trials), with CPU sampling.

Item 3: the same Vimeo 10Mbps cell with the stream probe disabled and enabled,
to find out whether the instrumentation itself causes the degradation seen there.
Item 6: three trials each on youtube 1/3 Mbps and vimeo 3/6 Mbps, so the n=1
results can be told apart from luck.
"""
import json, os, pathlib, subprocess, sys, time

os.environ["PRAMANA_CAPTURE_OVERHEAD_S"] = "40"
os.environ["PRAMANA_DEFER_ANALYSIS"] = "1"
sys.path.insert(
    0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana")
)
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))
import pramana_helpers as H

DUR = int(os.environ.get("DUR", "180"))
SAMPLER = "/tmp/cpu_sampler.py"
JOBS = json.loads(sys.argv[1])  # [[app, bw, trial, probe_off], ...]
KEYS = (
    "startup_delay_ms",
    "rebuffer_events",
    "delivered_video_bitrate_mbps",
    "delivered_audio_bitrate_mbps",
    "switch_count",
    "rendered_fps",
    "mse_video_bytes",
    "mse_audio_bytes",
    "rt_media_bytes",
    "rt_all_bytes",
    "rt_dropped_by_initiator",
    "rt_dropped_by_size_floor",
    "rt_dropped_bytes",
    "rt_all_entries",
    "rt_media_entries",
    "rt_buffer_full_events",
)

for app, bw, trial, probe_off in JOBS:
    tag = "%s_%gM_t%d_%s" % (app, bw, trial, "probeoff" if probe_off else "probeon")
    print("=== %s ===" % tag, flush=True)
    cpu = pathlib.Path("/tmp/cpu_%s_%d.jsonl" % (tag, int(time.time())))
    samp = None
    try:
        samp = subprocess.Popen(
            [
                "sudo",
                "-n",
                "python3",
                SAMPLER,
                str(cpu),
                str(DUR + 120),
                "5",
                json.dumps({"99": app}),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    cfg = H.ExperimentConfig(
        apps=[app],
        bandwidth_mbps=bw,
        upload_mbps=bw,
        latency_ms=50,
        loss_pct=0,
        aqm="pfifo",
        buffer_packets=1000,
        cca="cubic",
        duration_s=DUR,
        trial=trial,
        tag=tag,
        merge_vendor_stats=False,
        disable_stream_probe=bool(probe_off),
    )
    try:
        rec = H.run_direct(cfg)
    except Exception as e:
        print("FAILED %s %s: %s" % (tag, type(e).__name__, str(e)[:140]), flush=True)
        continue
    finally:
        if samp:
            try:
                samp.wait(timeout=120)
            except Exception:
                samp.kill()
    q = (rec.get("player_qoe") or {}).get(app) or {}
    u = q.get("uniform") or {}
    row = {k: u.get(k) for k in KEYS}
    row.update(
        status=q.get("status"),
        res_p=q.get("video_resolution_p"),
        legacy_fps=q.get("frame_rate_fps"),
        legacy_startup_ms=q.get("video_startup_time_ms"),
        buffer_mean=(u.get("buffer_level_secs") or {}).get("mean"),
        vmime=(u.get("mse_video_mime") or "")[:34],
    )
    # CPU digest
    peaks, tot = {}, []
    if cpu.exists():
        for line in cpu.read_text(errors="replace").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if "cpu_pct" not in r:
                continue
            tot.append(r.get("cpu_pct_total_of_host", 0))
            for k, v in r["cpu_pct"].items():
                peaks[k] = max(peaks.get(k, 0), v)
    row["cpu_host_peak"] = max(tot) if tot else None
    row["cpu_host_mean"] = round(sum(tot) / len(tot), 1) if tot else None
    row["cpu_collector_peak"] = round(peaks.get("collector_chrome_%s" % app, 0), 1)
    print("ROW %s %s" % (tag, json.dumps(row)), flush=True)
    print("DIR %s %s" % (tag, (rec.get("artifacts") or {}).get("dir")), flush=True)
print("BATCH_DONE", flush=True)
