import sys, json, pathlib, importlib.util
H_ = pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana"
sys.path.insert(0, str(H_))
import pramana_helpers as NEW
base = H_/"results/pramana_runs"
rows=[]
for rj in sorted(base.glob("*/record.json")):
    d=rj.parent
    if not (d/"capture.pcap").exists(): continue
    try: rec=json.load(open(rj))
    except Exception: continue
    apps=rec.get("config",{}).get("apps") or []
    cap=rec.get("config",{}).get("bandwidth_mbps") or 0
    if not apps or not cap: continue
    try: at=NEW.attribute_capture(d/"capture.pcap", apps, float(cap))
    except Exception as e:
        print("  ERR", d.name, e); continue
    old_pa=rec.get("per_app_stats") or {}
    for a in apps:
        for dirn in ("download","upload"):
            t=at.traffic(a,dirn)
            o=(old_pa.get(a) or {}).get(dirn) or {}
            if not t or not o: continue
            rows.append(dict(run=d.name, app=a, dirn=dirn,
                old_mb=o.get("total_mb"), new_mb=t.total_mb,
                old_stall=o.get("stall_seconds"), new_stall=t.stall_seconds,
                old_del=o.get("delivered_fraction"), new_del=t.delivered_fraction,
                adopted=bool(at.adopted_ips)))
ch=[r for r in rows if r["old_mb"]!=r["new_mb"] or r["old_stall"]!=r["new_stall"]]
same=[r for r in rows if r not in ch]
print(f"  series compared: {len(rows)}   unchanged: {len(same)}   changed: {len(ch)}")
byapp={}
for r in rows:
    k=r["app"]; byapp.setdefault(k,[0,0])
    byapp[k][1 if r in ch else 0]+=1
print("  per app (unchanged/changed): "+"  ".join(f"{k}={v[0]}/{v[1]}" for k,v in sorted(byapp.items())))
print("\n  CHANGED series:")
for r in sorted(ch, key=lambda r:-(abs((r['new_mb'] or 0)-(r['old_mb'] or 0)))):
    print("   %-46s %-8s %-8s MB %7.2f->%7.2f  stall %5.1f->%5.1f  deliv %.3f->%.3f %s"
          % (r["run"][:46], r["app"], r["dirn"], r["old_mb"] or 0, r["new_mb"] or 0,
             r["old_stall"] or 0, r["new_stall"] or 0, r["old_del"] or 0, r["new_del"] or 0,
             "ADOPTED" if r["adopted"] else ""))
