#!/bin/bash
set -e
T=/tmp/freshclone2; rm -rf $T; mkdir -p $T
echo "=== 1. shallow clone of the PR branch from GitHub ==="
git clone --depth 1 --branch jenil/pramana/notebook-code-only \
  https://github.com/sthanavm/agentic-thin-waist.git $T/repo 2>&1 | tail -1
cd $T/repo
echo "  cloned HEAD: $(git rev-parse --short=12 HEAD)"

echo "=== 2. does the CLONE carry the feature? ==="
for f in services/orchestration/scripts/selenium_video_qoe/bot_peer.py \
         services/orchestration/scripts/selenium_video_qoe/make_test_clip.py; do
  echo "  $(test -f $f && echo present || echo MISSING)  $f"
done
echo "  qoe.py inbound_bitrate_mbps : $(grep -c inbound_bitrate_mbps shared/qoe.py)"
echo "  qoe.py packet_loss_percent  : $(grep -c packet_loss_percent shared/qoe.py)"
echo "  qoe.py jitter_seconds       : $(grep -c jitter_seconds shared/qoe.py)"
echo "  qoe.py startup-artifact flag: $(grep -c max_bitrate_is_startup_artifact shared/qoe.py)"
echo "  helpers start_bot_peers     : $(grep -c 'def start_bot_peers' experiments/pramana/pramana_helpers.py)"
echo "  worker pcap_removed (x3)    : $(grep -lc pcap_removed services/substrate-worker/src/substrate/main*.py | wc -l)"

echo "=== 3. build the image FROM THE CLONE (throwaway tag) ==="
cd services/orchestration/scripts/selenium_video_qoe
docker build --pull=false -t vqc-meetcheck:test . 2>&1 | tail -2
echo "  image bot_peer.py      : $(docker run --rm --entrypoint sh vqc-meetcheck:test -lc 'test -f /app/bot_peer.py && echo yes || echo NO')"
echo "  image make_test_clip.py: $(docker run --rm --entrypoint sh vqc-meetcheck:test -lc 'test -f /app/make_test_clip.py && echo yes || echo NO')"

echo "=== 4. does the clip generator actually run inside that image? ==="
docker run --rm --entrypoint python3 vqc-meetcheck:test -c "
import sys; sys.path.insert(0,'/app')
from make_test_clip import write_clip
import os, zlib
p=write_clip('/tmp/c.y4m',160,120,12)
raw=open(p,'rb').read(); print('  clip %d bytes, deflate ratio %.1fx (low = realistic entropy)' % (len(raw), len(raw)/len(zlib.compress(raw,6))))"
echo "=== 5. bot_peer imports its deps inside the image ==="
docker run --rm --entrypoint python3 vqc-meetcheck:test -c "
import ast,sys; sys.path.insert(0,'/app')
ast.parse(open('/app/bot_peer.py').read())
from collect import GOOGLE_MEET_JOIN_JS, install_webrtc_hook, ensure_window_size, start_display
from make_test_clip import write_clip
print('  bot_peer dependency chain resolves: ok')"

echo "=== 6. cleanup ==="
docker rmi vqc-meetcheck:test >/dev/null 2>&1 && echo "  throwaway image removed"
rm -rf $T && echo "  clone removed"
df -h / | tail -1 | awk '{print "  disk: "$4" free"}'
