#!/bin/bash
# Three profiled concurrent cells, plus graded load on solo YouTube (1 and 2
# spinners). One run at a time, load gate, disk guard. Marker at the end.
cd ~/sthanav-agentic-thin-waist/experiments/pramana || exit 1
R=~/sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs
rm -f /tmp/trace_DONE
first=1
for T in 1 2 3; do
  for E in trace yt_cpu1 yt_cpu2; do
    case $E in
      trace)   TR=$((300 + T)) ;;
      yt_cpu1) TR=$((310 + T)) ;;
      yt_cpu2) TR=$((320 + T)) ;;
    esac
    echo "===== TRACE $E trial $T TRIAL=$TR start $(date -u +%FT%TZ) free=$(df -m / | tail -1 | awk '{print $4}')MB load1=$(cut -d' ' -f1 /proc/loadavg) ====="
    BW=6 DUR=180 TRIAL=$TR python3 run_freeze_why.py "$E" 2>&1
    rc=$?
    if [ $rc -eq 3 ]; then
      echo "BATCH STOPPED: disk guard before $E trial $T"
      { date -u +%FT%TZ; echo STOPPED_DISK; } > /tmp/trace_DONE; exit 3
    fi
    echo "===== TRACE $E trial $T done rc=$rc $(date -u +%FT%TZ) ====="
    if [ $first -eq 1 ]; then
      first=0
      D=$(ls -dt $R/*t${TR}_* 2>/dev/null | head -1)
      n=$(ls -1 "$D/qoe" 2>/dev/null | wc -l)
      prof=$(ls -1 "$D/qoe"/*_profile.json 2>/dev/null | wc -l)
      echo "HEALTHCHECK dir=$(basename ${D:-none}) qoe_files=$n profiles=$prof"
      if [ "$n" -lt 2 ] || [ "$prof" -lt 1 ]; then
        echo "BATCH STOPPED: first run produced no data/profile"
        { date -u +%FT%TZ; echo STOPPED_UNHEALTHY; } > /tmp/trace_DONE; exit 4
      fi
      echo "HEALTHCHECK passed; continuing"
    fi
  done
done
echo "ALL_TRACE_DONE"
{ date -u +%FT%TZ; grep -c "TRACE .* trial .* done" /tmp/trace.log; } > /tmp/trace_DONE
