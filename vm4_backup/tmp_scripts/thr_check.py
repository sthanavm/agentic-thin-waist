import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana"))
import pramana_helpers as H
base = pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
print(" %-40s %-7s %-8s %5s | zero-byte  <0.05Mbps  <5%%cap  | median Mbps" % ("run","app","dir","bins"))
for run, cap in [(a.split("|")[0], float(a.split("|")[1])) for a in sys.argv[1:]]:
    d=base/run
    rec=json.load(open(d/"record.json")); apps=rec["config"]["apps"]
    at=H.attribute_capture(d/"capture.pcap", apps, cap)
    for a in apps:
        for dirn in ("download","upload"):
            t=at.traffic(a,dirn)
            if not t or not t.mbps: continue
            r=t.mbps; n=len(r)
            z=sum(1 for v in r if v==0.0)
            lo=sum(1 for v in r if v<0.05)
            hi=sum(1 for v in r if v<max(0.05*cap,0.01))
            med=sorted(r)[n//2]
            print(" %-40s %-7s %-8s %5d | %9d  %9d  %6d  | %.4f" % (run[:40],a,dirn,n,z,lo,hi,med))
