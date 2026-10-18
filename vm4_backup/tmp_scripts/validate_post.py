"""Three-layer validation of every run after the window-fix build.

Layer 1  record.json internal consistency
Layer 2  raw jsonl samples
Layer 3  independently-parsed pcap ground truth
plus physical-sanity checks across the batch.
"""
import json, glob, os, re, subprocess, sys, datetime
from collections import defaultdict

R = "/home/student/sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
FR = re.compile(
    r"^(\d+\.\d+)\s+.*?length\s+(\d+):\s+(\d{1,3}(?:\.\d{1,3}){3})\.\d+\s+>\s+"
    r"(\d{1,3}(?:\.\d{1,3}){3})\.\d+"
)
def priv(ip):
    a=[int(x) for x in ip.split(".")]
    return a[0]==10 or (a[0]==172 and 16<=a[1]<=31) or (a[0]==192 and a[1]==168)

def parse(pcap, local=None):
    if not os.path.exists(pcap) or os.path.getsize(pcap) < 24: return None
    endp=defaultdict(int); pk=[]
    p=subprocess.Popen(["tcpdump","-r",pcap,"-nn","-tt","-e"],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    for line in p.stdout:
        m=FR.match(line)
        if not m: continue
        pk.append((float(m.group(1)), int(m.group(2)), m.group(3), m.group(4)))
        endp[m.group(3)]+=1; endp[m.group(4)]+=1
    p.wait()
    if not pk: return None
    if local is None:
        local={ip for ip,c in endp.items() if priv(ip) and c>=0.10*len(pk)} or {max(endp,key=endp.get)}
    t0=min(x[0] for x in pk); t1=max(x[0] for x in pk)
    dl=defaultdict(int); dlb=0; ulb=0; tot=0
    for ts,ln,src,dst in pk:
        tot+=ln
        if dst in local: dlb+=ln; dl[int(ts-t0)]+=ln
        elif src in local: ulb+=ln
    dur=t1-t0
    series=[dl.get(i,0)*8/1e6 for i in range(0,int(dur)+1)]
    return dict(total=tot, dl=dlb, ul=ulb, dur=dur, series=series, local=local, pkts=len(pk))

def jsonl(d, app):
    f=os.path.join(d,"qoe",f"{app}_stats.jsonl")
    if not os.path.exists(f): return None
    smp=[json.loads(l) for l in open(f) if l.strip() and json.loads(l).get("record")!="meta"]
    if not smp: return dict(n=0)
    g=lambda k:[ (x.get("stats") or {}).get(k) for x in smp ]
    rs=[x for x in g("ready_state") if x is not None]
    buf=[x for x in g("buffer_ahead_secs") if x is not None]
    frm=[x for x in g("total_video_frames") if x is not None]
    clk=[x for x in g("current_time_secs") if x is not None]
    paused=[x for x in g("paused") if x is not None]
    return dict(n=len(smp),
        novideo=sum(1 for x in smp if (x.get("stats") or {}).get("error")=="no_video"),
        stuck1=sum(1 for x in rs if x==1), rs_min=min(rs) if rs else None,
        frozen=(len(set(round(b,3) for b in buf))<=1) if buf else None,
        frm0=min(frm) if frm else None, frm1=max(frm) if frm else None,
        clk0=min(clk) if clk else None, clk1=max(clk) if clk else None,
        playing=sum(1 for x in paused if x is False))

BUILD=datetime.datetime(2026,9,16,5,36,24).timestamp()
runs=[]
for p in glob.glob(R+"/*/record.json"):
    j=json.load(open(p))
    if j["timestamp"] < BUILD: continue
    runs.append((j["timestamp"], os.path.dirname(p), j))
runs.sort()
print(f"validating {len(runs)} runs ...", flush=True)

out=[]
for n,(ts,d,j) in enumerate(runs,1):
    c=j["config"]; cap=c["bandwidth_mbps"]; apps=c["apps"]
    bugs=[]; finds=[]; notes=[]
    comb=parse(os.path.join(d,"capture.pcap"))
    net=j.get("network_stats") or {}
    art=j.get("artifacts") or {}
    ppa=art.get("pcap_per_app") or {}

    # ---- Layer 3: pcap ground truth + shaping (peak AND average) ----
    if comb is None:
        bugs.append(("NO_PCAP","capture missing or unreadable"))
        peak=avg=None
    else:
        peak=max(comb["series"]) if comb["series"] else 0.0
        avg=comb["dl"]*8/max(comb["dur"],1e-9)/1e6
        if peak > cap*1.10: bugs.append(("SHAPING_PEAK", f"pcap dl peak {peak:.3f} > cap {cap:g}x1.1"))
        if avg  > cap*1.15: bugs.append(("SHAPING_AVG",  f"pcap dl avg {avg:.3f} > cap {cap:g}x1.15"))
        ra=net.get("avg_throughput_mbps"); rd=net.get("duration_s")
        if ra is not None and max(ra,avg)>0.05 and abs(ra-avg)/max(ra,avg)>0.20:
            bugs.append(("TPUT_VS_PCAP", f"record {ra} vs pcap {avg:.3f} Mbps"))
        if rd is not None and abs(rd-comb["dur"])>6:
            bugs.append(("DURATION_VS_PCAP", f"record {rd}s vs pcap {comb['dur']:.1f}s"))
        # combined app bytes must never exceed what the link could carry
        ceil_mb = cap*1e6/8*comb["dur"]*1.10/1e6
        if comb["dl"]/1e6 > ceil_mb:
            bugs.append(("LINK_CEILING", f"{comb['dl']/1e6:.0f} MB > {ceil_mb:.0f} MB carryable"))

    per=[]
    buckets=j.get("capture_buckets_mb") or {}
    tot_b=sum(buckets.values()) or 1
    attributed=sum(v for k,v in buckets.items() if k in apps)/tot_b
    if attributed < 0.60:
        notes.append(f"QUIC-attribution gap: only {100*attributed:.0f}% of bytes named")

    for a in apps:
        pq=(j.get("player_qoe") or {}).get(a) or {}
        st=(j.get("per_app_stats") or {}).get(a) or {}
        dl=st.get("download") or {}
        w,s=pq.get("watched_seconds"), pq.get("session_seconds")
        res=pq.get("video_resolution_p")
        # ---- Layer 1: record internal consistency ----
        if res and pq.get("status")=="ok" and not w:
            bugs.append(("PLAYED_BUT_WATCHED_0", f"{a}: res={res} watched={w}"))
        if not st.get("player_qoe_available") and st.get("classification")=="served":
            bugs.append(("LABEL_VS_PLAYER", f"{a}: classified served without player data"))
        br=pq.get("mean_bitrate_mbps")
        if br is not None and br > cap*1.05:
            bugs.append(("BITRATE_SANITY", f"{a}: bitrate {br} > cap {cap:g}"))
        if pq.get("status")=="no_data" and st.get("classification") not in ("failed_to_deliver","no_traffic"):
            bugs.append(("NO_PLAYBACK_LOGIC", f"{a}: status=no_data but classification={st.get('classification')}"))
        # ---- Layer 2: raw jsonl ----
        rc=jsonl(d,a)
        if rc and rc.get("n"):
            if rc["stuck1"] > rc["n"]*0.5:
                bugs.append(("READYSTATE_STUCK", f"{a}: readyState=1 in {rc['stuck1']}/{rc['n']}"))
            if rc["frozen"] and pq.get("status")=="ok":
                bugs.append(("BUFFER_FROZEN", f"{a}: buffer frozen while ok"))
            if res and pq.get("status")=="ok" and rc["frm0"] is not None and rc["frm1"]<=rc["frm0"]:
                bugs.append(("FRAMES_STATIC", f"{a}: frames {rc['frm0']}->{rc['frm1']}"))
        # ---- per-app pcap cross-check ----
        own=ppa.get(a,"")
        omb=None
        if own and os.path.exists(own) and os.path.basename(own)!="capture.pcap" and comb:
            ob=parse(own, local=comb["local"])
            if ob: omb=ob["dl"]/1e6
            rec_mb=dl.get("total_mb")
            if omb and rec_mb and max(omb,rec_mb)>0.5 and abs(omb-rec_mb)/max(omb,rec_mb)>0.10:
                bugs.append(("BYTES_VS_OWN_PCAP", f"{a}: record {rec_mb} vs own pcap {omb:.2f} MB"))
        per.append(dict(app=a,res=res,startup=pq.get("video_startup_time_ms"),
            wf=(100*w/s) if (w is not None and s) else None, reb=pq.get("rebuffer_events"),
            drop=pq.get("dropped_frame_pct"), status=pq.get("status"), reason=pq.get("reason"),
            mb=dl.get("total_mb"), own_mb=omb, avg=dl.get("avg_throughput_mbps"),
            cls=st.get("classification"), raw=rc,
            obs=pq.get("resolutions_observed")))
    out.append(dict(dir=os.path.basename(d), ts=ts, tag=c.get("tag"), apps=apps, cap=cap,
        lat=c.get("latency_ms"), loss=c.get("loss_pct"), aqm=c.get("aqm"), dur=c.get("duration_s"),
        peak=peak, avg=avg, rec_avg=net.get("avg_throughput_mbps"),
        rec_dur=net.get("duration_s"), pcap_dur=comb["dur"] if comb else None,
        attributed=attributed, per_app=per, bugs=bugs, finds=finds, notes=notes))
    if n%10==0: print(f"  ... {n}/{len(runs)}", flush=True)

json.dump(out, open("/tmp/post_rows.json","w"), indent=1, default=str)
print(f"\nDONE: {len(out)} runs, raw bug flags on {sum(1 for r in out if r['bugs'])}", flush=True)
