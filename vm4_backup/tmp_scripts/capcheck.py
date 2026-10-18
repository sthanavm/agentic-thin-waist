import json, pathlib, collections
RB = pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
ds = sorted(RB.glob("vimeo_10mbps_50ms_*vcap*"), key=lambda p: p.stat().st_mtime)
if not ds:
    print("no vcap run dir yet"); raise SystemExit
d = ds[-1]
print("dir:", d.name)
cl = d/"collector.log"
if cl.exists():
    for ln in cl.read_text(errors="replace").splitlines():
        if "viewport capped" in ln.lower() or "confirmed after" in ln.lower():
            print("   ", ln.strip())
j = d/"qoe"/"vimeo_stats.jsonl"
if not j.exists():
    print("no samples yet"); raise SystemExit
st = [json.loads(l).get("stats") for l in open(j) if l.strip() and '"meta"' not in l]
st = [s for s in st if s]
print("  samples:", len(st))
print("  elem geom:", collections.Counter((s.get("player_elem_w"), s.get("player_elem_h")) for s in st).most_common(3))
print("  window_inner:", collections.Counter((s.get("window_inner_w"), s.get("window_inner_h")) for s in st).most_common(2))
print("  res hist:", collections.Counter(s.get("resolution") for s in st).most_common())
last = None
print("  series (changes only):")
for s in st:
    r = s.get("resolution")
    if r != last:
        print("     %-10s buf=%5.1fs" % (r, s.get("buffer_ahead_secs") or -1))
        last = r
