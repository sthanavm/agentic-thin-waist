import json, pathlib, sys, collections
H_=pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0,str(H_/"experiments/pramana")); sys.path.insert(0,str(H_))
import pramana_helpers as H
base=H_/"experiments/pramana/results/pramana_runs"
RUNS=[("meet_10mbps_50ms_0pct_pfifo_cubic_solo_t1_05afa404ea","meet"),
      ("meet_10mbps_50ms_0pct_pfifo_cubic_solo_t1_eefe603aa4","meet"),
      ("zoom_10mbps_50ms_0pct_pfifo_cubic_solo_t1_e65a42bb06","zoom"),
      ("youtube_3mbps_50ms_0pct_pfifo_cubic_solo_t1_91b81b9735","youtube")]
for run,app in RUNS:
    d=base/run
    q=json.load(open(d/"record.json"))["player_qoe"][app]
    at=H.attribute_capture(d/"capture.pcap",[app],10.0)
    t=at.traffic(app,"download")
    off=q["session_start_epoch"]-at.t0_epoch
    pre=[v for i,v in enumerate(t.mbps) if i<off]
    # find the last second before sampling with meaningful traffic, and quiet run
    busy=[i for i,v in enumerate(pre) if v>0.20]
    last_busy=busy[-1] if busy else None
    quiet=(off-last_busy) if last_busy is not None else None
    loadmb=sum(pre)/8
    print(" %-46s sampling@%.1fs" % (run[:46], off))
    print("      page/app load traffic : %.2f MB, busy until t=%.0fs" % (loadmb, last_busy if last_busy else -1))
    print("      quiet gap before sampling starts : %.0fs" % (quiet if quiet else -1))
    # bytes in the first 15s vs rest of pre-window
    early=sum(pre[:15])/8; late=sum(pre[15:])/8
    print("      first 15s: %.2f MB   |   t=15s..sampling: %.2f MB" % (early, late))
    print()
