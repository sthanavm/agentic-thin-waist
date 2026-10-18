import sys, pathlib
sys.path.insert(0, str(pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana"))
import pramana_helpers as H
base = pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
run, app, cap, sess_off = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
at = H.attribute_capture(base/run/"capture.pcap", [app], cap)
t = at.traffic(app, "download")
r = t.mbps
thr_cap = max(0.05*cap, 0.01)
for name, thr in (("5%-of-cap (current)", thr_cap), ("absolute 0.05 Mbps", 0.05)):
    stalls = [i for i, v in enumerate(r) if v < thr]
    pre  = [i for i in stalls if i < sess_off]
    post = [i for i in stalls if i >= sess_off]
    print(f"  {name:22s} thr={thr:.3f} Mbps -> stall {len(stalls):3d}/{len(r)} bins "
          f"| pre-session {len(pre):3d}  during-session {len(post):3d}  delivered={1-len(stalls)/len(r):.3f}")
during = r[int(sess_off):]
print(f"\n  during-session bins: n={len(during)}  min={min(during):.4f}  median={sorted(during)[len(during)//2]:.4f}  max={max(during):.4f} Mbps")
print(f"  bins with literally zero bytes during session: {sum(1 for v in during if v==0)}")
