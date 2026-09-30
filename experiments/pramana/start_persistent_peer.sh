#!/usr/bin/env bash
# Launch a peer that stays in a room across many experiments.
#
# A per-run bot must be launched and (on Meet) manually admitted for every
# single experiment; that admission is where most Meet runs were lost. This
# joins once, loops a real video file as its camera, and keeps publishing until
# it is explicitly stopped, so later runs join a room that already has media.
#
#   ./start_persistent_peer.sh meet "https://meet.google.com/xxx-yyyy-zzz"
#   docker stop pramana-peer-meet      # when you are done with the room
set -euo pipefail

APP="${1:?usage: start_persistent_peer.sh <app> <join_url> [clip]}"
ROOM="${2:?usage: start_persistent_peer.sh <app> <join_url> [clip]}"
CLIP="${3:-$HOME/pramana-assets/bbb_360p24_15s.y4m}"
NAME="pramana-peer-${APP}"
OUT="$HOME/pramana-assets/peer-${APP}"
IMAGE="${PRAMANA_COLLECTOR_IMAGE:-video-qoe-collector:latest}"

[ -s "$CLIP" ] || { echo "clip not found: $CLIP" >&2; exit 1; }

# Normalise the join URL exactly as the pipeline does. A Zoom /j/ invite lands
# on a launcher page whose "Join from browser" click is silently swallowed, so
# a peer pointed at one sits there forever reporting "knocking" while nobody
# sees it. start_bot_peers() applies this; a hand launch must too.
ROOM="$(python3 - "$APP" "$ROOM" <<'PYNORM'
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
try:
    from shared import apps as A
    print(A.normalize_join_url(sys.argv[1], sys.argv[2]))
except Exception:
    print(sys.argv[2])
PYNORM
)"
mkdir -p "$OUT"

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped \
  --shm-size=2g \
  -v "$CLIP:/clip.y4m:ro" \
  -v "$OUT:/out" \
  --env "BOT_ROOM_URL=$ROOM" \
  --env "BOT_NAME=pramana-peer" \
  --env "BOT_APP=$APP" \
  --env "BOT_CLIP=/clip.y4m" \
  --env "BOT_STATUS=/out/${APP}_status.json" \
  --env "BOT_HOLD_SECONDS=86400" \
  --entrypoint python3 "$IMAGE" /app/bot_peer.py >/dev/null

echo "launched $NAME"
echo "  room : $ROOM"
echo "  clip : $CLIP  ($(du -h "$CLIP" | cut -f1), looped by Chrome)"
echo "  state: $OUT/${APP}_status.json"
echo
echo "Admit '\''pramana-peer'\'' in the meeting UI once; it then stays for every later run."
