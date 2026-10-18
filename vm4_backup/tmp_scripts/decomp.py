import json, collections, pathlib
from scapy.all import PcapReader, IP
D=pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs/meet_10mbps_50ms_0pct_pfifo_cubic_solo_t1_05afa404ea"
LOCAL="172.16.1.1"
first=None; down=collections.Counter(); up=collections.Counter()
with PcapReader(str(D/"capture.pcap")) as rd:
    for p in rd:
        t=float(p.time)
        if first is None: first=t
        ip=p.getlayer(IP)
        if ip is None: continue
        sec=int(t-first)
        if ip.dst==LOCAL: down[sec]+=len(p)
        elif ip.src==LOCAL: up[sec]+=len(p)
rows=[json.loads(l) for l in open(D/"qoe"/"meet_stats.jsonl") if l.strip()]
rows=[r for r in rows if r.get("record")!="meta"]
def st(r): return r.get("stats",r)
t0=rows[0]["timestamp"]; off=int(t0-first); end=int(rows[-1]["timestamp"]-first)
vid=(st(rows[-1])["bytes_received"]-st(rows[0])["bytes_received"])/1e6
dl_sess=sum(v for k,v in down.items() if off<=k<=end)/1e6
dl_pre =sum(v for k,v in down.items() if k<off)/1e6
dur=end-off
print(f"  capture 0..{max(down)}s, session window {off}..{end}s ({dur}s)")
print()
print(f"  ALL download, whole capture      : {sum(down.values())/1e6:6.2f} MB   -> avg over 96.25s = {sum(down.values())*8/96.25/1e6:.3f} Mbps  <- chart 'avg 0.70'")
print(f"    of which BEFORE the call       : {dl_pre:6.2f} MB   (page load / join)")
print(f"    of which DURING the call       : {dl_sess:6.2f} MB   -> {dl_sess*8/dur/1e6:.3f} Mbps")
print(f"  inbound VIDEO only (getStats)    : {vid:6.2f} MB   -> {vid*8/dur/1e6:.3f} Mbps  <- chart 'bitrate 0.35'")
print(f"    video as share of in-call down : {100*vid/dl_sess:.1f}%")
print(f"    non-video in-call (audio/RTCP/signalling/app): {dl_sess-vid:.2f} MB")
