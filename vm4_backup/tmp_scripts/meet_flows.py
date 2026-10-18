import sys, collections
from scapy.all import PcapReader, IP, IPv6, UDP, TCP
pcap, sess_start = sys.argv[1], float(sys.argv[2])
LOCAL = "172.16.1.1"
first=None; flows=collections.defaultdict(lambda: [0,0,None,None])  # peer -> up,down,proto,lastsec
persec=collections.defaultdict(lambda: collections.Counter())
with PcapReader(pcap) as rd:
    for p in rd:
        t=float(p.time)
        if first is None: first=t
        ip=p.getlayer(IP) or p.getlayer(IPv6)
        if ip is None: continue
        proto = "UDP" if p.haslayer(UDP) else ("TCP" if p.haslayer(TCP) else "other")
        port = (p[UDP].dport if p.haslayer(UDP) else (p[TCP].dport if p.haslayer(TCP) else None))
        sec=int(t-first)
        if ip.src==LOCAL:
            peer=ip.dst; flows[peer][0]+=len(p); persec[peer][sec]+=len(p)
        elif ip.dst==LOCAL:
            peer=ip.src; flows[peer][1]+=len(p)
        else: continue
        flows[peer][2]=proto; flows[peer][3]=port
off = sess_start-first
print("peer                     proto  up_MB  down_MB   UP bytes in cap_t 45-96   secs_active/52")
for peer,(u,d,pr,po) in sorted(flows.items(), key=lambda kv:-(kv[1][0]+kv[1][1]))[:8]:
    win=sum(v for k,v in persec[peer].items() if 45<=k<=96)
    act=sum(1 for k in range(45,97) if persec[peer].get(k,0)>0)
    print(f"  {peer:22s} {pr:5s} {u/1e6:6.2f} {d/1e6:8.2f}   {win:>14,}   {act:>10}")
print(f"\nsession t=0 is capture t={off:+.1f}s")
