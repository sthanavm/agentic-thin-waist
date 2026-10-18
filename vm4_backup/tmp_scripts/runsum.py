import json, pathlib
base=pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
for d in sorted(list(base.glob("meet_*"))+list(base.glob("zoom_*"))):
    rj=d/"record.json"
    if not rj.exists(): continue
    try: r=json.load(open(rj))
    except Exception: continue
    c=r.get("config",{}); app=(c.get("apps") or ["?"])[0]
    q=(r.get("player_qoe") or {}).get(app) or {}
    sh=r.get("shaping_applied") or {}
    peers=c.get("peers") or {}
    pk=list(peers.keys())
    bot=None
    bs=d/"bots"/f"{app}_status.json"
    if bs.exists():
        try: bot=json.load(open(bs))
        except Exception: pass
    print(f"### {d.name}")
    print(f"    app={app}  status={q.get('status')}  verdict={(r.get('per_app',{}).get(app,{}) or {}).get('traffic_verdict')}")
    print(f"    shaping: {c.get('bandwidth_mbps')}Mbps dl / {c.get('upload_mbps')}Mbps ul, {c.get('latency_ms')}ms, loss {c.get('loss_pct')}%, aqm {c.get('aqm')}, buf {c.get('buffer_packets')}, cca {c.get('cca')}")
    print(f"    verified: {sh.get('verified')}  measured: {'; '.join((sh.get('verification_log') or [])[:2])}")
    print(f"    duration_s={c.get('duration_s')}  concurrency={c.get('concurrency')}  trial={c.get('trial')}  tag={c.get('tag')}")
    print(f"    peers={pk}  peer_wait_s={c.get('peer_wait_s')}  bot_final={bot}")
    print(f"    url={(c.get('app_urls') or {}).get(app)}")
    print(f"    res={q.get('video_resolution_p')}p fps={q.get('frame_rate_fps')} watched={q.get('watched_seconds')} rebuf={q.get('rebuffer_events')} dropped%={q.get('dropped_frame_pct')} bitrate={q.get('mean_bitrate_mbps')}")
    print(f"    samples={q.get('total_samples')}  pcap={(d/'capture.pcap').exists()}  ts={r.get('timestamp')}")
    print()
