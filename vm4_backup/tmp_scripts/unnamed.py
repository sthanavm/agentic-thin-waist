import sys, collections, pathlib
sys.path.insert(0, str(pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana"))
import pramana_helpers as H

def survey(pcap, apps):
    ip_host={}; endpoint=collections.Counter(); tot=0; t0=None; tmax=0
    import struct
    for ts,_ol,lt,data in H.iter_capture(str(pcap)):
        fam,l3=H._l3(lt,data)
        if fam is None: continue
        p=H._parse_ip(fam,l3)
        if p is None: continue
        src,dst,proto,l4=p
        if ts:
            t0=ts if t0 is None else min(t0,ts); tmax=max(tmax,ts)
        tot+=1; endpoint[src]+=1; endpoint[dst]+=1
        if proto==17 and len(l4)>=8:
            sp,_=struct.unpack_from(">HH",l4,0)
            if sp==53: H._harvest_dns(l4,ip_host)
        elif proto==6: H._harvest_sni(l4,dst,ip_host)
    local=sorted(ip for ip,c in endpoint.items() if H._is_private(ip) and c>=0.10*tot)
    if not local and endpoint: local=[max(endpoint,key=endpoint.get)]
    L=set(local)
    per_ip=collections.Counter(); per_ip_first={}; per_ip_last={}
    for ts,ol,lt,data in H.iter_capture(str(pcap)):
        fam,l3=H._l3(lt,data)
        if fam is None: continue
        p=H._parse_ip(fam,l3)
        if p is None: continue
        src,dst,_,_=p
        if dst in L: rem=src
        elif src in L: rem=dst
        else: continue
        per_ip[rem]+=ol
        if ts:
            per_ip_first.setdefault(rem,ts); per_ip_last[rem]=ts
    rows=[]
    for ip,b in per_ip.most_common():
        host=ip_host.get(ip,"")
        bucket=H.classify_host(host,apps) if host else "other(unnamed)"
        rows.append((ip,b,host,bucket,per_ip_first.get(ip),per_ip_last.get(ip)))
    return rows,local,t0,tmax

base=pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
for name,apps in [(sys.argv[1], sys.argv[2].split(","))]:
    d=base/name
    rows,local,t0,tmax=survey(d/"capture.pcap", apps)
    print(f"=== {name}  apps={apps}  local={local}  dur={tmax-t0:.1f}s")
    named=sum(b for _,b,h,bk,_,_ in rows if h)
    unnamed=sum(b for _,b,h,_,_,_ in rows if not h)
    print(f"    named {named/1e6:.2f} MB | UNNAMED {unnamed/1e6:.2f} MB")
    print("    ip                       MB     bucket            host                       t_first  t_last")
    for ip,b,host,bucket,f,l in rows[:12]:
        if b < 50000: continue
        print("    %-22s %6.2f  %-16s %-26s %6.1f  %6.1f" % (ip,b/1e6,bucket,host[:26] or "-", (f-t0) if f else -1, (l-t0) if l else -1))
