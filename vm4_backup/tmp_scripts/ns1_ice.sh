set -e
NS=$(python3 - <<'PY'
import sys, pathlib
sys.path.insert(0, str(pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana"))
import pramana_helpers as H
print(H.ns1_netns_path() or "")
PY
)
echo "  ns1 netns: ${NS:-NOT FOUND}"
[ -n "$NS" ] || exit 1
docker run --rm --privileged --pid host \
  -v /proc:/hostproc:ro -v /tmp/ice_iso.py:/tmp/ice_iso.py:ro \
  --shm-size=1g --entrypoint nsenter video-qoe-collector:latest \
  --net="$NS" -- python3 /tmp/ice_iso.py 2>&1 | grep -vE "^\[|Deprecat|warnings.warn"
