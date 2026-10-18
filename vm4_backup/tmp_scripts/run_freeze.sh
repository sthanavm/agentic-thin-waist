#!/bin/bash
cd ~/sthanav-agentic-thin-waist/experiments/pramana
for C in solo cpuload twitch pinned; do
  echo "===== FREEZE COND $C start $(date -u +%FT%TZ) load1=$(cut -d" " -f1 /proc/loadavg) ====="
  BW=6 DUR=180 TRIAL=1 python3 run_freeze_tests.py $C 2>&1
  echo "===== FREEZE COND $C exit=$? $(date -u +%FT%TZ) ====="
done
echo "ALL_FREEZE_CONDS_DONE"
