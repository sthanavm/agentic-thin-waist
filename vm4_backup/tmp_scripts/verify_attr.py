import sys, json, collections, pathlib
from scapy.all import PcapReader, IP
D=pathlib.Path(sys.argv[1]); app=sys.argv[2]; PEER=sys.argv[3]; LOCAL="172.16.1.1"
field=sys.argv[4]   # player-side cumulative byte counter
first=None; down=collections.Counter()
with PcapReader(str(D/"capture.pcap")) as rd:
    for p in rd:
        t=float(p.time)
        if first is None: first=t
        ip=p.getlayer(IP)
        if ip is None: continue
        if ip.src==PEER and ip.dst==LOCAL: down[int(t-first)]+=len(p)
rows=[json.loads(l) for l in open(D/"qoe"/f"{app}_stats.jsonl") if l.strip()]
rows=[r for r in rows if r.get("record")!="meta"]
def st(r): return r.get("stats",r)
t0=rows[0]["timestamp"]; off=t0-first
vals=[(r["timestamp"]-t0, st(r).get(field)) for r in rows]
vals=[(t,v) for t,v in vals if isinstance(v,(int,float))]
print(f"  adopted peer {PEER};  session t=0 = capture t={off:+.1f}s")
if vals:
    grew=vals[-1][1]-vals[0][1]
    lo,hi=int(off),int(rows[-1]['timestamp']-first)
    pcap_win=sum(v for k,v in down.items() if lo<=k<=hi)
    print(f"  player-side {field}: grew {grew/1e6:.2f} MB over the session")
    print(f"  pcap DOWN from adopted peer over the same window: {pcap_win/1e6:.2f} MB")
    print(f"  ratio pcap/player = {pcap_win/grew:.2f}x  (>1 expected: headers, retransmits, RTCP/audio)")
# seconds with any traffic from the adopted peer, inside the session
lo,hi=int(off),int(rows[-1]['timestamp']-first)
active=sum(1 for k in range(lo,hi+1) if down.get(k,0)>0)
print(f"  seconds with >0 bytes from adopted peer, session window: {active}/{hi-lo+1}")
nz=[down.get(k,0) for k in range(lo,hi+1)]
if nz: print(f"  per-second bytes: min={min(nz):,}  median={sorted(nz)[len(nz)//2]:,}  max={max(nz):,}")
