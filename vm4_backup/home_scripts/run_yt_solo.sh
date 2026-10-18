#!/usr/bin/env bash
# Solo YouTube baseline (60s watch) on the substrate worker (run ON the VM).
# Same conditions as the concurrent runs: 6 Mbps / 50 ms / fifo, cubic.
set -u
BASE=http://localhost:8002
NAME=youtube_solo_6_6_50_fifo
WATCH=60
CAP=75   # capture/qtrace a bit longer than the watch window

echo "=== 1. SHAPE 6Mbps/50ms/fifo ==="
curl -s -X POST $BASE/shape -H 'Content-Type: application/json' -d '{
  "downstream_iface":"veth2","upstream_iface":"veth4",
  "download_mbps":6,"upload_mbps":6,"latency_ms":50,"latency_location":"upstream",
  "qdisc":"pfifo","buffer_packets":1000,"verify":false
}' | python3 -c 'import sys,json;print("shape:",json.load(sys.stdin).get("status"))'

echo "=== 2. START capture(${CAP}s) + qtrace(5ms,${CAP}s) ==="
curl -s -X POST $BASE/capture -H 'Content-Type: application/json' \
  -d "{\"interface\":\"veth2\",\"filename\":\"$NAME\",\"duration_seconds\":$CAP}" \
  | python3 -c 'import sys,json;print("capture:",json.load(sys.stdin).get("status"))'
curl -s -X POST $BASE/qtrace -H 'Content-Type: application/json' \
  -d "{\"interfaces\":[\"veth2\",\"veth4\"],\"filename\":\"${NAME}_qtrace\",\"interval_ms\":5,\"duration_seconds\":$CAP}" \
  | python3 -c 'import sys,json;print("qtrace:",json.load(sys.stdin).get("status"))'

# --- YouTube request (watch 60s) ---
cat > /tmp/youtube_solo_req.json <<JSON
{
  "cca":"cubic","runtime":"browser","experiment_max_seconds":90,
  "parameters":{"url":"https://www.youtube.com/watch?v=1G4isv_Fylg","watch_seconds":"$WATCH"},
  "workflow":{
    "specification":"watch youtube",
    "states":[{"checks":[{"type":"always_true","params":{}}],"actions":[
      {"type":"go_to_url","params":{"url":"https://www.youtube.com/watch?v=1G4isv_Fylg"}},
      {"type":"wait","params":{"seconds":$WATCH}}
    ]}],
    "parameters":["url","watch_seconds"]
  }
}
JSON

echo "=== 3. Run YouTube (watch ${WATCH}s) at $(date +%T) ==="
curl -s -X POST $BASE/run -H 'Content-Type: application/json' -d @/tmp/youtube_solo_req.json > /tmp/youtube_solo_out.json 2>&1
echo "done at $(date +%T)"

echo "=== YOUTUBE result ==="
python3 -c 'import json;d=json.load(open("/tmp/youtube_solo_out.json"));r=d["result"][0];print("success:",r.get("success"),"| error:",r.get("error"),"| cubic:",d.get("congestion_observed"))' 2>&1 || cat /tmp/youtube_solo_out.json

echo "=== artifacts ==="
ls -lh /tmp/substrate-capture/${NAME}* 2>/dev/null
