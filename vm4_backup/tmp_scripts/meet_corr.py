import sys, json, collections, pathlib
from scapy.all import PcapReader, IP
D=pathlib.Path(sys.argv[1]); LOCAL="172.16.1.1"; PEER="74.125.250.130"
first=None; down=collections.Counter(); up=collections.Counter()
with PcapReader(str(D/"capture.pcap")) as rd:
    for p in rd:
        t=float(p.time)
        if first is None: first=t
        ip=p.getlayer(IP)
        if ip is None: continue
        sec=int(t-first)
        if ip.src==PEER and ip.dst==LOCAL: down[sec]+=len(p)
        elif ip.src==LOCAL and ip.dst==PEER: up[sec]+=len(p)
rows=[json.loads(l) for l in open(D/"qoe"/"meet_stats.jsonl") if l.strip()]
rows=[r for r in rows if r.get("record")!="meta"]
t0=rows[0]["timestamp"]; off=t0-first
print(f"session t=0 = capture t={off:+.1f}s;  peer {PEER}")
print("\n sess_t  getStats d_bytes_recv/s   pcap DOWN(peer)/s   pcap UP(peer)/s")
prev=None
for r in rows:
    s=r.get("stats",r); t=r["timestamp"]-t0
    br=s.get("bytes_received")
    if prev and br is not None and t>=44:
        dt=t-prev[1]; rate=(br-prev[0])/dt if dt>0 else 0
        sec=int(t+off)
        print("  %5.1f   %18.0f   %17d   %15d" % (t, rate, down.get(sec,0), up.get(sec,0)))
    if br is not None: prev=(br,t)
tot_gs=(rows[-1].get("stats",rows[-1]).get("bytes_received") or 0)-(rows[0].get("stats",rows[0]).get("bytes_received") or 0)
lo,hi=int(off),int(rows[-1]["timestamp"]-first)
print(f"\n getStats bytes_received grew {tot_gs/1e6:.2f} MB over the session")
print(f" pcap DOWN from {PEER} over cap_t {lo}-{hi}: {sum(v for k,v in down.items() if lo<=k<=hi)/1e6:.2f} MB")
