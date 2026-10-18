import json, pathlib, sys, time
sys.path.insert(0, "/home/student/sthanav-agentic-thin-waist/experiments/pramana")
sys.path.insert(0, "/home/student/sthanav-agentic-thin-waist")
import pramana_helpers as H
RB = pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
done = [x["cell"] for x in json.load(open("/tmp/sweep_m1.json"))["done"]]
for cell in done:
    bw, lat = cell.split("M_"); lat = lat.replace("ms", "")
    ds = sorted(RB.glob(f"zoom+vimeo_{bw}mbps_{lat}ms_*nocvimeoav01_*"),
                key=lambda p: p.stat().st_mtime)
    if not ds:
        print("MISSING", cell, flush=True); continue
    d = ds[-1]
    t0 = time.time()
    try:
        plots = H.replot_run(d)
        rec = json.loads((d/"record.json").read_text())
        pas = (rec.get("per_app_stats") or {})
        print("FINALIZED %-10s %5.1fs plots=%d deferred=%s zoom_mb=%s vimeo_mb=%s" % (
            cell, time.time()-t0, len(plots), rec.get("analysis_deferred"),
            (pas.get("zoom") or {}).get("download", {}).get("total_mb"),
            (pas.get("vimeo") or {}).get("download", {}).get("total_mb")), flush=True)
    except Exception as e:
        print("FAILED", cell, type(e).__name__, str(e)[:120], flush=True)
print("FINALIZE_DONE", flush=True)
