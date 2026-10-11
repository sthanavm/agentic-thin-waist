#!/usr/bin/env python3
"""Vimeo 10 Mbps: host/decode-limited, or just the content's ceiling?

Network was already ruled out from the summaries (delivered 3.09 Mbps against a
10 Mbps cap with the buffer at its ~24 s ceiling; a network limit needs delivery
near the cap and a draining buffer).

That leaves two explanations, and they differ in a way the per-sample data can
settle. If the player ATTEMPTED a higher rendition and then abandoned it while
DROPPING frames, the decoder could not keep up -> host-limited. If it reached
the higher rendition with no drops, or never attempted one, nothing was
limited -> the player simply chose 720p.

The test could come out either way: a run with 1440x1080 samples and zero drops
would refute the host-limit reading outright.
"""
import json
import pathlib
import sys


def samples(p):
    out = []
    for line in pathlib.Path(p).read_text(errors="replace").splitlines():
        if not line.strip() or '"meta"' in line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        st = r.get("stats")
        if st:
            st["_ts"] = r.get("timestamp")
            out.append(st)
    return out


def num(v):
    try:
        return float(v)
    except Exception:
        return None


for d in sys.argv[1:]:
    p = pathlib.Path(d) / "qoe" / "vimeo_stats.jsonl"
    if not p.exists():
        print("no samples: %s" % d)
        continue
    st = samples(p)
    print("=" * 100)
    print("%s   (%d samples)" % (pathlib.Path(d).name, len(st)))
    # group samples by the rendition that was active
    by_res = {}
    for s in st:
        r = s.get("resolution") or (
            "%sx%s" % (s.get("frame_width"), s.get("frame_height"))
        )
        by_res.setdefault(str(r), []).append(s)
    print("  rendition occupancy:")
    for r, rows in sorted(by_res.items(), key=lambda kv: -len(kv[1])):
        print("    %-12s %4d samples" % (r, len(rows)))
    # dropped frames: cumulative counter -> per-sample delta, attributed to the
    # rendition active at that sample
    drops_by_res = {}
    dec_by_res = {}
    prev_d = prev_t = None
    for s in st:
        d_ = num(s.get("dropped_video_frames"))
        t_ = num(s.get("total_video_frames"))
        r = str(
            s.get("resolution")
            or "%sx%s" % (s.get("frame_width"), s.get("frame_height"))
        )
        if d_ is not None and prev_d is not None and d_ >= prev_d:
            drops_by_res[r] = drops_by_res.get(r, 0) + (d_ - prev_d)
        if t_ is not None and prev_t is not None and t_ >= prev_t:
            dec_by_res[r] = dec_by_res.get(r, 0) + (t_ - prev_t)
        if d_ is not None:
            prev_d = d_
        if t_ is not None:
            prev_t = t_
    print("  frames decoded / dropped, per rendition (deltas, so attribution is")
    print("  to the rendition active at that moment):")
    print("    %-12s %10s %10s %9s" % ("rendition", "decoded", "dropped", "drop %"))
    for r in sorted(
        set(list(drops_by_res) + list(dec_by_res)),
        key=lambda x: -(dec_by_res.get(x, 0)),
    ):
        dec = dec_by_res.get(r, 0)
        dr = drops_by_res.get(r, 0)
        print(
            "    %-12s %10.0f %10.0f %8.2f%%"
            % (r, dec, dr, (100.0 * dr / dec) if dec else 0.0)
        )
    # presented vs decoded overall
    pres = [
        num(s.get("presented_frames")) for s in st if num(s.get("presented_frames"))
    ]
    dec = [
        num(s.get("total_video_frames")) for s in st if num(s.get("total_video_frames"))
    ]
    if pres and dec:
        print(
            "  presented/decoded at end: %.0f / %.0f = %.4f"
            % (pres[-1], dec[-1], pres[-1] / dec[-1] if dec[-1] else 0)
        )
    # buffer during the high-rendition samples specifically
    hi = [s for r, rows in by_res.items() if ("1440" in r or "1080" in r) for s in rows]
    if hi:
        bufs = [num(s.get("buffer_ahead_secs")) for s in hi]
        bufs = [b for b in bufs if b is not None]
        print(
            "  during 1080/1440-tall samples (n=%d): buffer min=%.1f mean=%.1f max=%.1f s"
            % (len(hi), min(bufs), sum(bufs) / len(bufs), max(bufs))
            if bufs
            else "  no buffer data"
        )
    else:
        print("  NO 1080/1440-tall samples in this run")
