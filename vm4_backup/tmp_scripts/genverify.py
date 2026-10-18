import json, pathlib, statistics as S
base = pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
NEW = {"youtube":"youtube_10mbps_50ms_0pct_pfifo_cubic_solo_t1_e04816dcd4",
       "vimeo":"vimeo_10mbps_50ms_0pct_pfifo_cubic_solo_t1_346f63a642",
       "tubi":"tubi_10mbps_50ms_0pct_pfifo_cubic_solo_t1_6d8b119830",
       "twitch":"twitch_10mbps_50ms_0pct_pfifo_cubic_solo_t1_90cb513af6"}
print("== NEW runs (generic extraction) vs HISTORICAL runs at 10Mbps/50ms ==\n")
for app, run in NEW.items():
    d = base/run
    q = json.load(open(d/"record.json"))["player_qoe"][app]
    hist = []
    for rj in base.glob(f"{app}_10mbps_50ms_*/record.json"):
        if rj.parent.name == run: continue
        try: h = json.load(open(rj))["player_qoe"][app]
        except Exception: continue
        if h.get("status") == "ok": hist.append(h)
    def med(k):
        v = [x.get(k) for x in hist if isinstance(x.get(k),(int,float))]
        return round(S.median(v),2) if v else None
    print(f"  {app}")
    print(f"     NEW  status={q.get('status')} res={q.get('video_resolution_p')}p fps={q.get('frame_rate_fps')} "
          f"rebuf={q.get('rebuffer_events')} dropped={q.get('dropped_frame_pct')}% watched={q.get('watched_seconds')}s")
    if hist:
        print(f"     HIST(n={len(hist)}) median res={med('video_resolution_p')}p fps={med('frame_rate_fps')} "
              f"rebuf={med('rebuffer_events')} dropped={med('dropped_frame_pct')}%")
    else:
        print("     HIST  none at these conditions")
    # standardized shape + codec straight from the raw samples
    rows=[json.loads(l) for l in open(d/"qoe"/f"{app}_stats.jsonl") if l.strip()]
    rows=[r for r in rows if r.get("record")!="meta"]
    st=[r.get("stats",r) for r in rows]
    mid=st[len(st)//2]
    have=all(k in mid for k in ("video","playback","frames"))
    codecs={s.get("codec") for s in st if s.get("codec")}
    flat=[k for k in ("resolution","current_time_secs","total_video_frames","dropped_video_frames",
                      "buffer_ahead_secs","video_height","paused") if k in mid]
    print(f"     standardized blocks present: {have} | codec: {sorted(codecs) if codecs else 'none detected'}")
    print(f"     flat keys retained: {len(flat)}/7   sample video={json.dumps(mid.get('video'))}")
    print()
