#!/bin/bash
# Four samplers x 3 trials, one run at a time, each behind the host-load gate.
#   A = current full extractor (control, in-page log on)
#   B = uniform hooks + CDP drain + in-page log OFF, 1 s
#   C = collector at 4ada635, before the uniform QoE hooks, 1 s
#   D = Jaber's approach: plain video + stats-for-nerds at 2 s
# Trial numbers are kept distinct per condition so nothing overwrites.
cd ~/sthanav-agentic-thin-waist/experiments/pramana || exit 1
CUR=~/sthanav-agentic-thin-waist/services/orchestration/scripts/selenium_video_qoe/collect.py
for T in 1 2 3; do
  for C in A B C D; do
    case $C in
      A) TR=$((100 + T)) ;;
      B) TR=$((110 + T)) ;;
      C) TR=$((120 + T)) ;;
      D) TR=$((130 + T)) ;;
    esac
    unset PRAMANA_INPAGE_LOG PRAMANA_MINIMAL_SAMPLER PRAMANA_SAMPLE_INTERVAL_S
    export PRAMANA_COLLECTOR_SRC="$CUR"
    case $C in
      A) export PRAMANA_INPAGE_LOG=1 ;;
      B) export PRAMANA_MINIMAL_SAMPLER=1 ;;
      C) export PRAMANA_COLLECTOR_SRC=/tmp/collect_old_4ada635.py ;;
      D) export PRAMANA_MINIMAL_SAMPLER=1; export PRAMANA_SAMPLE_INTERVAL_S=2.0 ;;
    esac
    PROBE=/tmp/cpu_probe_origin_${C}_t${T}.jsonl
    rm -f "$PROBE"
    echo "===== ORIGIN $C trial $T TRIAL=$TR start $(date -u +%FT%TZ) load1=$(cut -d' ' -f1 /proc/loadavg) ====="
    PROBE_DURATION_S=400 PROBE_INTERVAL_S=0.2 python3 cpu_probe.py "$PROBE" &
    PP=$!
    BW=6 DUR=180 TRIAL=$TR python3 run_concurrent.py 2>&1
    kill $PP 2>/dev/null
    echo "ORIGIN_PROBE $C $T $TR $PROBE"
    echo "===== ORIGIN $C trial $T done $(date -u +%FT%TZ) ====="
  done
done
echo "ALL_ORIGIN_DONE"
