#!/bin/bash
# Four experiments x 3 trials, one at a time, behind the host-load gate.
# Stops the whole batch if a run aborts for disk space (exit 3), and refuses
# to continue past the first run unless that run actually produced data --
# the previous attempt "completed" 4 runs that were all empty.
cd ~/sthanav-agentic-thin-waist/experiments/pramana || exit 1
R=~/sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs
rm -f /tmp/why_DONE
first=1
for T in 1 2 3; do
  for E in yt_first vm_first vimeo_cpu yt_480p; do
    case $E in
      yt_first)  TR=$((200 + T)) ;;
      vm_first)  TR=$((210 + T)) ;;
      vimeo_cpu) TR=$((220 + T)) ;;
      yt_480p)   TR=$((230 + T)) ;;
    esac
    echo "===== WHY $E trial $T TRIAL=$TR start $(date -u +%FT%TZ) free=$(df -m / | tail -1 | awk '{print $4}')MB load1=$(cut -d' ' -f1 /proc/loadavg) ====="
    BW=6 DUR=180 TRIAL=$TR python3 run_freeze_why.py "$E" 2>&1
    rc=$?
    if [ $rc -eq 3 ]; then
      echo "BATCH STOPPED: free-space guard tripped before $E trial $T"
      { date -u +%FT%TZ; echo "STOPPED_DISK"; } > /tmp/why_DONE
      exit 3
    fi
    echo "===== WHY $E trial $T done rc=$rc $(date -u +%FT%TZ) ====="

    if [ $first -eq 1 ]; then
      first=0
      D=$(ls -dt $R/*t${TR}_* 2>/dev/null | head -1)
      n=$(ls -1 "$D/qoe" 2>/dev/null | wc -l)
      nulls=$(grep -c '"rendered_fps": null' /tmp/why.log 2>/dev/null)
      echo "HEALTHCHECK dir=$(basename ${D:-none}) qoe_files=$n null_fps_rows=$nulls"
      if [ "$n" -lt 2 ]; then
        echo "BATCH STOPPED: first run produced no data (qoe/ has $n files)"
        { date -u +%FT%TZ; echo "STOPPED_UNHEALTHY"; } > /tmp/why_DONE
        exit 4
      fi
      echo "HEALTHCHECK passed; continuing"
    fi
  done
done
echo "ALL_WHY_DONE"
{ date -u +%FT%TZ; grep -c "WHY .* trial .* done" /tmp/why.log; } > /tmp/why_DONE
