#!/bin/bash
# Is the persistent Zoom peer actually in the call? Measured as outbound bytes
# on its only interface, inside its own netns. Its DOM self-report reads its own
# local preview and keeps saying "publishing" long after a meeting has ended, so
# the byte counters are the only honest witness.
PID=$(docker inspect -f '{{.State.Pid}}' pramana-peer-zoom 2>/dev/null)
if [ -z "$PID" ]; then
  echo "NO PEER CONTAINER"
  exit 1
fi
A=$(sudo nsenter -t "$PID" -n cat /proc/net/dev | awk '/eth0/ {print $2, $10}')
sleep 20
B=$(sudo nsenter -t "$PID" -n cat /proc/net/dev | awk '/eth0/ {print $2, $10}')
python3 - "$A" "$B" <<'PY'
import sys

a = sys.argv[1].split()
b = sys.argv[2].split()
drx = (int(b[0]) - int(a[0])) / 20.0
dtx = (int(b[1]) - int(a[1])) / 20.0
print("  rx %.1f kB/s  tx %.1f kB/s over 20s" % (drx / 1000, dtx / 1000))
if dtx > 20000:
    print("  VERDICT: IN CALL (non-zero outbound)")
else:
    print("  VERDICT: NOT IN CALL - outbound only %.1f kB/s" % (dtx / 1000))
PY
