import json, pathlib, sys, struct, collections
H_=pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0,str(H_/"experiments/pramana")); sys.path.insert(0,str(H_))
import pramana_helpers as H
base=H_/"experiments/pramana/results/pramana_runs"
RUNS=[("meet_10mbps_50ms_0pct_pfifo_cubic_solo_t1_05afa404ea","meet","meet.google.com"),
      ("meet_10mbps_50ms_0pct_pfifo_cubic_solo_t1_eefe603aa4","meet","meet.google.com"),
      ("zoom_10mbps_50ms_0pct_pfifo_cubic_solo_t1_e65a42bb06","zoom","zoom.us"),
      ("zoom_10mbps_50ms_0pct_pfifo_cubic_solo_t1_a13d5114a9","zoom","zoom.us"),
      ("youtube_3mbps_50ms_0pct_pfifo_cubic_solo_t1_91b81b9735","youtube","youtube.com")]
print(" %-14s %9s %9s %9s %9s %9s" % ("run","1st pkt","1st DNS","app 1st","sampling","cap dur"))
print(" " + "-"*66)
for run,app,dom in RUNS:
    d=base/run
    if not (d/"capture.pcap").exists(): continue
    q=json.load(open(d/"record.json"))["player_qoe"][app]
    ip_host={}; first=None; firstdns=None; appfirst=None
    ip_first={}
    for ts,ol,lt,data in H.iter_capture(str(d/"capture.pcap")):
        fam,l3=H._l3(lt,data)
        if fam is None: continue
        p=H._parse_ip(fam,l3)
        if p is None: continue
        src,dst,proto,l4=p
        if first is None: first=ts
        if proto==17 and len(l4)>=8:
            sp,_=struct.unpack_from(">HH",l4,0)
            if sp==53:
                H._harvest_dns(l4,ip_host)
                if firstdns is None: firstdns=ts
        elif proto==6:
            H._harvest_sni(l4,dst,ip_host)
        for ip in (src,dst):
            ip_first.setdefault(ip,ts)
    for ip,host in ip_host.items():
        if host and (host.endswith(dom) or dom in host):
            t=ip_first.get(ip)
            if t and (appfirst is None or t<appfirst): appfirst=t
    sess=q.get("session_start_epoch")
    at_t0=first
    def rel(x): return (x-at_t0) if x else float("nan")
    print(" %-14s %9.1f %9.1f %9.1f %9.1f %9.1f" % (
        run[:14], 0.0, rel(firstdns), rel(appfirst), rel(sess), 0))
