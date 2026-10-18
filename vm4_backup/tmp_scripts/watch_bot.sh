#!/bin/bash
R=/home/student/sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs
for i in $(seq 1 60); do
  f=$(ls -1t $R/meet_*/bots/meet_status.json 2>/dev/null | head -1)
  if [ -n "$f" ]; then
    st=$(python3 -c "import json;print(json.load(open('$f')).get('state'))" 2>/dev/null)
    echo "  [$(date +%H:%M:%S)] bot state: $st"
    [ "$st" = "joined" ] && exit 0
    [ "$st" = "failed" ] && { cat "$f"; exit 1; }
  else
    echo "  [$(date +%H:%M:%S)] waiting for bot status file ..."
  fi
  sleep 10
done
