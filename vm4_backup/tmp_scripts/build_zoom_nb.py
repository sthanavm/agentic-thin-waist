import json
SRC="/home/student/sthanav-agentic-thin-waist/experiments/pramana/pramana_demo.ipynb"
DST="/home/student/sthanav-agentic-thin-waist/experiments/pramana/pramana_zoom_run.ipynb"
nb=json.load(open(SRC)); C=nb["cells"]
CELL = '''# ── ZOOM END-TO-END: a needs_peer app driven like any other ──────────────
# Zoom is measured as an HTML5 app: its web client is a WASM stack with no
# RTCPeerConnection, but it decodes into a MediaStream-backed <video> that
# getVideoPlaybackQuality() reads. Liveness comes from frame counts, because
# Zoom's currentTime stays pinned at 0.
ROOM = "https://us04web.zoom.us/j/73093848652?pwd=bkQH53Qa0iQ2gefK20wx6b79Sn9TEX.1"

baseline_records = []
record = None
concurrent_cfg = None
concurrent_record = None

if EXECUTION_MODE == "direct":
    concurrent_cfg = ExperimentConfig(
        apps=["zoom"], bandwidth_mbps=10, latency_ms=50, loss_pct=0,
        aqm="pfifo", cca="cubic", duration_s=60, controller=CONTROLLER,
        tag="zoom_e2e",
        app_urls={"zoom": ROOM},          # /j/ is normalised to /wc/ by the registry
        peers={"zoom": {"join_url": ROOM, "name": "pramana-bot"}},
        peer_wait_s=900,
    )
    concurrent_record = run_experiment(concurrent_cfg, mode="direct")
    record = concurrent_record
    if concurrent_record:
        baseline_records.append(concurrent_record)
        pq = (concurrent_record.get("player_qoe") or {}).get("zoom") or {}
        print("\\n>>> zoom result:", json.dumps({k: pq.get(k) for k in
              ("status","reason","player_qoe_available","video_resolution_p",
               "frame_rate_fps","dropped_frame_pct","total_video_frames",
               "watched_seconds","session_seconds","rebuffer_events")}, indent=1), flush=True)
    print("\\nzoom e2e complete", flush=True)
else:
    print("needs EXECUTION_MODE == 'direct'")
'''
C[6]["source"]=CELL.splitlines(keepends=True)
for c in C:
    if c["cell_type"]=="code":
        c["outputs"]=[]; c["execution_count"]=None
        s="".join(c["source"])
        if "RUN_FULL_SWEEP = True" in s:
            c["source"]=s.replace("RUN_FULL_SWEEP = True","RUN_FULL_SWEEP = False").splitlines(keepends=True)
json.dump(nb, open(DST,"w"), indent=1, ensure_ascii=False)
import ast
for c in C:
    if c["cell_type"]=="code": ast.parse("".join(c["source"]))
print(f"wrote {DST}  all parse OK")
