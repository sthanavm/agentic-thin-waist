import json, pathlib, collections
d=pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs/meet_10mbps_50ms_0pct_pfifo_cubic_solo_t1_eefe603aa4"
rows=[json.loads(l) for l in open(d/"qoe"/"meet_stats.jsonl") if l.strip()]
rows=[r for r in rows if r.get("record")!="meta"]
def st(r): return r.get("stats",r)
per=collections.defaultdict(list)
for r in rows:
    for s in (st(r).get("inbound_streams") or []):
        per[s["id"]].append((r["timestamp"], s))
span=rows[-1]["timestamp"]-rows[0]["timestamp"]
print("  span %.2fs, %d streams\n" % (span, len(per)))
for sid, pts in per.items():
    f0,f1 = pts[0][1], pts[-1][1]
    dfr = f1["frames_decoded"]-f0["frames_decoded"]
    dby = f1["bytes_received"]-f0["bytes_received"]
    ddr = f1["frames_dropped"]-f0["frames_dropped"]
    res = collections.Counter("%sx%s"%(p[1]["frame_width"],p[1]["frame_height"]) for p in pts)
    print("  stream %s" % sid)
    print("     resolution(s)   : %s" % dict(res))
    print("     frames decoded  : %d over %.1fs = %.1f fps" % (dfr, span, dfr/span))
    print("     bytes received  : %.2f MB = %.3f Mbps" % (dby/1e6, dby*8/span/1e6))
    print("     frames dropped  : %d (%.2f%%)" % (ddr, 100*ddr/max(dfr,1)))
    print("     codec/freezes   : %s / %s" % (f1.get("codec"), f1.get("freeze_count")))
    print()
