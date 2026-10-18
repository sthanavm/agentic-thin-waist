#!/usr/bin/env bash
# Concurrent YouTube + Zoom experiment on the substrate worker (run ON the VM).
set -u
BASE=http://localhost:8002
NAME=youtube_zoom_concurrent_6_6_50_fifo
MEETING_ID="79176586474"
PASSCODE="CK2aXv"
DISPLAY_NAME="Pramana"

echo "=== 1. SHAPE 6Mbps/50ms/fifo ==="
curl -s -X POST $BASE/shape -H 'Content-Type: application/json' -d '{
  "downstream_iface":"veth2","upstream_iface":"veth4",
  "download_mbps":6,"upload_mbps":6,"latency_ms":50,"latency_location":"upstream",
  "qdisc":"pfifo","buffer_packets":1000,"verify":false
}' | python3 -c 'import sys,json;print("shape:",json.load(sys.stdin).get("status"))'

echo "=== 2. START capture(90s) + qtrace(5ms,90s) ==="
curl -s -X POST $BASE/capture -H 'Content-Type: application/json' \
  -d "{\"interface\":\"veth2\",\"filename\":\"$NAME\",\"duration_seconds\":90}" \
  | python3 -c 'import sys,json;print("capture:",json.load(sys.stdin).get("status"))'
curl -s -X POST $BASE/qtrace -H 'Content-Type: application/json' \
  -d "{\"interfaces\":[\"veth2\",\"veth4\"],\"filename\":\"${NAME}_qtrace\",\"interval_ms\":5,\"duration_seconds\":90}" \
  | python3 -c 'import sys,json;print("qtrace:",json.load(sys.stdin).get("status"))'

# --- Zoom request (full join workflow) ---
cat > /tmp/zoom_req.json <<JSON
{
  "cca":"cubic","runtime":"browser","experiment_max_seconds":80,
  "parameters":{"meeting_id":"$MEETING_ID","passcode":"$PASSCODE","display_name":"$DISPLAY_NAME","wait_seconds":"45"},
  "workflow":{
    "specification":"Join a zoom meeting and wait.",
    "states":[{"checks":[{"type":"always_true","params":{}}],"actions":[
      {"type":"go_to_url","params":{"url":"https://app.zoom.us/wc","new_tab":false}},
      {"type":"click_element","params":{"selector":"button.MuiButtonBase-root.MuiButton-root[aria-label=\"Join Meeting\"][type=\"button\"]"}},
      {"type":"input_text","params":{"selector":"input.join-meetingId[placeholder=\"Meeting ID or Personal Link Name\"][type=\"text\"]","text":"{{meeting_id}}"}},
      {"type":"send_keys","params":{"keys":"Enter"}},
      {"type":"wait","params":{"seconds":"12"}},
      {"type":"input_text","params":{"selector":"iframe#webclient >>> input#input-for-pwd","text":"{{passcode}}"}},
      {"type":"input_text","params":{"selector":"iframe#webclient >>> input#input-for-name","text":"{{display_name}}"}},
      {"type":"click_element","params":{"selector":"iframe#webclient >>> button.preview-join-button"}},
      {"type":"wait","params":{"seconds":"{{wait_seconds}}"}}
    ],"end_state":"done","executed":[]}],
    "parameters":["meeting_id","passcode","display_name","wait_seconds"]
  }
}
JSON

# --- YouTube request (watch while zoom call is active) ---
cat > /tmp/youtube_req.json <<'JSON'
{
  "cca":"cubic","runtime":"browser","experiment_max_seconds":80,
  "parameters":{"url":"https://www.youtube.com/watch?v=1G4isv_Fylg","watch_seconds":"50"},
  "workflow":{
    "specification":"watch youtube",
    "states":[{"checks":[{"type":"always_true","params":{}}],"actions":[
      {"type":"go_to_url","params":{"url":"https://www.youtube.com/watch?v=1G4isv_Fylg"}},
      {"type":"wait","params":{"seconds":50}}
    ]}],
    "parameters":["url","watch_seconds"]
  }
}
JSON

echo "=== 3. Launch Zoom + YouTube concurrently at $(date +%T) ==="
curl -s -X POST $BASE/run -H 'Content-Type: application/json' -d @/tmp/zoom_req.json > /tmp/zoom_out.json 2>&1 &
ZPID=$!
curl -s -X POST $BASE/run -H 'Content-Type: application/json' -d @/tmp/youtube_req.json > /tmp/youtube_out.json 2>&1 &
YPID=$!
echo "waiting (zoom=$ZPID youtube=$YPID)..."
wait $ZPID $YPID
echo "both done at $(date +%T)"

echo "=== YOUTUBE result ==="
python3 -c 'import json;d=json.load(open("/tmp/youtube_out.json"));r=d["result"][0];print("success:",r.get("success"),"| error:",r.get("error"),"| cubic:",d.get("congestion_observed"))' 2>&1 || cat /tmp/youtube_out.json
echo "=== ZOOM result ==="
python3 -c 'import json;d=json.load(open("/tmp/zoom_out.json"));r=d["result"][0];print("success:",r.get("success"),"| error:",r.get("error"),"| cubic:",d.get("congestion_observed"))' 2>&1 || cat /tmp/zoom_out.json

echo "=== artifacts ==="
ls -lh /tmp/substrate-capture/${NAME}* 2>/dev/null
