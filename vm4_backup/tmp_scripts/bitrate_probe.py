import json, collections, pathlib, statistics as S
from scapy.all import PcapReader, IP
D=pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs/meet_10mbps_50ms_0pct_pfifo_cubic_solo_t1_05afa404ea"
LOCAL="172.16.1.1"
first=None; up=collections.Counter(); down=collections.Counter()
with PcapReader(str(D/"capture.pcap")) as rd:
    for p in rd:
        t=float(p.time)
        if first is None: first=t
        ip=p.getlayer(IP)
        if ip is None: continue
        sec=int(t-first)
        if ip.src==LOCAL: up[sec]+=len(p)
        elif ip.dst==LOCAL: down[sec]+=len(p)
rows=[json.loads(l) for l in open(D/"qoe"/"meet_stats.jsonl") if l.strip()]
rows=[r for r in rows if r.get("record")!="meta"]
def st(r): return r.get("stats",r)
t0=rows[0]["timestamp"]; off=t0-first
print("  sess_t  inbound_bitrate_mbps   d(bytes_received)Mbps   pcap DOWN Mbps   pcap UP Mbps")
prev=None; A=[];B=[];C=[]
for r in rows:
    s=st(r); t=r["timestamp"]-t0; sec=int(t+off)
    ib=s.get("inbound_bitrate_mbps"); br=s.get("bytes_received")
    if prev and ib is not None and br is not None:
        dt=t-prev[1]
        recv=(br-prev[0])*8/dt/1e6 if dt>0 else 0
        d=down.get(sec,0)*8/1e6; u=up.get(sec,0)*8/1e6
        A.append(ib); B.append(recv); C.append(u)
        if int(t)%7==0:
            print("  %6.1f %18.4f %20.4f %16.4f %14.4f" % (t, ib, recv, d, u))
    if br is not None: prev=(br,t)
print()
print("  mean inbound_bitrate_mbps (field)      : %.4f" % S.mean(A))
print("  mean d(bytes_received) recomputed      : %.4f  <- matches field? %s" % (S.mean(B), abs(S.mean(A)-S.mean(B))<0.02))
print("  mean pcap UPLOAD                       : %.4f" % S.mean(C))
def corr(x,y):
    mx,my=S.mean(x),S.mean(y)
    n=sum((a-mx)*(b-my) for a,b in zip(x,y))
    d=(sum((a-mx)**2 for a in x)*sum((b-my)**2 for b in y))**0.5
    return n/d if d else 0
print()
print("  correlation( field , recomputed bytes_received ) = %.4f" % corr(A,B))
print("  correlation( field , pcap UPLOAD )               = %.4f" % corr(A,C))
