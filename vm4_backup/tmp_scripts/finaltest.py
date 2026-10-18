import os, sys, time, json, pathlib
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana"))
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))
import pramana_helpers as H
d = pathlib.Path(sys.argv[1])
t0 = time.time()
plots = H.replot_run(d)
el = time.time() - t0
rec = json.loads((d/"record.json").read_text())
pas = (rec.get("per_app_stats") or {}).get("vimeo", {}).get("download", {})
print("FINALIZE_S %.1f" % el)
print("PLOTS %d" % len(plots))
print("PER_APP total_mb=%s avg=%s peak=%s" % (
    pas.get("total_mb"), pas.get("avg_throughput_mbps"), pas.get("peak_throughput_mbps")))
print("PNGS_ON_DISK %d" % len(list(d.glob("*.png"))))
print("deferred flag still:", rec.get("analysis_deferred"))
