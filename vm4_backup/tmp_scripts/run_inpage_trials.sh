#!/bin/bash
# Three 6 Mbps concurrent cells with the in-page sampler enabled, one at a
# time, each behind the same host-load gate. Existing trials are untouched.
cd ~/sthanav-agentic-thin-waist/experiments/pramana
export PRAMANA_INPAGE_LOG=1
for T in 5 6 7; do
  echo "===== INPAGE TRIAL $T starting $(date -u +%FT%TZ) load1=$(cut -d" " -f1 /proc/loadavg) ====="
  BW=6 DUR=180 TRIAL=$T python3 run_concurrent.py 2>&1
  echo "===== INPAGE TRIAL $T exit=$? $(date -u +%FT%TZ) ====="
done
echo "ALL_INPAGE_TRIALS_DONE"
