import json, pathlib, sys
H_=pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0,str(H_/"experiments/pramana")); sys.path.insert(0,str(H_))
import pramana_helpers as H
base=H_/"experiments/pramana/results/pramana_runs"
for run,app,cap in [("meet_10mbps_50ms_0pct_pfifo_cubic_solo_t1_05afa404ea","meet",10.0),
                    ("zoom_10mbps_50ms_0pct_pfifo_cubic_solo_t1_e65a42bb06","zoom",10.0)]:
    d=base/run; r=json.load(open(d/"record.json")); q=r["player_qoe"][app]
    at=H.attribute_capture(d/"capture.pcap",[app],cap)
    sess_t0=q["session_start_epoch"]; cap_t0=at.t0_epoch
    off=sess_t0-cap_t0; sess=q["session_seconds"]
    print("### %s  call starts at capture t=%.1fs, call ends t=%.1fs, capture ends t=%.1fs" % (
        app, off, off+sess, at.duration_s))
    clipped=(off+sess)-at.duration_s
    print("    player samples past the plotted x-axis: %.1fs %s" % (
        max(0,clipped), "<-- CLIPPED from the chart" if clipped>0 else ""))
    for dirn in ("download","upload"):
        t=at.traffic(app,dirn)
        pre=sum(v for i,v in enumerate(t.mbps) if i<off)*at.bin_s/8
        during=sum(v for i,v in enumerate(t.mbps) if i>=off)*at.bin_s/8
        print("    %-8s total %.2f MB = %.2f MB BEFORE the call (setup/asset load) + %.2f MB during (%.0f%% during)" % (
            dirn, t.total_mb, pre, during, 100*during/max(t.total_mb,1e-9)))
        incall=[v for i,v in enumerate(t.mbps) if i>=off]
        if incall:
            print("             in-call mean %.3f Mbps (chart avg %.2f is over the whole %.0fs capture)" % (
                sum(incall)/len(incall), t.avg_throughput_mbps, at.duration_s))
    print()
