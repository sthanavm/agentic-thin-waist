import json, pathlib, collections
RB = pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
ds = sorted(RB.glob("vimeo_10mbps_50ms_*noc*"), key=lambda p: p.stat().st_mtime)
if not ds:
    print("no _noc run dir found"); raise SystemExit
d = ds[-1]
print("dir:", d.name)
st = [json.loads(l).get("stats") for l in open(d/"qoe"/"vimeo_stats.jsonl")
      if l.strip() and '"meta"' not in l]
st = [s for s in st if s]
print("  codec   :", collections.Counter((s.get("codec") or "?") for s in st).most_common(3))
print("  res hist:", collections.Counter(s.get("resolution") for s in st).most_common())
last = None; sw = 0
for s in st:
    r = s.get("resolution")
    if r != last:
        print("     ->", r, "buf=%.1fs" % (s.get("buffer_ahead_secs") or -1))
        last = r; sw += 1
print("  ladder switches:", sw - 1)
rec = json.load(open(d/"record.json"))
print("  record markers: app_blocked_codecs=%s app_window_caps=%s" % (
    rec.get("app_blocked_codecs"), rec.get("app_window_caps")))
