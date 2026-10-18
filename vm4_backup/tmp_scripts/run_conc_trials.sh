#!/bin/bash
# Three repeats of the 6 Mbps concurrent YouTube+Vimeo cell, one at a time.
# Each waits on the same host-load gate. Trial 1 already exists and is kept.
cd ~/sthanav-agentic-thin-waist/experiments/pramana
for T in 2 3 4; do
  echo "===== CONCURRENT TRIAL $T starting $(date -u +%FT%TZ) load1=$(cut -d" " -f1 /proc/loadavg) ====="
  BW=6 DUR=180 TRIAL=$T python3 run_concurrent.py 2>&1
  echo "===== CONCURRENT TRIAL $T exit=$? $(date -u +%FT%TZ) ====="
done
echo "ALL_CONCURRENT_TRIALS_DONE"
