"""Re-derive player_qoe from stored samples with the corrected rebuffer rule.

Pure re-derivation: shared/qoe.py reduces the per-second JSONL that is already
on disk, so nothing is re-measured and no value is invented. Only runs whose
numbers actually change are rewritten, and the old value is printed next to the
new one so the diff is auditable.
"""
import json, pathlib, sys, shutil
H_ = pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0, str(H_/"experiments/pramana")); sys.path.insert(0, str(H_))
import pramana_helpers as H
reg, qoelib = H._bind_shared()
base = H_/"experiments/pramana/results/pramana_runs"
APPLY = "--apply" in sys.argv

for lat in (50,150):
    for bw in (3,6,10):
        c=sorted(base.glob("zoom+vimeo_%dmbps_%dms_0pct_pfifo_cubic_concurrent2_t1_*"%(bw,lat)),
                 key=lambda p:p.stat().st_mtime)
        if not c: print("  %2dM/%3dms NO RUN"%(bw,lat)); continue
        d=c[-1]; rp=d/"record.json"; rec=json.load(open(rp))
        changed=False; lines=[]
        for app in ("zoom","vimeo"):
            j=d/"qoe"/f"{app}_stats.jsonl"
            if not j.exists(): continue
            new=qoelib.summarize(j, app)
            old=(rec.get("player_qoe") or {}).get(app) or {}
            diffs=[]
            for k in ("rebuffer_events","rebuffer_duration_ms","dropped_frame_pct",
                      "frame_rate_fps","video_resolution_p","watched_seconds","status"):
                o,nv=old.get(k),new.get(k)
                if o!=nv: diffs.append("%s %s->%s"%(k,o,nv))
            if diffs:
                changed=True; lines.append("    %-6s %s"%(app,"; ".join(diffs)))
            rec.setdefault("player_qoe",{})[app]=new
        tag="%s %2dM/%3dms"%("B1" if lat==50 else "B2",bw,lat)
        if changed:
            print("%s  CHANGED"%tag); [print(l) for l in lines]
            if APPLY:
                shutil.copy(rp, str(rp)+".prefix")
                json.dump(rec, open(rp,"w"), indent=2)
                print("      written (backup .prefix)")
        else:
            print("%s  unchanged"%tag)
