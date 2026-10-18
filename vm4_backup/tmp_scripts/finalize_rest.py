import json, pathlib, sys, time
sys.path.insert(0, "/home/student/sthanav-agentic-thin-waist/experiments/pramana")
sys.path.insert(0, "/home/student/sthanav-agentic-thin-waist")
import pramana_helpers as H
RB = pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
for cell in [x["cell"] for x in json.load(open("/tmp/sweep_m1.json"))["done"]]:
    apps, regime = cell.rsplit("_", 2)[0], cell.rsplit("_", 2)[1:]
    bw = regime[0].rstrip("M"); lat = regime[1].rstrip("ms")
    pat = (f"zoom+vimeo_{bw}mbps_{lat}ms_*nocvimeoav01_*" if apps == "zoom+vimeo"
           else f"zoom_{bw}mbps_{lat}ms_0pct_pfifo_cubic_solo_t1_*")
    ds = sorted(RB.glob(pat), key=lambda p: p.stat().st_mtime)
    if not ds:
        print("MISSING", cell, pat, flush=True); continue
    d = ds[-1]
    rec = json.loads((d/"record.json").read_text())
    if not rec.get("analysis_deferred"):
        print("already final", cell, flush=True); continue
    t0 = time.time()
    try:
        plots = H.replot_run(d)
        rec = json.loads((d/"record.json").read_text())
        print("FINALIZED %-24s %4.1fs plots=%d deferred=%s" % (
            cell, time.time()-t0, len(plots), rec.get("analysis_deferred")), flush=True)
    except Exception as e:
        print("FAILED", cell, type(e).__name__, str(e)[:120], flush=True)
print("FINALIZE_DONE", flush=True)
