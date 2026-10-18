import json, sys, pathlib
d = pathlib.Path(sys.argv[1])
rows = [json.loads(l) for l in open(d/"qoe"/"meet_stats.jsonl") if l.strip()]
meta = [r for r in rows if r.get("record") == "meta"]
rows = [r for r in rows if r.get("record") != "meta"]
print("meta:", meta[0] if meta else None)
print("sample keys:", sorted(rows[3].get("stats", rows[3]).keys()))
t0 = rows[0]["timestamp"]
print(f"\nsession epoch start {t0:.3f}  end {rows[-1]['timestamp']:.3f}  ({rows[-1]['timestamp']-t0:.1f}s, {len(rows)} samples)")
def st(r): return r.get("stats", r)
print("\n  t_sess  frames_dec  d_frames  bytes_recv   d_bytes  bytes_sent   d_sent   WxH")
prev = None
for r in rows:
    s = st(r); t = r["timestamp"] - t0
    fd = s.get("frames_decoded"); br = s.get("bytes_received"); bs = s.get("bytes_sent")
    if prev:
        dfd = (fd - prev[0]) if fd is not None and prev[0] is not None else None
        dbr = (br - prev[1]) if br is not None and prev[1] is not None else None
        dbs = (bs - prev[2]) if bs is not None and prev[2] is not None else None
    else:
        dfd = dbr = dbs = None
    if t >= 40:
        print("  %6.1f  %10s  %8s  %10s  %8s  %10s %8s   %sx%s" % (
            t, fd, dfd, br, dbr, bs, dbs, s.get("frame_width"), s.get("frame_height")))
    prev = (fd, br, bs)
