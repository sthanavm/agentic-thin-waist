"""Re-derive the Zoom solo sweep's player_qoe with the corrected rebuffer rule.

Re-derivation reduces the per-second samples already on disk. It can therefore
only change values the DERIVATION got wrong (the counter-reset-as-freeze bug).
It cannot repair a value that was wrong at COLLECTION time - if the collector
measured an uncomposited <video>, the stored dropped-frame counts describe that
element and no amount of re-reduction recovers the real ones.
"""
import json, pathlib, sys, shutil
H_ = pathlib.Path.home()/"sthanav-agentic-thin-waist"
sys.path.insert(0, str(H_/"experiments/pramana")); sys.path.insert(0, str(H_))
import pramana_helpers as H
reg, qoelib = H._bind_shared()
RB = H_/"experiments/pramana/results/pramana_runs"
APPLY = "--apply" in sys.argv
KEYS = ("rebuffer_events","rebuffer_duration_ms","dropped_frame_pct","frame_rate_fps",
        "video_resolution_p","watched_seconds","startup_ms","status")
nchanged = 0
for d in sorted(RB.glob("zoom_*mbps_*ms_0pct_pfifo_cubic_solo_t1_*")):
    j = d/"qoe"/"zoom_stats.jsonl"; rp = d/"record.json"
    if not j.exists() or not rp.exists(): continue
    rec = json.load(open(rp))
    old = (rec.get("player_qoe") or {}).get("zoom") or {}
    new = qoelib.summarize(j, "zoom")
    diffs = [f"{k}: {old.get(k)} -> {new.get(k)}" for k in KEYS if old.get(k) != new.get(k)]
    if diffs:
        nchanged += 1
        print(f"CHANGED {d.name}")
        for x in diffs: print(f"    {x}")
        if APPLY:
            shutil.copy(rp, str(rp)+".prefix")
            rec.setdefault("player_qoe", {})["zoom"] = new
            json.dump(rec, open(rp, "w"), indent=2)
            print("    written (backup .prefix)")
    else:
        print(f"unchanged {d.name}")
print(f"\n{nchanged} run(s) changed")
