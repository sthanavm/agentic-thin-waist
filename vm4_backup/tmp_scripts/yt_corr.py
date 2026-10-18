import sys, json, collections, pathlib
from scapy.all import PcapReader, IP
D=pathlib.Path(sys.argv[1]); PEER="198.189.66.13"; LOCAL="172.16.1.1"
first=None; down=collections.Counter()
with PcapReader(str(D/"capture.pcap")) as rd:
    for p in rd:
        t=float(p.time)
        if first is None: first=t
        ip=p.getlayer(IP)
        if ip is None: continue
        if ip.src==PEER and ip.dst==LOCAL: down[int(t-first)]+=len(p)
rows=[json.loads(l) for l in open(D/"qoe"/"youtube_stats.jsonl") if l.strip()]
rows=[r for r in rows if r.get("record")!="meta"]
def st(r): return r.get("stats",r)
t0=rows[0]["timestamp"]; off=t0-first
dur=next((st(r).get("duration_secs") for r in rows if st(r).get("duration_secs")),None)
print(f"  video duration {dur}s;  session t=0 = capture t={off:+.1f}s")
print("\n  sess_t  loaded_frac  Dloaded_MB_est   adopted_flow_bytes/s")
prev=None; pairs=[]
for r in rows:
    s=st(r); t=r["timestamp"]-t0; lf=s.get("loaded_fraction")
    sec=int(t+off); b=down.get(sec,0)
    if prev and lf is not None:
        dlf=lf-prev
        pairs.append((dlf,b))
        if int(t)%8==0:
            print("  %6.1f  %11.4f  %14.4f   %18d" % (t, lf, dlf, b))
    if lf is not None: prev=lf
adv=[b for d,b in pairs if d>0.0005]; flat=[b for d,b in pairs if d<=0.0005]
import statistics as S
print(f"\n  seconds where buffer ADVANCED: n={len(adv)}  mean adopted-flow bytes/s = {S.mean(adv):,.0f}" if adv else "")
print(f"  seconds where buffer was FLAT : n={len(flat)}  mean adopted-flow bytes/s = {S.mean(flat):,.0f}" if flat else "")
tot=sum(down.values())
print(f"\n  adopted flow total: {tot/1e6:.2f} MB")
print(f"  player buffered {(rows[-1] and st(rows[-1]).get('loaded_fraction')):.3f} of the video by the end")
