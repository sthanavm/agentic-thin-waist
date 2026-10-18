import json
SRC="/home/student/sthanav-agentic-thin-waist/experiments/pramana/pramana_demo.ipynb"
DST="/home/student/sthanav-agentic-thin-waist/experiments/pramana/pramana_meet_run.ipynb"
nb=json.load(open(SRC)); C=nb["cells"]
CELL = '''# ── MEET END-TO-END: a needs_peer app driven like any other ──────────────
# The bot peer publishes a canned clip from outside the shaped namespace; the
# app under test runs inside ns1 behind the cap, exactly like youtube/vimeo.
ROOM = "https://meet.google.com/kmr-oamc-jam"

baseline_records = []
record = None
concurrent_cfg = None
concurrent_record = None

if EXECUTION_MODE == "direct":
    concurrent_cfg = ExperimentConfig(
        apps=["meet"], bandwidth_mbps=10, latency_ms=50, loss_pct=0,
        aqm="pfifo", cca="cubic", duration_s=60, controller=CONTROLLER,
        tag="meet_e2e",
        app_urls={"meet": ROOM},
        peers={"meet": {"join_url": ROOM, "name": "pramana-bot"}},
        peer_wait_s=900,
    )
    concurrent_record = run_experiment(concurrent_cfg, mode="direct")
    record = concurrent_record
    if concurrent_record:
        baseline_records.append(concurrent_record)
        pq = (concurrent_record.get("player_qoe") or {}).get("meet") or {}
        print("\\n>>> meet result:", json.dumps({
            k: pq.get(k) for k in
            ("status","reason","player_qoe_available","mean_bitrate_mbps",
             "video_resolution_p","frames_per_second","watched_seconds",
             "session_seconds","rebuffer_events")}, indent=1), flush=True)
    print("\\nmeet e2e complete", flush=True)
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
